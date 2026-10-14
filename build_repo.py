import os
import shutil

SRC = "."
OUT = "../repo_final"
if os.path.exists(OUT):
    shutil.rmtree(OUT)


def read(name):
    return open(os.path.join(SRC, name), encoding="utf-8").read().strip()


def write(path, text):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.rstrip() + "\n")


task4_py = read("task4_data.py").replace(
    "from task3 import df, events",
    'sys.path.append("../task3-python")\nfrom task3 import df, events',
).replace("import json\n", "import json\nimport sys\n", 1)

files = {
    "task1-sql/task1.sql": read("task1.sql"),
    "task2-mongodb/task2.js": read("task2.js"),
    "task3-python/task3.py": read("task3.py"),
    "task4-dashboard/task4_data.py": task4_py,
    "task4-dashboard/dashboard.html": read("dashboard.html"),
}
for path, text in files.items():
    write(path, text)

write("task1-sql/README.md", f"""# Task 1 - SQL

## Setup
```bash
psql -U postgres -c "CREATE DATABASE nimbus_core"
psql -U postgres -d nimbus_core -f nimbus_core.sql
```

## Code
`task1.sql`
```sql
{files["task1-sql/task1.sql"]}
```

## Run
```bash
psql -U postgres -d nimbus_core -f task1.sql
```
""")

write("task2-mongodb/README.md", f"""# Task 2 - MongoDB

## Setup
```bash
mongosh --file nimbus_events.js
psql -U postgres -d nimbus_core -c "\\copy (SELECT DISTINCT ON (c.customer_id) c.customer_id, p.plan_tier FROM nimbus.customers c JOIN nimbus.subscriptions s USING (customer_id) JOIN nimbus.plans p USING (plan_id) ORDER BY c.customer_id, s.subscription_id DESC) TO 'sql_customers.csv' CSV HEADER"
mongoimport --db nimbus_events --collection sql_customers --type csv --headerline --drop --file sql_customers.csv
```

## Code
`task2.js`
```js
{files["task2-mongodb/task2.js"]}
```

## Run
```bash
mongosh nimbus_events --file task2.js
```
""")

write("task3-python/README.md", f"""# Task 3 - Python

## Setup
```bash
pip install pandas numpy scipy scikit-learn statsmodels psycopg2-binary pymongo
```

## Code
`task3.py`
```python
{files["task3-python/task3.py"]}
```

## Run
```bash
python task3.py
```
""")

write("task4-dashboard/README.md", f"""# Task 4 - Dashboard

## Setup
```bash
pip install pandas numpy statsmodels psycopg2-binary pymongo scipy scikit-learn
```

## Code
`task4_data.py`
```python
{files["task4-dashboard/task4_data.py"]}
```

`dashboard.html`
```html
{files["task4-dashboard/dashboard.html"]}
```

## Run
```bash
python task4_data.py
```
Open `dashboard.html` in a browser (keep `data.js` in the same folder).
""")

write(".gitignore", "*.csv\n*.pdf\nnimbus_core*.sql\nnimbus_events.js\ndata.js\noutputs/\n__pycache__/")

for root, _, names in os.walk(OUT):
    for n in sorted(names):
        p = os.path.join(root, n)
        print(os.path.relpath(p, OUT).replace(os.sep, "/"), os.path.getsize(p))
