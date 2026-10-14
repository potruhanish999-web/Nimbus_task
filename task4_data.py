import json

import numpy as np
import pandas as pd
import psycopg2
import statsmodels.api as sm
from pymongo import MongoClient
from statsmodels.stats.multitest import multipletests

from task3 import df, events

pg = psycopg2.connect("dbname=nimbus_core user=postgres host=localhost")
invoices = pd.read_sql("SELECT customer_id, total_usd FROM nimbus.billing_invoices WHERE status = 'paid'", pg)
mongo = MongoClient("mongodb://localhost:27017")["nimbus_events"]
onboarding = pd.DataFrame(list(mongo.onboarding_events.find({}, {"_id": 0})))

tiers = ["free", "starter", "professional", "enterprise"]
df = df.copy()
df["paid"] = df["customer_id"].map(invoices.groupby("customer_id")["total_usd"].sum()).fillna(0)
df["quartile"] = pd.qcut(df["events_per_month"].rank(method="first"), 4, labels=False)
df["tenure_band"] = pd.cut(df["tenure_days"], [0, 180, 365, 10**6], labels=[0, 1, 2], right=False).astype(int)
df["at_risk"] = ((df["churned"] == 0) & (df["low_engagement"] == 1)).astype(int)

profile = df.groupby("segment").agg(mrr=("mrr_usd", "median"), ev=("events_per_month", "median"))
free_segment = profile["mrr"].idxmin()
paying = profile.drop(free_segment)
names = {free_segment: "Engaged, free plan",
         paying["ev"].idxmin(): "Paying, under-engaged",
         paying["ev"].idxmax(): "Paying, engaged"}
segments = sorted(names.values())
reasons = sorted(df["churn_reason"].dropna().unique())

rows = []
for _, r in df.iterrows():
    rows.append([
        r["company_name"], tiers.index(r["plan_tier"]), segments.index(names[r["segment"]]),
        int(r["signup_date"].year), int(r["tenure_band"]), int(r["churned"]), int(r["quartile"]),
        round(float(r["mrr_usd"] or 0), 2),
        reasons.index(r["churn_reason"]) if isinstance(r["churn_reason"], str) else -1,
        int(r["at_risk"]), round(float(r["paid"]), 0), round(float(r["events_per_month"]), 2),
    ])

months = pd.date_range("2023-01-01", "2025-10-01", freq="MS")
monthly = {}
for t in tiers:
    g = df[df["plan_tier"] == t]
    opening, churned = [], []
    for m in months:
        nxt = m + pd.offsets.MonthBegin(1)
        opening.append(int(((g["signup_date"] < m) & (g["churned_at"].isna() | (g["churned_at"] >= m))).sum()))
        churned.append(int(((g["churned_at"] >= m) & (g["churned_at"] < nxt)).sum()))
    monthly[t] = {"months": [m.strftime("%Y-%m") for m in months], "opening": opening, "churned": churned}

used = events.dropna(subset=["feature"]).groupby(["customer_id", "feature"]).size().unstack(fill_value=0) > 0
features = []
for f in used.columns:
    flag = df["customer_id"].map(used[f]).fillna(False).astype(int)
    X = sm.add_constant(pd.DataFrame({"used": flag, "log_tenure": np.log(df["tenure_days"])}))
    p = sm.Logit(df["churned"], X).fit(disp=0).pvalues["used"]
    features.append({"feature": f, "adopters": int(flag.sum()),
                     "churn_if_used": float(df.loc[flag == 1, "churned"].mean()),
                     "churn_if_not": float(df.loc[flag == 0, "churned"].mean()), "p": float(p)})
p_adjusted = multipletests([x["p"] for x in features], method="fdr_bh")[1]
for x, p in zip(features, p_adjusted):
    x["p_fdr"] = float(p)

onboarding["customer"] = pd.to_numeric(onboarding["customer_id"].fillna(onboarding["customerId"]), errors="coerce")
onboarding = onboarding[onboarding["customer"].isin(df["customer_id"])]
steps = ["signup", "first_login", "workspace_created", "first_project", "invited_teammate"]
counts = [int(onboarding.loc[onboarding["step"] == s, "customer"].nunique()) for s in steps]
funnel = [{"step": s, "customers_reached": n, "step_conversion": 0 if i == 0 else n / counts[i - 1]}
          for i, (s, n) in enumerate(zip(steps, counts))]

data = {"tiers": tiers, "segments": segments, "reasons": reasons, "rows": rows,
        "monthly": monthly, "features": features, "funnel": funnel}
with open("data.js", "w", encoding="utf-8") as out:
    out.write("const D = " + json.dumps(data, separators=(",", ":")) + ";")
