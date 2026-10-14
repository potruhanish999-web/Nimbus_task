# Task 4 - Dashboard

## Setup
```bash
pip install pandas numpy statsmodels psycopg2-binary pymongo scipy scikit-learn
```

## Code
`task4_data.py`
```python
import json
import sys

import numpy as np
import pandas as pd
import psycopg2
import statsmodels.api as sm
from pymongo import MongoClient
from statsmodels.stats.multitest import multipletests

sys.path.append("../task3-python")
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
```

`dashboard.html`
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NimbusAI Churn Dashboard</title>
<style>
:root {
  --page: #f6f6f3; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #7b7a74;
  --grid: #e1e0d9; --line: #c3c2b7; --ring: rgba(11,11,11,.10);
  --blue: #2a78d6; --orange: #eb6834; --aqua: #1baf7a; --yellow: #eda100; --neutral: #b9b8b0; --wash: #eef3fb;
  --font: system-ui, -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --muted: #98968f;
    --grid: #2c2c2a; --line: #383835; --ring: rgba(255,255,255,.10);
    --blue: #3987e5; --orange: #d95926; --aqua: #199e70; --yellow: #c98500; --neutral: #4a4a46; --wash: #20262f;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --muted: #98968f;
  --grid: #2c2c2a; --line: #383835; --ring: rgba(255,255,255,.10);
  --blue: #3987e5; --orange: #d95926; --aqua: #199e70; --yellow: #c98500; --neutral: #4a4a46; --wash: #20262f;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--page); color: var(--ink); font-family: var(--font); line-height: 1.45; margin: 0; padding-inline: 16px; padding-block: 18px 40px; }
.wrap { max-width: 1180px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
h1 { font-size: 1.5rem; margin: 0; letter-spacing: -.01em; text-wrap: balance; }
h2 { font-size: .98rem; margin: 0; text-wrap: balance; }
.sub { color: var(--ink2); margin: 4px 0 0; max-width: 78ch; }
.eyebrow { font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 4px; }
.filters { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 12px 14px; display: flex; flex-wrap: wrap; gap: 14px 22px; align-items: flex-end; }
@media (min-width: 900px) { .filters { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; } }
.f { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.f > span { font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip, select, .btn { font: inherit; font-size: .85rem; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
.chip[aria-pressed="true"] { background: var(--blue); border-color: var(--blue); color: #fff; }
.chip:focus-visible, select:focus-visible, .btn:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.btn { color: var(--ink2); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.kpi { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 12px 14px; min-width: 0; }
.kpi b { display: block; font-size: 1.7rem; letter-spacing: -.02em; }
.kpi small { color: var(--ink2); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 480px), 1fr)); gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px 16px 12px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.card .q { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
.card .tag { font-size: .7rem; color: var(--muted); white-space: nowrap; }
.insight { color: var(--ink2); font-size: .88rem; margin: 0; min-height: 2.6em; }
.insight b { color: var(--ink); }
.plot { position: relative; height: 270px; }
.plot.tall { height: 330px; }
.tbl-btn { align-self: flex-start; font-size: .78rem; background: none; border: 0; color: var(--blue); cursor: pointer; padding: 2px 0; font-family: inherit; }
.tscroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .84rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: .72rem; letter-spacing: .05em; text-transform: uppercase; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; font-size: .72rem; padding: 1px 8px; border-radius: 99px; background: var(--wash); color: var(--ink2); }
.recs { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 12px; }
.rec { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px 16px; min-width: 0; }
.rec h3 { margin: 0 0 6px; font-size: .98rem; }
.rec p { margin: 4px 0; font-size: .88rem; color: var(--ink2); }
.rec p b { color: var(--ink); }
@media (max-width: 520px) { .plot { height: 240px; } h1 { font-size: 1.25rem; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">NimbusAI churn and retention</p>
    <h1 id="headline"></h1>
    <p class="sub">Use the filters to see where it hurts.</p>
  </header>

  <section class="filters" aria-label="Filters">
    <div class="f"><span>Plan tier</span><div class="chips" id="tierChips"></div></div>
    <div class="f"><span>Segment</span><select id="segSel" aria-label="Segment"></select></div>
    <div class="f"><span>Signup year</span><select id="yearSel" aria-label="Signup year"></select></div>
    <div class="f"><span>Tenure</span><select id="tbSel" aria-label="Tenure"></select></div>
    <button class="btn" id="reset" type="button">Reset</button>
  </section>

  <section class="kpis" id="kpis" aria-label="Key numbers"></section>

  <section class="grid">
    <article class="card"><div class="q"><h2>Where and when is churn rising?</h2><span class="tag">SQL</span></div>
      <p class="insight" id="i1"></p><div class="plot"><canvas id="c1"></canvas></div><button class="tbl-btn" data-t="c1" type="button">Show data table</button><div class="tscroll" id="t-c1" hidden></div></article>
    <article class="card"><div class="q"><h2>Does low product engagement predict churn?</h2><span class="tag">SQL churn + Mongo activity</span></div>
      <p class="insight" id="i2"></p><div class="plot"><canvas id="c2"></canvas></div><button class="tbl-btn" data-t="c2" type="button">Show data table</button><div class="tscroll" id="t-c2" hidden></div></article>
    <article class="card"><div class="q"><h2>Why do customers say they leave?</h2><span class="tag">SQL</span></div>
      <p class="insight" id="i3"></p><div class="plot tall"><canvas id="c3"></canvas></div><button class="tbl-btn" data-t="c3" type="button">Show data table</button><div class="tscroll" id="t-c3" hidden></div></article>
    <article class="card"><div class="q"><h2>Which customer groups carry the revenue and the risk?</h2><span class="tag">SQL + Mongo</span></div>
      <p class="insight" id="i4"></p><div class="plot tall"><canvas id="c4"></canvas></div><button class="tbl-btn" data-t="c4" type="button">Show data table</button><div class="tscroll" id="t-c4" hidden></div></article>
    <article class="card"><div class="q"><h2>Which features relate to retention?</h2><span class="tag">Mongo + SQL, not filtered</span></div>
      <p class="insight" id="i5"></p><div class="plot tall"><canvas id="c5"></canvas></div><button class="tbl-btn" data-t="c5" type="button">Show data table</button><div class="tscroll" id="t-c5" hidden></div></article>
    <article class="card"><div class="q"><h2>Where do new customers drop out of onboarding?</h2><span class="tag">Mongo, not filtered</span></div>
      <p class="insight" id="i6"></p><div class="plot tall"><canvas id="c6"></canvas></div><button class="tbl-btn" data-t="c6" type="button">Show data table</button><div class="tscroll" id="t-c6" hidden></div></article>
  </section>

  <section class="card">
    <div class="q"><h2>Who should Customer Success call first?</h2><span class="tag">active, lowest-engagement quarter, by monthly revenue</span></div>
    <p class="insight" id="i7"></p>
    <div class="tscroll"><table id="riskTbl"></table></div>
  </section>

  <section aria-label="Recommendations">
    <h2 style="margin-bottom:8px">Three recommendations</h2>
    <div class="recs">
      <div class="rec"><h3>1. Add an engagement early warning</h3><div id="r1"></div></div>
      <div class="rec"><h3>2. Convert engaged Free users</h3><div id="r2"></div></div>
      <div class="rec"><h3>3. Test a teammate-invite nudge</h3><div id="r3"></div></div>
    </div>
  </section>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="data.js"></script>
<script>
const TIER_NAMES = ["Free", "Starter", "Professional", "Enterprise"];
const TB_NAMES = ["Under 6 months", "6 to 12 months", "1 year or more"];
const Q_NAMES = ["Lowest quarter", "Second", "Third", "Highest quarter"];
const state = { tiers: new Set([0, 1, 2, 3]), seg: "all", year: "all", tb: "all" };
const charts = {};
const pct = (x, d = 1) => (x * 100).toFixed(d) + "%";
const money = x => "$" + Math.round(x).toLocaleString("en-US");
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const sum = (a, f) => a.reduce((s, r) => s + f(r), 0);
const rate = a => (a.length ? a.filter(r => r[5]).length / a.length : 0);
const sorted = a => [...a].sort((x, y) => x - y);
const quantile = (a, q) => { const s = sorted(a); return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0; };

const chips = document.getElementById("tierChips");
TIER_NAMES.forEach((n, i) => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.textContent = n; b.setAttribute("aria-pressed", "true");
  b.onclick = () => {
    if (state.tiers.has(i)) { if (state.tiers.size > 1) state.tiers.delete(i); } else state.tiers.add(i);
    b.setAttribute("aria-pressed", state.tiers.has(i));
    render();
  };
  chips.appendChild(b);
});

function fillSel(id, opts) {
  const s = document.getElementById(id);
  s.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  s.onchange = () => { state[id === "segSel" ? "seg" : id === "yearSel" ? "year" : "tb"] = s.value; render(); };
}
const years = [...new Set(D.rows.map(r => r[3]))].sort();
fillSel("segSel", [["all", "All segments"], ...D.segments.map((s, i) => [i, s])]);
fillSel("yearSel", [["all", "All years"], ...years.map(y => [y, y])]);
fillSel("tbSel", [["all", "All tenures"], ...TB_NAMES.map((s, i) => [i, s])]);

document.getElementById("reset").onclick = () => {
  state.tiers = new Set([0, 1, 2, 3]); state.seg = state.year = state.tb = "all";
  chips.querySelectorAll(".chip").forEach(c => c.setAttribute("aria-pressed", "true"));
  ["segSel", "yearSel", "tbSel"].forEach(id => (document.getElementById(id).value = "all"));
  render();
};

const rowsNow = () => D.rows.filter(r => state.tiers.has(r[1]) && (state.seg === "all" || r[2] == state.seg)
  && (state.year === "all" || r[3] == state.year) && (state.tb === "all" || r[4] == state.tb));

function poolRate(tierSet, lo, hi) {
  let o = 0, c = 0;
  [...tierSet].forEach(ti => {
    const m = D.monthly[D.tiers[ti]];
    m.months.forEach((mo, i) => { if (mo >= lo && mo <= hi) { o += m.opening[i]; c += m.churned[i]; } });
  });
  return o ? 100 * c / o : 0;
}

function mk(id, config) {
  if (charts[id]) charts[id].destroy();
  Chart.defaults.font.family = css("--font");
  Chart.defaults.color = css("--ink2");
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, config.options);
  charts[id] = new Chart(document.getElementById(id), config);
  if (!document.getElementById("t-" + id).hidden) fillTable(id);
}
const axis = (extra = {}) => Object.assign({ grid: { color: css("--grid") }, border: { color: css("--line") }, ticks: { color: css("--muted") } }, extra);
const legend = { position: "bottom", labels: { color: css("--ink2"), boxWidth: 12, boxHeight: 12 } };

function fillTable(id) {
  const c = charts[id], t = document.getElementById("t-" + id);
  const sets = c.data.datasets.filter(d => d.label);
  const head = ["", ...sets.map(d => d.label)];
  const body = c.data.labels.map((l, i) => [l, ...sets.map(d => (d.data[i] == null ? "" : typeof d.data[i] === "number" ? +d.data[i].toFixed(1) : d.data[i]))]);
  t.innerHTML = "<table><tr>" + head.map(h => `<th>${h}</th>`).join("") + "</tr>" +
    body.map(r => "<tr>" + r.map((v, j) => `<td${j ? ' class="n"' : ""}>${v}</td>`).join("") + "</tr>").join("") + "</table>";
}
document.querySelectorAll(".tbl-btn").forEach(b => (b.onclick = () => {
  const t = document.getElementById("t-" + b.dataset.t);
  t.hidden = !t.hidden;
  b.textContent = t.hidden ? "Show data table" : "Hide data table";
  if (!t.hidden) fillTable(b.dataset.t);
}));

function fixedText() {
  const all = D.rows, active = all.filter(r => !r[5]);
  const allTiers = new Set([0, 1, 2, 3]);
  const before = poolRate(allTiers, "2024-01", "2024-06"), after = poolRate(allTiers, "2025-05", "2025-10");
  const low = all.filter(r => r[6] === 0), rest = all.filter(r => r[6] !== 0);
  document.getElementById("headline").textContent =
    `Churn is up ${before ? (after / before).toFixed(1) : "n/a"} times since early 2024, and the quietest customers leave ${rate(rest) ? (rate(low) / rate(rest)).toFixed(1) : "n/a"} times as often`;

  const risk = active.filter(r => r[9]);
  document.getElementById("r1").innerHTML =
    `<p>Flag active customers using the product less than about once a month and start outreach.</p>
     <p><b>${risk.length} customers</b> qualify, with <b>${money(sum(risk, r => r[7]))}</b> monthly revenue (${pct(sum(risk, r => r[7]) / sum(active, r => r[7]), 0)} of the active total). They churn at ${pct(rate(low))} against ${pct(rate(rest))} for everyone else.</p>`;

  const free = all.filter(r => r[1] === 0), starter = all.filter(r => r[1] === 1);
  const freeActive = active.filter(r => r[1] === 0);
  const cut = quantile(freeActive.map(r => r[11]), 0.75);
  const top = freeActive.filter(r => r[11] >= cut);
  const paid = sorted(active.filter(r => r[1] === 1 && r[7] > 0).map(r => r[7]));
  const starterPrice = paid.length ? paid[Math.floor(paid.length / 2)] : 0;
  document.getElementById("r2").innerHTML =
    `<p>Free churns ${pct(rate(free))} against ${pct(rate(starter))} on Starter. Prompt the busiest Free accounts to upgrade.</p>
     <p><b>${top.length} active Free customers</b> are in the top quarter of Free users by engagement. At the median Starter price that is about <b>${money(top.length * starterPrice)}</b> a month if all convert.</p>`;

  const invite = D.funnel[D.funnel.length - 1];
  const gone = all.filter(r => r[5] && r[8] >= 0);
  const counts = {};
  gone.forEach(r => (counts[r[8]] = (counts[r[8]] || 0) + 1));
  const topReason = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  document.getElementById("r3").innerHTML =
    `<p>The invite step reaches only ${pct(invite.step_conversion, 0)} of customers who got to the step before it${topReason ? `, and "${D.reasons[topReason[0]]}" is the top recorded reason for leaving (${topReason[1]} customers)` : ""}.</p>
     <p>Treat this as an experiment: test a nudge and compare churn against a control group.</p>`;
}

function render() {
  const R = rowsNow(), n = R.length, ch = R.filter(r => r[5]).length;
  const act = R.filter(r => !r[5]);
  const kp = [
    ["Customers", n.toLocaleString("en-US"), "match the filters"],
    ["Churn rate", n ? pct(ch / n) : "n/a", `${ch} customers have left`],
    ["Active monthly revenue", money(sum(act, r => r[7])), `${act.length} active customers`],
    ["Revenue at risk", money(sum(act.filter(r => r[9]), r => r[7])), `${act.filter(r => r[9]).length} active, lowest-engagement quarter`],
  ];
  document.getElementById("kpis").innerHTML = kp.map(k => `<div class="kpi"><small>${k[0]}</small><b>${k[1]}</b><small>${k[2]}</small></div>`).join("");
  c1(); c2(R); c3(R); c4(R); c5(); c6(); risk(R);
}

function c1() {
  const months = D.monthly.free.months.filter(m => m >= "2023-07");
  const colors = [css("--blue"), css("--orange"), css("--aqua"), css("--yellow")];
  const ds = [];
  [...state.tiers].sort().forEach(ti => {
    const m = D.monthly[D.tiers[ti]];
    const data = months.map(mo => {
      const i = m.months.indexOf(mo);
      if (i < 2) return null;
      const op = m.opening[i] + m.opening[i - 1] + m.opening[i - 2];
      return op >= 60 ? 100 * (m.churned[i] + m.churned[i - 1] + m.churned[i - 2]) / op : null;
    });
    ds.push({ label: TIER_NAMES[ti], data, borderColor: colors[ti], backgroundColor: colors[ti], borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .25 });
  });
  const a = poolRate(state.tiers, "2024-01", "2024-06"), b = poolRate(state.tiers, "2025-05", "2025-10");
  document.getElementById("i1").innerHTML = `For the selected plans, monthly churn went from <b>${a.toFixed(2)}%</b> in early 2024 to <b>${b.toFixed(2)}%</b> in May to October 2025 (${a ? (b / a).toFixed(1) : "n/a"}x).`;
  mk("c1", { type: "line", data: { labels: months, datasets: ds },
    options: { interaction: { mode: "index", intersect: false },
      plugins: { legend, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y == null ? "n/a" : c.parsed.y.toFixed(2) + "%"}` } } },
      scales: { x: axis({ ticks: { color: css("--muted"), maxTicksLimit: 8 } }),
                y: axis({ beginAtZero: true, title: { display: true, text: "Monthly churn, 3-month pooled (%)", color: css("--muted") } }) } } });
}

function c2(R) {
  const groups = Q_NAMES.map((_, q) => R.filter(r => r[6] === q));
  const values = groups.map(g => (g.length ? 100 * rate(g) : null));
  const base = R.length ? 100 * rate(R) : 0;
  const low = R.filter(r => r[6] === 0), rest = R.filter(r => r[6] !== 0);
  document.getElementById("i2").innerHTML = low.length && rest.length
    ? `The lowest-engagement quarter churns at <b>${pct(rate(low))}</b> against <b>${pct(rate(rest))}</b> for everyone else (${low.length} vs ${rest.length} customers). Set Tenure to "1 year or more" to see the cleanest pattern.`
    : "Not enough customers in this selection.";
  mk("c2", { type: "bar", data: { labels: Q_NAMES, datasets: [
      { label: "Churn rate (%)", data: values, backgroundColor: values.map((_, i) => (i === 0 ? css("--orange") : css("--blue"))), borderRadius: 4, maxBarThickness: 64 },
      { label: "All selected customers", type: "line", data: Q_NAMES.map(() => base), borderColor: css("--muted"), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 }] },
    options: { plugins: { legend, tooltip: { callbacks: { label: c => (c.datasetIndex ? `Overall: ${c.parsed.y.toFixed(1)}%` : `Churn: ${c.parsed.y == null ? "n/a" : c.parsed.y.toFixed(1) + "%"} (${groups[c.dataIndex].length} customers)`) } } },
      scales: { x: axis({ grid: { display: false }, title: { display: true, text: "Product events per month (Mongo), lowest to highest", color: css("--muted") } }),
                y: axis({ beginAtZero: true, title: { display: true, text: "Churn rate (%)", color: css("--muted") } }) } } });
}

function c3(R) {
  const gone = R.filter(r => r[5]);
  const cnt = {};
  gone.forEach(r => { const k = r[8] < 0 ? "(not recorded)" : D.reasons[r[8]]; cnt[k] = (cnt[k] || 0) + 1; });
  const items = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
  const top = items.find(i => i[0] !== "(not recorded)");
  document.getElementById("i3").innerHTML = gone.length
    ? `<b>${cnt["(not recorded)"] ? pct(cnt["(not recorded)"] / gone.length, 0) : "0%"}</b> of churned customers have no reason on file. The most common recorded reason is <b>${top ? top[0] : "n/a"}</b>${top ? ` (${top[1]} customers)` : ""}.`
    : "No churned customers in this selection.";
  mk("c3", { type: "bar", data: { labels: items.map(i => i[0]), datasets: [{ label: "Churned customers", data: items.map(i => i[1]),
      backgroundColor: items.map(i => (i[0] === "(not recorded)" ? css("--neutral") : css("--blue"))), borderRadius: 4 }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } },
      scales: { x: axis({ beginAtZero: true, ticks: { color: css("--muted"), precision: 0 } }), y: axis({ grid: { display: false } }) } } });
}

function c4(R) {
  const total = sum(R, r => r[10]);
  const seg = D.segments.map((_, i) => R.filter(r => r[2] === i));
  const share = seg.map(g => (total ? 100 * sum(g, r => r[10]) / total : 0));
  const churn = seg.map(g => 100 * rate(g));
  const names = D.segments.map((s, i) => `${s} (${seg[i].length})`);
  const worst = churn.indexOf(Math.max(...churn));
  document.getElementById("i4").innerHTML = R.length
    ? `<b>${D.segments[worst]}</b> has the highest churn at <b>${churn[worst].toFixed(1)}%</b>. Groups come from a K-means model on engagement, support load and plan price; churn was not used to build them.`
    : "No customers in this selection.";
  mk("c4", { type: "bar", data: { labels: names, datasets: [
      { label: "Share of paid revenue (%)", data: share, backgroundColor: css("--blue"), borderRadius: 4, maxBarThickness: 46 },
      { label: "Churn rate (%)", data: churn, backgroundColor: css("--orange"), borderRadius: 4, maxBarThickness: 46 }] },
    options: { plugins: { legend, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } } },
      scales: { x: axis({ grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkip: false } }), y: axis({ beginAtZero: true }) } } });
}

function c5() {
  const f = [...D.features].sort((a, b) => a.churn_if_used - b.churn_if_used);
  const minp = Math.min(...f.map(x => x.p_fdr));
  document.getElementById("i5").innerHTML = `Adopters look safer, but once tenure is controlled for the smallest adjusted p-value across ${f.length} features is <b>${minp.toFixed(2)}</b>${minp < 0.05 ? "" : ", so none is significant"}.`;
  mk("c5", { type: "bar", data: { labels: f.map(x => x.feature.replace(/_/g, " ")), datasets: [
      { label: "Used the feature", data: f.map(x => 100 * x.churn_if_used), backgroundColor: css("--blue"), borderRadius: 3 },
      { label: "Did not use it", data: f.map(x => 100 * x.churn_if_not), backgroundColor: css("--neutral"), borderRadius: 3 }] },
    options: { indexAxis: "y", plugins: { legend, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.x.toFixed(1)}% churn` } } },
      scales: { x: axis({ beginAtZero: true, title: { display: true, text: "Churn rate (%)", color: css("--muted") } }), y: axis({ grid: { display: false } }) } } });
}

function c6() {
  const f = D.funnel;
  const worst = f.slice(1).reduce((a, b) => (b.step_conversion < a.step_conversion ? b : a));
  document.getElementById("i6").innerHTML = `The biggest loss is at <b>${worst.step.replace(/_/g, " ")}</b>: only <b>${pct(worst.step_conversion, 0)}</b> of customers who got to the step before it reach it.`;
  mk("c6", { type: "bar", data: { labels: f.map(x => x.step.replace(/_/g, " ")), datasets: [{ label: "Customers who reached the step", data: f.map(x => x.customers_reached), backgroundColor: css("--blue"), borderRadius: 4 }] },
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.x} customers` + (c.dataIndex ? ` (${pct(f[c.dataIndex].step_conversion, 0)} of previous step)` : "") } } },
      scales: { x: axis({ beginAtZero: true, ticks: { color: css("--muted"), precision: 0 } }), y: axis({ grid: { display: false } }) } } });
}

function risk(R) {
  const L = R.filter(r => !r[5] && r[9]).sort((a, b) => b[7] - a[7]);
  document.getElementById("i7").innerHTML = `<b>${L.length}</b> active customers in this selection are in the lowest-engagement quarter, worth <b>${money(sum(L, r => r[7]))}</b> a month. The ten largest are below.`;
  document.getElementById("riskTbl").innerHTML = `<tr><th>Customer</th><th>Plan</th><th>Segment</th><th class="n">Monthly revenue</th><th class="n">Events per month</th></tr>` +
    L.slice(0, 10).map(r => `<tr><td>${r[0]}</td><td>${TIER_NAMES[r[1]]}</td><td><span class="pill">${D.segments[r[2]]}</span></td><td class="n">${money(r[7])}</td><td class="n">${r[11].toFixed(2)}</td></tr>`).join("");
}

fixedText();
render();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
</script>
</body>
</html>
```

## Run
```bash
python task4_data.py
```
Open `dashboard.html` in a browser (keep `data.js` in the same folder).
