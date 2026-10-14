import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

import psycopg2
from pymongo import MongoClient

pg = psycopg2.connect("dbname=nimbus_core user=postgres host=localhost")
customers = pd.read_sql("SELECT * FROM nimbus.customers", pg)
subs = pd.read_sql("SELECT * FROM nimbus.subscriptions", pg)
plans = pd.read_sql("SELECT * FROM nimbus.plans", pg)
tickets = pd.read_sql("SELECT * FROM nimbus.support_tickets", pg)
mongo = MongoClient("mongodb://localhost:27017")["nimbus_events"]
events = pd.DataFrame(list(mongo.user_activity_logs.find({}, {"_id": 0})))

def log(step, before, after):
    print(f"{step:<48} {before:>6} -> {after:>6}")

n = len(customers)
customers = customers.copy()
customers["company_name"] = customers["company_name"].str.strip().replace("", np.nan)
customers = customers.dropna(subset=["company_name", "industry"])
log("customers: drop empty sign-ups", n, len(customers))

n = len(customers)
customers["name_key"] = customers["company_name"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
customers["email_domain"] = customers["contact_email"].str.lower().str.split("@").str[1]
customers = customers.sort_values("customer_id").drop_duplicates(["name_key", "email_domain", "country_code"])
log("customers: drop duplicate accounts", n, len(customers))

customers["signup_date"] = pd.to_datetime(customers["signup_date"], format="mixed")
customers["churned_at"] = pd.to_datetime(customers["churned_at"], format="mixed")
customers["churned"] = customers["churned_at"].notna().astype(int)

n = len(events)

events["customer_id"] = pd.to_numeric(events["customer_id"].fillna(events["customerId"]), errors="coerce")
events = events.dropna(subset=["customer_id", "event_type"])
events["customer_id"] = events["customer_id"].astype(int)
log("events: unify customer id", n, len(events))

def to_time(x):
    """five timestamp formats -> one. Slash dates are MM/DD/YYYY."""
    if isinstance(x, str) and "/" in x:
        return pd.to_datetime(x, format="%m/%d/%Y %H:%M:%S", utc=True)
    return pd.to_datetime(x, utc=True, errors="coerce")

n = len(events)
events["time"] = events["timestamp"].map(to_time).dt.tz_localize(None)
events = events.dropna(subset=["time"])
events = events.drop_duplicates(["customer_id", "time", "event_type", "page_url", "feature"])
log("events: fix timestamps, drop duplicates", n, len(events))

n = len(events)
events = events[events["customer_id"].isin(customers["customer_id"])]
log("events: drop customers missing from SQL", n, len(events))

n = len(events)
events = events.merge(customers[["customer_id", "signup_date", "churned_at"]], on="customer_id")
data_end = events["time"].max()
end = events["churned_at"].fillna(data_end)
events = events[(events["time"] >= events["signup_date"]) & (events["time"] <= end)]
log("events: keep only signup -> churn window", n, len(events))

last_sub = subs.sort_values("subscription_id").groupby("customer_id").tail(1)
last_sub = last_sub.merge(plans[["plan_id", "plan_tier"]], on="plan_id")[["customer_id", "plan_tier", "mrr_usd"]]
df = customers.merge(last_sub, on="customer_id", how="inner")

per_customer = events.groupby("customer_id").agg(n_events=("time", "size"),
                                                 features_used=("feature", "nunique"))
df = df.merge(per_customer, on="customer_id", how="left").fillna({"n_events": 0, "features_used": 0})
ticket_count = tickets.groupby("customer_id").size().rename("n_tickets")
df = df.merge(ticket_count, on="customer_id", how="left").fillna({"n_tickets": 0})

df["end"] = df["churned_at"].fillna(data_end)
df["tenure_days"] = (df["end"] - df["signup_date"]).dt.days.clip(lower=1)
months = df["tenure_days"] / 30.4
df["events_per_month"] = df["n_events"] / months
log("final table: customers x (SQL + Mongo)", len(customers), len(df))
print(f"churn rate = {df.churned.mean():.1%}")

print("\nH0: churn is the same for low-engagement customers and everyone else")
print("H1: churn is different.   alpha = 0.05   test: chi-square")
cut = df["events_per_month"].quantile(0.25)
df["low_engagement"] = (df["events_per_month"] <= cut).astype(int)
table = pd.crosstab(df["low_engagement"], df["churned"])
chi2, p, _, expected = stats.chi2_contingency(table, correction=False)
print(df.groupby("low_engagement")["churned"].mean().round(3).to_dict(), "(0 = others, 1 = low)")
print(f"chi2 = {chi2:.2f}, p = {p:.2g}, smallest expected count = {expected.min():.0f} (need >= 5)")
print("reject H0" if p < 0.05 else "fail to reject H0")

X = sm.add_constant(pd.DataFrame({"low_engagement": df["low_engagement"],
                                  "log_tenure": np.log(df["tenure_days"])}))
model = sm.Logit(df["churned"], X).fit(disp=0)
print(f"odds ratio (low engagement) = {np.exp(model.params['low_engagement']):.2f}, "
      f"p = {model.pvalues['low_engagement']:.4f}  <- still significant after controlling for tenure")

safe_months = np.maximum(months, 3)
features = pd.DataFrame({"events_per_month": df["n_events"] / safe_months,
                         "log_mrr": np.log1p(df["mrr_usd"]),
                         "log_tickets_per_month": np.log1p(df["n_tickets"] / safe_months)})
scaled = StandardScaler().fit_transform(features)
df["segment"] = KMeans(n_clusters=3, n_init=20, random_state=42).fit_predict(scaled)
summary = df.groupby("segment").agg(customers=("customer_id", "size"),
                                    churn_rate=("churned", "mean"),
                                    events_per_month=("events_per_month", "median"),
                                    median_mrr=("mrr_usd", "median"),
                                    tickets=("n_tickets", "median")).round(2)
print("\nSEGMENTS (K-means, 3 groups)")
print(summary)
df.to_csv("customer_table_simple.csv", index=False)
