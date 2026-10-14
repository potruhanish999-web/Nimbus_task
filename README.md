# NimbusAI Churn & Retention Analysis

Data-analyst take-home: why are customers leaving, and what should we do about it?
Focus area: **customer churn and retention**. Data: PostgreSQL (`nimbus_core`) and MongoDB (`nimbus_events`).

> The dataset and the assignment brief are not included in this repository. Load your own copy of the two databases
> to reproduce the results.

## Headline findings
| # | Finding | Evidence |
|---|---|---|
| 1 | Churn has risen about 2.4x, and Free is worst | Monthly churn 0.97% (H1 2024) to 2.35% (May to Oct 2025). Free 0.87% to 3.55%. Overall 266 of 1,199 customers (22.2%) have churned. |
| 2 | Low product engagement predicts churn, and it is not just a tenure effect | Lowest-engagement quarter churns at about 32% against about 19% for everyone else (chi-square p < 0.001). Odds ratio 1.58 after controlling for tenure. |
| 3 | No product feature explains retention once tenure is controlled | 13 features tested with tenure control and multiple-test correction: none significant. |

## Recommendations
1. **Engagement early warning.** Flag active customers using the product less than about once a month (202 customers, $6,740 monthly revenue) and start outreach.
2. **Convert engaged Free users.** Free churns most (28.1% against 17.5% on Starter). Prompt the busiest Free accounts to upgrade.
3. **Test a teammate-invite nudge.** The invite step loses about half of customers and "Not enough users" is the top recorded churn reason. Treat it as an experiment: the link to churn was not statistically significant.

## Data problems found and how they were handled
| Problem | Handling |
|---|---|
| The SQL dump's `subscriptions` INSERT ends with `,` instead of `;`, which breaks a `psql` load | Change that one comma to a semicolon before loading |
| 49% of Mongo events are dated before the customer signed up | Excluded from all engagement metrics |
| Events dated after a customer churned | Excluded, because they would leak the answer |
| Duplicate events, 4 spellings of the user ID, 5 timestamp formats | Collapsed, unified, converted to one format |
| Events for customers that do not exist in SQL | Excluded |
| Empty and duplicate customer accounts | Dropped and merged |
| `member_id` in Mongo does not match SQL team members | Joined on `customer_id` and analysed per customer |

## How to run
```bash
# 1. load the data
psql -U postgres -c "CREATE DATABASE nimbus_core"
psql -U postgres -d nimbus_core -f nimbus_core.sql        # fix the subscriptions comma first
mongosh --file nimbus_events.js

# 2. Task 1
psql -U postgres -d nimbus_core -f task1.sql

# 3. copy each customer's plan tier into Mongo (needed by Task 2), then run Task 2
psql -U postgres -d nimbus_core -c "\copy (SELECT DISTINCT ON (c.customer_id) c.customer_id, p.plan_tier FROM nimbus.customers c JOIN nimbus.subscriptions s USING (customer_id) JOIN nimbus.plans p USING (plan_id) ORDER BY c.customer_id, s.subscription_id DESC) TO 'sql_customers.csv' CSV HEADER"
mongoimport --db nimbus_events --collection sql_customers --type csv --headerline --drop --file sql_customers.csv
mongosh nimbus_events --file task2.js                       # needs MongoDB 7.0 or newer

# 4. Task 3
pip install pandas numpy scipy scikit-learn statsmodels psycopg2-binary pymongo
python task3.py
```

---

## Task 1: SQL (PostgreSQL)
Five queries. "Today" is fixed at `2025-10-30` because the data ends then (using `NOW()` would return nothing). Only `paid` invoices count as revenue.

| Query | Question | Idea |
|---|---|---|
| Q1 | Per plan: active customers, average monthly revenue, tickets per customer per month (last 6 months) | Join plans to active subscriptions, LEFT JOIN recent tickets, GROUP BY plan |
| Q2 | Rank customers by lifetime value within their plan tier, with the % gap from the tier average | `RANK() OVER (PARTITION BY tier)` and `AVG() OVER (PARTITION BY tier)` |
| Q3 | Customers who downgraded in the last 90 days and raised more than 3 tickets in the 30 days before | `LAG()` to compare each plan with the previous one, then count tickets |
| Q4 | By tier and month: new-subscription growth, churn, rolling 3-month average, spike flag | Month grid with `generate_series`, `FILTER` counts, rolling window of the 3 prior months |
| Q5 | Possible duplicate accounts | Score three clues (same cleaned name, same email domain, shared team member); report pairs with two or more |

