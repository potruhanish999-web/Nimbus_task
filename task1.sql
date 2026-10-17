SET search_path TO nimbus;
---Q1
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

---Q2

WITH latest_plan AS (
    SELECT DISTINCT ON (customer_id) customer_id, plan_id
    FROM subscriptions
    ORDER BY customer_id, subscription_id DESC
),
ltv AS (
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

---Q3

WITH changes AS (
    SELECT customer_id,
           plan_id AS new_plan_id,
           start_date::date AS change_date,
           LAG(plan_id) OVER (PARTITION BY customer_id ORDER BY subscription_id) AS old_plan_id
    FROM subscriptions
),
downgrades AS (
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


---Q4
WITH grid AS (
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
with_avg AS (
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

---Q5
WITH cust AS (
    SELECT customer_id, company_name,
           regexp_replace(lower(company_name), '[^a-z0-9]', '', 'g') AS name_key,
           CASE WHEN split_part(lower(contact_email), '@', 2)
                     IN ('gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com')
                THEN NULL ELSE split_part(lower(contact_email), '@', 2) END AS domain
    FROM customers
    WHERE company_name <> ''
),
shared AS (
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