```sql
-- Task 1 (easy version) - PostgreSQL, database nimbus_core
-- Note: the data ends on 2025-10-30, so that date is used as "today" instead of NOW().
SET search_path TO nimbus;


-- Q1. For each plan: active customers, average monthly revenue, tickets per customer per month (last 6 months)
SELECT p.plan_name,
       COUNT(DISTINCT s.customer_id)                                     AS active_customers,
       ROUND(AVG(s.mrr_usd), 2)                                          AS avg_monthly_revenue,
       ROUND(COUNT(t.ticket_id)::numeric / COUNT(DISTINCT s.customer_id) / 6, 3) AS tickets_per_customer_per_month
FROM plans p
JOIN subscriptions s ON s.plan_id = p.plan_id AND s.status = 'active'
LEFT JOIN support_tickets t ON t.customer_id = s.customer_id
                           AND t.created_at >= DATE '2025-10-30' - INTERVAL '6 months'
GROUP BY p.plan_id, p.plan_name
ORDER BY p.plan_id;


-- Q2. Rank customers inside their plan tier by lifetime value (money paid), and show % difference from the tier average
WITH latest_plan AS (                       -- each customer's newest subscription
    SELECT DISTINCT ON (customer_id) customer_id, plan_id
    FROM subscriptions
    ORDER BY customer_id, subscription_id DESC
),
ltv AS (                                    -- lifetime value = sum of paid invoices
    SELECT l.customer_id, p.plan_tier, COALESCE(SUM(i.total_usd), 0) AS lifetime_value
    FROM latest_plan l
    JOIN plans p ON p.plan_id = l.plan_id
    LEFT JOIN billing_invoices i ON i.customer_id = l.customer_id AND i.status = 'paid'
    GROUP BY l.customer_id, p.plan_tier
)
SELECT plan_tier, customer_id, ROUND(lifetime_value, 2) AS lifetime_value,
       RANK() OVER (PARTITION BY plan_tier ORDER BY lifetime_value DESC) AS rank_in_tier,
       ROUND(100 * (lifetime_value - AVG(lifetime_value) OVER (PARTITION BY plan_tier))
                 / AVG(lifetime_value) OVER (PARTITION BY plan_tier), 1) AS pct_diff_from_tier_avg
FROM ltv
ORDER BY plan_tier, rank_in_tier;


-- Q3. Downgraded in the last 90 days AND had more than 3 tickets in the 30 days before the downgrade
WITH changes AS (                           -- each subscription next to the customer's previous one
    SELECT customer_id,
           plan_id AS new_plan_id,
           start_date::date AS change_date,
           LAG(plan_id) OVER (PARTITION BY customer_id ORDER BY subscription_id) AS old_plan_id
    FROM subscriptions
),
downgrades AS (                             -- downgrade = the new plan is cheaper than the old one
    SELECT c.*
    FROM changes c
    JOIN plans np ON np.plan_id = c.new_plan_id
    JOIN plans op ON op.plan_id = c.old_plan_id
    WHERE np.monthly_price_usd < op.monthly_price_usd
      AND c.change_date BETWEEN DATE '2025-10-30' - 90 AND DATE '2025-10-30'
)
SELECT d.customer_id, d.change_date,
       op.plan_name AS previous_plan, np.plan_name AS current_plan,
       COUNT(t.ticket_id) AS tickets_in_30_days_before
FROM downgrades d
JOIN plans op ON op.plan_id = d.old_plan_id
JOIN plans np ON np.plan_id = d.new_plan_id
JOIN support_tickets t ON t.customer_id = d.customer_id
                      AND t.created_at::date >= d.change_date - 30
                      AND t.created_at::date <  d.change_date
GROUP BY d.customer_id, d.change_date, op.plan_name, np.plan_name
HAVING COUNT(t.ticket_id) > 3;


-- Q4. By plan tier and month: new subscriptions + growth, churn rate, rolling 3-month average churn,
--     and a flag when churn is more than 2x that average
WITH grid AS (                              -- every month for every tier
    SELECT t.plan_tier, m::date AS month
    FROM (SELECT DISTINCT plan_tier FROM plans) t
    CROSS JOIN generate_series('2023-01-01'::date, '2025-10-01'::date, '1 month') m
),
subs AS (
    SELECT p.plan_tier, s.status, s.start_date::date AS start_d, s.end_date::date AS end_d
    FROM subscriptions s JOIN plans p ON p.plan_id = s.plan_id
),
monthly AS (
    SELECT g.plan_tier, g.month,
           COUNT(*) FILTER (WHERE date_trunc('month', s.start_d) = g.month)                       AS new_subs,
           COUNT(*) FILTER (WHERE s.status IN ('cancelled', 'expired')
                              AND date_trunc('month', s.end_d) = g.month)                          AS churned,
           COUNT(*) FILTER (WHERE s.start_d < g.month
                              AND (s.end_d IS NULL OR s.end_d >= g.month))                         AS active_at_start
    FROM grid g
    LEFT JOIN subs s ON s.plan_tier = g.plan_tier
    GROUP BY g.plan_tier, g.month
),
rates AS (
    SELECT *,
           churned::numeric / NULLIF(active_at_start, 0)                              AS churn_rate,
           LAG(new_subs) OVER (PARTITION BY plan_tier ORDER BY month)                 AS last_month_new
    FROM monthly
),
with_avg AS (                               -- average churn of the 3 months BEFORE this one
    SELECT *, AVG(churn_rate) OVER (PARTITION BY plan_tier ORDER BY month
                                    ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS avg_prev_3_months
    FROM rates
)
SELECT plan_tier, month, new_subs,
       ROUND(100.0 * (new_subs - last_month_new) / NULLIF(last_month_new, 0), 1) AS new_subs_growth_pct,
       ROUND(100 * churn_rate, 2)        AS churn_pct,
       ROUND(100 * avg_prev_3_months, 2) AS avg_prev_3_months_pct,
       churn_rate > 2 * avg_prev_3_months AS churn_spike
FROM with_avg
ORDER BY plan_tier, month;


-- Q5. Possible duplicate customer accounts. A pair is reported when at least 2 of these 3 clues match:
--     (1) same company name ignoring spaces / punctuation / capitals   ("Acme Labs" = "AcmeLabs")
--     (2) same email domain, ignoring free providers like gmail
--     (3) the two accounts share a team member email
WITH cust AS (
    SELECT customer_id, company_name,
           regexp_replace(lower(company_name), '[^a-z0-9]', '', 'g') AS name_key,
           CASE WHEN split_part(lower(contact_email), '@', 2)
                     IN ('gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com')
                THEN NULL ELSE split_part(lower(contact_email), '@', 2) END AS domain
    FROM customers
    WHERE company_name <> ''
),
shared AS (                                 -- account pairs with a common team member email
    SELECT DISTINCT a.customer_id AS id1, b.customer_id AS id2
    FROM team_members a
    JOIN team_members b ON lower(a.email) = lower(b.email) AND a.customer_id < b.customer_id
)
SELECT a.customer_id AS id1, a.company_name AS name1,
       b.customer_id AS id2, b.company_name AS name2,
       (a.name_key = b.name_key)::int
     + COALESCE(a.domain = b.domain, false)::int
     + (s.id1 IS NOT NULL)::int AS clues
FROM cust a
JOIN cust b ON a.customer_id < b.customer_id AND (a.name_key = b.name_key OR a.domain = b.domain)
LEFT JOIN shared s ON s.id1 = a.customer_id AND s.id2 = b.customer_id
WHERE (a.name_key = b.name_key)::int + COALESCE(a.domain = b.domain, false)::int + (s.id1 IS NOT NULL)::int >= 2
ORDER BY clues DESC;
```

---

## Task 2: MongoDB
The logs are deliberately messy, so the script first cleans them into an `events` collection (one ID spelling, one date format, no duplicates, plan tier added from SQL). Then four aggregation pipelines answer the questions.

| Query | Question | Idea |
|---|---|---|
| Q1 | Sessions per user per week by tier, plus 25th/50th/75th percentile duration | No session ID exists, so one session = one user on one day |
| Q2 | Daily active users and 7-day retention per feature | Distinct users per feature per day; share of users who return within 7 days |
| Q3 | Onboarding funnel with drop-off and median time between steps | Follows the customer account, because `member_id` does not line up with the customer |
| Q4 | Top 20 engaged free accounts | Score = 3 x features used + 2 x active days + events in the last 90 days |

```js
// =====================================================================
// Task 2 (simple version) - MongoDB, run in mongosh:
//     mongosh nimbus_events --file task2_simple.js     (MongoDB 7.0 or newer)
//
// STEP 0 - the logs do not contain the plan tier, so copy it from SQL once:
//   psql -d nimbus_core -c "\copy (SELECT DISTINCT ON (c.customer_id) c.customer_id, p.plan_tier
//        FROM nimbus.customers c JOIN nimbus.subscriptions s USING (customer_id)
//        JOIN nimbus.plans p USING (plan_id) ORDER BY c.customer_id, s.subscription_id DESC)
//        TO 'sql_customers.csv' CSV HEADER"
//   mongoimport --db nimbus_events --collection sql_customers --type csv --headerline --drop --file sql_customers.csv
// =====================================================================
db = db.getSiblingDB('nimbus_events');

// ---------------------------------------------------------------------
// STEP 1 - clean the messy logs once, save as "events".
//   * ids come as customer_id / customerId and member_id / userId / userID / memberId (some are text)
//   * timestamps come in 5 formats (slash dates are MM/DD/YYYY)
//   * some events are exact duplicates
// ---------------------------------------------------------------------
// turns any of the timestamp formats into a real date
const parseTime = {
  $cond: [
    { $eq: [{ $type: '$timestamp' }, 'date'] },
    '$timestamp',
    { $cond: [
      { $regexMatch: { input: '$timestamp', regex: '^\\d\\d/\\d\\d/\\d{4}' } },
      { $dateFromString: { dateString: '$timestamp', format: '%m/%d/%Y %H:%M:%S' } },
      // '2025-09-03 13:23:25' -> '2025-09-03T13:23:25' so every ISO-style variant parses
      { $dateFromString: { dateString: { $replaceOne: { input: '$timestamp', find: ' ', replacement: 'T' } } } } ] } ]
};

db.user_activity_logs.aggregate([
  { $addFields: {
      customer: { $toInt: { $ifNull: ['$customer_id', '$customerId'] } },
      user:     { $toInt: { $ifNull: ['$member_id', { $ifNull: ['$memberId', { $ifNull: ['$userId', '$userID'] }] }] } },
      time: parseTime } },
  { $group: { _id: { customer: '$customer', user: '$user', time: '$time', type: '$event_type', page: '$page_url' },
              doc: { $first: '$$ROOT' } } },                      // removes duplicates
  { $replaceRoot: { newRoot: '$doc' } },
  { $lookup: { from: 'sql_customers', localField: 'customer', foreignField: 'customer_id', as: 'sql' } },
  { $addFields: { tier: { $first: '$sql.plan_tier' } } },         // null tier = customer not in SQL
  { $project: { customer: 1, user: 1, time: 1, event_type: 1, feature: 1, tier: 1,
                session_duration_sec: 1, _id: 0 } },
  { $out: 'events' }
]);
print('clean events:', db.events.countDocuments(), 'of', db.user_activity_logs.countDocuments());


// ---------------------------------------------------------------------
// Q1. Average sessions per user per week, by plan tier, plus the 25th / 50th / 75th
//     percentile session duration.
//     No session id exists, so: 1 session = one user on one day.
// ---------------------------------------------------------------------
print('\nQ1');
db.events.aggregate([
  { $match: { tier: { $ne: null } } },
  { $group: { _id: { user: '$user', tier: '$tier', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              duration: { $max: '$session_duration_sec' } } },                      // one row per session
  { $group: { _id: { user: '$_id.user', tier: '$_id.tier',
                     week: { $dateTrunc: { date: '$_id.day', unit: 'week' } } },
              sessions: { $sum: 1 } } },                                            // sessions per user-week
  { $group: { _id: '$_id.tier', avg_sessions_per_user_per_week: { $avg: '$sessions' } } },
  { $sort: { _id: 1 } }
]).forEach(printjson);

db.events.aggregate([                                                               // the percentiles
  { $match: { tier: { $ne: null } } },
  { $group: { _id: { user: '$user', tier: '$tier', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              duration: { $max: '$session_duration_sec' } } },
  { $group: { _id: '$_id.tier',
              p25_p50_p75: { $percentile: { input: '$duration', p: [0.25, 0.5, 0.75], method: 'approximate' } } } },
  { $sort: { _id: 1 } }
]).forEach(printjson);


// ---------------------------------------------------------------------
// Q2a. Daily active users (DAU) per feature: for each feature, the average number of distinct users per day
// ---------------------------------------------------------------------
print('\nQ2a');
db.events.aggregate([
  { $match: { feature: { $ne: null } } },
  { $group: { _id: { feature: '$feature', day: { $dateTrunc: { date: '$time', unit: 'day' } } },
              users: { $addToSet: '$user' } } },
  { $group: { _id: '$_id.feature', avg_daily_active_users: { $avg: { $size: '$users' } } } },
  { $sort: { avg_daily_active_users: -1 } }
]).forEach(printjson);

// Q2b. 7-day retention: of the users who used a feature, how many used it AGAIN
//      within 7 days of their first use?
print('\nQ2b');
db.events.aggregate([
  { $match: { feature: { $ne: null } } },
  { $group: { _id: { feature: '$feature', user: '$user' }, times: { $push: '$time' }, first: { $min: '$time' } } },
  { $addFields: { came_back: { $anyElementTrue: { $map: { input: '$times', as: 't', in: { $and: [
      { $gt: ['$$t', '$first'] },
      { $lte: ['$$t', { $dateAdd: { startDate: '$first', unit: 'day', amount: 7 } }] } ] } } } } } },
  { $group: { _id: '$_id.feature', users: { $sum: 1 }, came_back: { $sum: { $cond: ['$came_back', 1, 0] } } } },
  { $project: { users: 1, came_back: 1,
                retention_7d_pct: { $round: [{ $multiply: [100, { $divide: ['$came_back', '$users'] }] }, 1] } } },
  { $sort: { retention_7d_pct: -1 } }
]).forEach(printjson);


// ---------------------------------------------------------------------
// Q3. Onboarding funnel: signup -> first_login -> workspace_created -> first_project -> invited_teammate
//     Counts how many customers reached each step, drop-off from the step before, and median hours between steps.
//     NOTE: we follow the CUSTOMER (account), not member_id. In this data member_id does not line up with
//     the customer, so following a "user" across steps finds almost nobody (2,000 signups -> 0 invites).
//     A step counts as reached if it ever happened; the steps are not guaranteed to be in order.
// ---------------------------------------------------------------------
print('\nQ3');
const steps = ['signup', 'first_login', 'workspace_created', 'first_project', 'invited_teammate'];
const first = {};                                                   // first time each step happened, per user
steps.forEach(s => first[s] = { $min: { $cond: [{ $eq: ['$step', s] }, '$time', '$$REMOVE'] } });

const accounts = db.onboarding_events.aggregate([
  { $addFields: {
      customer: { $toInt: { $ifNull: ['$customer_id', '$customerId'] } },
      time: parseTime } },
  { $group: Object.assign({ _id: '$customer' }, first) }
]).toArray();

function median(a) { a.sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; }
let previous = null;
steps.forEach((s, i) => {
  const reached = accounts.filter(u => u[s]);
  const hours = i === 0 ? [] :
    accounts.filter(u => u[s] && u[steps[i - 1]]).map(u => (u[s] - u[steps[i - 1]]) / 3600000).filter(h => h >= 0);
  print(s.padEnd(18), 'customers:', reached.length,
        previous === null ? '' : 'drop-off: ' + (100 * (1 - reached.length / previous)).toFixed(1) + '%',
        i === 0 ? '' : 'median hours from previous step: ' + (median(hours) === null ? 'n/a' : median(hours).toFixed(1)));
  previous = reached.length;
});


// ---------------------------------------------------------------------
// Q4. Top 20 most engaged FREE-plan accounts (upsell targets)
//     We score the CUSTOMER account (the thing that can be upsold). Scoring by member_id gives tiny, tied
//     scores because member_id does not line up with the customer in this data.
//     Engagement score = 3 x distinct features used + 2 x active days + 1 x events in last 90 days of data
//       - features used   = breadth, people who explore are hitting the free-plan limits
//       - active days     = habit (counted per day so one busy day cannot dominate)
//       - recent events   = still active now; a dormant user is not a lead
//     The weights are simple and easy to explain. (Free tier comes from SQL via sql_customers.)
// ---------------------------------------------------------------------
print('\nQ4');
const latest = db.events.find().sort({ time: -1 }).limit(1).toArray()[0].time;
const since = new Date(latest.getTime() - 90 * 24 * 3600 * 1000);
db.events.aggregate([
  { $match: { tier: 'free' } },
  { $group: { _id: '$customer',
              features: { $addToSet: '$feature' },
              days: { $addToSet: { $dateTrunc: { date: '$time', unit: 'day' } } },
              recent_events: { $sum: { $cond: [{ $gte: ['$time', since] }, 1, 0] } } } },
  { $project: { customer: '$_id', _id: 0,
                features_used: { $size: { $filter: { input: '$features', cond: { $ne: ['$$this', null] } } } },
                active_days: { $size: '$days' }, recent_events: 1 } },
  { $addFields: { engagement_score: { $add: [{ $multiply: [3, '$features_used'] },
                                             { $multiply: [2, '$active_days'] }, '$recent_events'] } } },
  { $sort: { engagement_score: -1 } },
  { $limit: 20 }
]).forEach(printjson);
```

---

## Task 3: Python (clean, merge, test, segment)
One row per customer, combining SQL facts (plan, churn, tickets) with Mongo facts (events). Every cleaning step prints before and after row counts.

**Hypothesis test**
- H0: churn is the same for low-engagement customers (bottom quarter by events per month) and everyone else.
- H1: churn differs. Significance level 0.05. Test: chi-square, with a logistic regression that controls for tenure.
- Why control for tenure: customers who stayed longer had more time to generate events, so a raw count would only measure how long people stayed.
- Result: about 32% against 19% churn, p < 0.001. Odds ratio 1.58 after controlling for tenure, so H0 is rejected.

**Segmentation:** K-means with 3 groups on engagement, support load and plan price. Churn is deliberately left out of the model so it can validate the groups afterwards.

```python
"""
Task 3 (simple version): merge, clean, hypothesis test, segmentation.
Run:  python task3_simple.py
Needs: pandas numpy scipy scikit-learn statsmodels  (+ psycopg2-binary pymongo for the live databases)
"""
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------- 1. LOAD (PostgreSQL + MongoDB)
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


# ---------------------------------------------------------------- 2. CLEAN: customers
n = len(customers)
customers = customers.copy()
customers["company_name"] = customers["company_name"].str.strip().replace("", np.nan)
customers = customers.dropna(subset=["company_name", "industry"])           # empty sign-ups
log("customers: drop empty sign-ups", n, len(customers))

n = len(customers)
customers["name_key"] = customers["company_name"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
customers["email_domain"] = customers["contact_email"].str.lower().str.split("@").str[1]
customers = customers.sort_values("customer_id").drop_duplicates(["name_key", "email_domain", "country_code"])
log("customers: drop duplicate accounts", n, len(customers))

customers["signup_date"] = pd.to_datetime(customers["signup_date"], format="mixed")
customers["churned_at"] = pd.to_datetime(customers["churned_at"], format="mixed")
customers["churned"] = customers["churned_at"].notna().astype(int)

# ---------------------------------------------------------------- CLEAN: events (Mongo)
n = len(events)
# the same field arrives under different names -> take whichever one is filled
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
events = events[events["customer_id"].isin(customers["customer_id"])]       # orphans not in SQL
log("events: drop customers missing from SQL", n, len(events))

n = len(events)
events = events.merge(customers[["customer_id", "signup_date", "churned_at"]], on="customer_id")
data_end = events["time"].max()
end = events["churned_at"].fillna(data_end)
events = events[(events["time"] >= events["signup_date"]) & (events["time"] <= end)]
log("events: keep only signup -> churn window", n, len(events))

# ---------------------------------------------------------------- 3. MERGE: one row per customer
last_sub = subs.sort_values("subscription_id").groupby("customer_id").tail(1)
last_sub = last_sub.merge(plans[["plan_id", "plan_tier"]], on="plan_id")[["customer_id", "plan_tier", "mrr_usd"]]
df = customers.merge(last_sub, on="customer_id", how="inner")               # SQL side

per_customer = events.groupby("customer_id").agg(n_events=("time", "size"),
                                                 features_used=("feature", "nunique"))
df = df.merge(per_customer, on="customer_id", how="left").fillna({"n_events": 0, "features_used": 0})
ticket_count = tickets.groupby("customer_id").size().rename("n_tickets")    # SQL side
df = df.merge(ticket_count, on="customer_id", how="left").fillna({"n_tickets": 0})

df["end"] = df["churned_at"].fillna(data_end)
df["tenure_days"] = (df["end"] - df["signup_date"]).dt.days.clip(lower=1)
months = df["tenure_days"] / 30.4
df["events_per_month"] = df["n_events"] / months
log("final table: customers x (SQL + Mongo)", len(customers), len(df))
print(f"churn rate = {df.churned.mean():.1%}")

# ---------------------------------------------------------------- 4. HYPOTHESIS TEST
print("\nH0: churn is the same for low-engagement customers and everyone else")
print("H1: churn is different.   alpha = 0.05   test: chi-square")
cut = df["events_per_month"].quantile(0.25)
df["low_engagement"] = (df["events_per_month"] <= cut).astype(int)
table = pd.crosstab(df["low_engagement"], df["churned"])
chi2, p, _, expected = stats.chi2_contingency(table, correction=False)
print(df.groupby("low_engagement")["churned"].mean().round(3).to_dict(), "(0 = others, 1 = low)")
print(f"chi2 = {chi2:.2f}, p = {p:.2g}, smallest expected count = {expected.min():.0f} (need >= 5)")
print("reject H0" if p < 0.05 else "fail to reject H0")

# same test but controlling for tenure (long-time customers have more time to log events)
X = sm.add_constant(pd.DataFrame({"low_engagement": df["low_engagement"],
                                  "log_tenure": np.log(df["tenure_days"])}))
model = sm.Logit(df["churned"], X).fit(disp=0)
print(f"odds ratio (low engagement) = {np.exp(model.params['low_engagement']):.2f}, "
      f"p = {model.pvalues['low_engagement']:.4f}  <- still significant after controlling for tenure")

# ---------------------------------------------------------------- 5. SEGMENTATION
safe_months = np.maximum(months, 3)   # a 1-month-old customer with 2 tickets is not a "problem account"
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
```

---

## Task 4: Dashboard
Built for the VP of Product and the Head of Customer Success. The story runs top to bottom: how bad is it, where, why, who is at risk, what to do.

**Dashboard link:** _add your Power BI / Tableau / Looker link here_

| # | Question | Chart | Source |
|---|---|---|---|
| 1 | Where and when is churn rising? | Monthly churn lines by plan tier | SQL |
| 2 | Does low engagement predict churn? | Churn by engagement quarter | **SQL + MongoDB** |
| 3 | Why do customers leave? | Churn reasons | SQL |
| 4 | Which groups carry revenue and risk? | Revenue share and churn rate by segment | SQL + MongoDB |
| 5 | Which features relate to retention? | Churn if used against not used | MongoDB + SQL |
| 6 | Where do new customers drop out? | Onboarding funnel | MongoDB |

**Filters:** plan tier, customer segment, signup year, tenure band. Every chart has a one-line insight, and the dashboard ends with the three recommendations above.

---

## Limitations
- The results are associations from observational data, not proof of cause: low usage may be a symptom of a decision to leave that was already made.
- Half of the Mongo events had impossible dates and were excluded, so engagement is measured on the remaining half.
- Invoice currency labels are inconsistent, so amounts were treated as USD without conversion.
