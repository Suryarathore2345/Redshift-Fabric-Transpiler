<div align="center">
<br/>

<img src="https://img.shields.io/badge/AWS%20Redshift-%23FF9900?style=for-the-badge&logo=amazonaws&logoColor=white" alt="AWS Redshift"/>
&nbsp;&nbsp;
<img src="https://img.shields.io/badge/%E2%86%92-2d2d2d?style=for-the-badge" alt="→"/>
&nbsp;&nbsp;
<img src="https://img.shields.io/badge/Microsoft%20Fabric-%230078D4?style=for-the-badge&logo=microsoft&logoColor=white" alt="Microsoft Fabric"/>

<br/><br/>

# Redshift → Fabric DDL Converter

Enterprise-grade SQL migration tool that converts AWS Redshift DDL into production-ready Microsoft Fabric T-SQL.

Upload a `.sql` file or paste DDL directly — get parameterised Fabric T-SQL with full diagnostics, inline warnings, and downloadable output in seconds.

<br/>

</div>

---

## ✨ What It Does

Migrating from AWS Redshift to Microsoft Fabric requires rewriting every `CREATE TABLE` and `CREATE VIEW` — Redshift uses PostgreSQL-dialect SQL with proprietary storage hints, while Fabric uses T-SQL with its own function names, type system, and syntax rules.

This tool automates that conversion entirely:

* 📄 Paste DDL or upload a `.sql` / `.txt` file — both work the same way
* ⚡ Converts Tables, Views, and Materialised Views in a single pass
* 🏷 Injects Flyway-compatible placeholders like `${schema}`, `${rs_sales}`, `${os_reporting}` automatically — no config needed for new schemas
* ⚠️ Inline warnings on every affected line — you see exactly what needs review, right in the SQL
* 📊 Confidence scoring per object — HIGH CONFIDENCE / PARTIAL / MANUAL REVIEW / FAILED
* 🏭 Materialised Views → **Fabric Warehouse Stored Procedure** (T-SQL CTAS pattern) or **Fabric Lakehouse Materialized Lake View** (Spark SQL) — selectable from the UI
* 🔀 **Dynamic schema mode** (parameterised placeholders) or **Hardcoded schema mode** (original names preserved) — toggle in the UI
* 📥 Download converted SQL and Markdown reports from the UI

<br/>

---

## 🖥 Web UI

Start the server and open `http://localhost:8000` in your browser.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ✏️  PASTE SQL              📁  UPLOAD FILE                          │
│  ──────────────────          ──────────────────────────────────────  │
│  [ Paste Redshift DDL... ]  [ Drop .sql / .txt here or browse     ] │
│                                                                     │
│  MV TARGET  [ 🏭 Warehouse SP ]  [ 🌊 Lakehouse MV ]               │
│  SCHEMA     [ 🔀 Dynamic      ]  [ 📌 Hardcoded    ]               │
│                                                                     │
│               [ ⚡ Convert to Fabric T-SQL ]                        │
└─────────────────────────────────────────────────────────────────────┘
```

After conversion, results appear as expandable cards — one per object — showing the converted T-SQL with syntax highlighting, inline warnings, and applied transformation rules. All output is downloadable.

<br/>

---

## 🔄 Conversion Examples

### Table — `CREATE TABLE`

**Input (Redshift):**

```sql
CREATE TABLE bi_schools.student_login (
    login_date_dw_id      bigint ENCODE raw,
    school_dw_id          bigint ENCODE raw DISTKEY,
    outside_school_flag   boolean ENCODE raw,
    login_local_date_time timestamp without time zone ENCODE az64
) DISTSTYLE AUTO SORTKEY (school_dw_id, login_date_dw_id);
```

**Output (Fabric T-SQL):**

```sql
-- ══════════════════════════════════════════════════════════════════
-- TABLE  : bi_schools.student_login
-- Target : ${schema}.student_login
-- Status : ✅ HIGH_CONFIDENCE  |  Confidence: 100%
-- Warnings: 0  ← clean conversion
-- ══════════════════════════════════════════════════════════════════
IF OBJECT_ID('${schema}.student_login', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.student_login (
        login_date_dw_id      BIGINT,
        school_dw_id          BIGINT,
        outside_school_flag   BIT,
        login_local_date_time DATETIME2(6)
    );
END;
```

`ENCODE`, `DISTKEY`, `DISTSTYLE`, `SORTKEY` stripped · `boolean → BIT` · `timestamp → DATETIME2(6)` · Idempotent `IF OBJECT_ID` wrapper added · Schema parameterised as `${schema}`

<br/>

### View — `CREATE VIEW`

**Input (Redshift):**

```sql
CREATE OR REPLACE VIEW reporting.vw_policy_summary AS
SELECT
    p.policy_id,
    c.customer_name,
    cl.claim_amount,
    DATE_TRUNC('month', cl.claim_date)   AS claim_month,
    NVL(cl.claim_status, 'PENDING')      AS claim_status,
    COALESCE(p.premium_amount, 0)        AS premium
FROM insurance.policy p
JOIN customer.customer_master c  ON p.customer_id  = c.customer_id
JOIN claims.claim_details cl     ON p.policy_id    = cl.policy_id
GROUP BY 1, 2, 3, 4, 5, 6;
```

**Output (Fabric T-SQL):**

```sql
-- ══════════════════════════════════════════════════════════════════
-- VIEW    : reporting.vw_policy_summary
-- Target  : ${os_reporting}.vw_policy_summary
-- Status  : ✅ HIGH_CONFIDENCE  |  Confidence: 100%
-- Warnings: 0
-- ══════════════════════════════════════════════════════════════════

CREATE OR ALTER VIEW ${os_reporting}.vw_policy_summary AS
SELECT
    p.policy_id,
    c.customer_name,
    cl.claim_amount,
    DATETRUNC(month, cl.claim_date)       AS claim_month,
    ISNULL(cl.claim_status, 'PENDING')    AS claim_status,
    ISNULL(p.premium_amount, 0)           AS premium
FROM ${rs_insurance}.policy p
JOIN ${rs_customer}.customer_master c  ON p.customer_id  = c.customer_id
JOIN ${rs_claims}.claim_details cl     ON p.policy_id    = cl.policy_id
GROUP BY
    p.policy_id,
    c.customer_name,
    cl.claim_amount,
    DATETRUNC(month, cl.claim_date),
    ISNULL(cl.claim_status, 'PENDING'),
    ISNULL(p.premium_amount, 0);
```

`CREATE OR REPLACE VIEW → CREATE OR ALTER VIEW` · `DATE_TRUNC → DATETRUNC` · `NVL → ISNULL` · `COALESCE(a,b) → ISNULL(a,b)` · `GROUP BY 1,2,3 → explicit columns` · All schema names auto-parameterised · Table aliases (`p`, `c`, `cl`) preserved correctly

<br/>

### Materialised View — Option 1: Warehouse Stored Procedure

**Output (Fabric T-SQL — `MV TARGET: Warehouse SP`):**

```sql
CREATE OR ALTER PROCEDURE ${os_bi_schools}.usp_refresh_agg_login AS
BEGIN
    DROP TABLE IF EXISTS ${os_bi_schools}.agg_login_staging;
    CREATE TABLE ${os_bi_schools}.agg_login_staging AS <original SELECT>;
    DROP TABLE IF EXISTS ${os_bi_schools}.agg_login;
    EXEC sp_rename 'agg_login_staging', 'agg_login';
END;
```

<br/>

### Materialised View — Option 2: Lakehouse Materialized Lake View

**Output (Spark SQL — `MV TARGET: Lakehouse MV`):**

```sql
-- ══════════════════════════════════════════════════════════════════
-- MATERIALIZED LAKE VIEW: bi_schools.agg_login
-- Target  : ${os_bi_schools}.agg_login
-- Engine  : Fabric Lakehouse · Spark SQL (Delta Lake)
-- Status  : ✅ HIGH_CONFIDENCE  |  Confidence: 100%
-- ══════════════════════════════════════════════════════════════════

CREATE OR REPLACE MATERIALIZED LAKE VIEW ${os_bi_schools}.agg_login
AS
SELECT
    s.school_id,
    DATE_TRUNC('month', s.login_date)  AS login_month,
    COALESCE(s.grade, 0)               AS grade,
    ARRAY_JOIN(COLLECT_LIST(s.name), ', ') AS student_list
FROM ${rs_bi_schools}.students s
GROUP BY
    s.school_id,
    DATE_TRUNC('month', s.login_date),
    COALESCE(s.grade, 0);
```

<br/>

---

## 🗺 Automatic Schema Parameterisation

Any schema name is automatically converted to a Flyway placeholder with zero configuration.

| Redshift SQL | Fabric Output | How |
|---|---|---|
| `FROM insurance.policy` | `FROM ${rs_insurance}.policy` | Auto-generated |
| `FROM sales.orders` | `FROM ${rs_sales}.orders` | Auto-generated |
| `CREATE VIEW reporting.v_foo` | `CREATE OR ALTER VIEW ${os_reporting}.v_foo` | Auto-generated |
| `FROM bi_schools_dev.t` | `FROM ${rs_bi_schools}.t` | Override (dev→prod) |

Only add to `settings.py` when you need dev and prod to share the same placeholder. For everything else, it just works.

> **Hardcoded mode**: toggle `SCHEMA MODE → Hardcoded` in the UI to keep original schema names as-is in the output — useful when deploying to an environment that already matches the source schema names.

<br/>

---

## 📋 What Gets Converted

### Redshift → Fabric: Type Mapping

| Redshift | Fabric T-SQL |
|---|---|
| `bigint`, `int8` | `BIGINT` |
| `integer`, `int4` | `INT` |
| `boolean`, `bool` | `BIT` |
| `timestamp without time zone` | `DATETIME2(6)` |
| `timestamp with time zone` | `DATETIME2(6)` ⚠️ |
| `numeric(p,s)` | `DECIMAL(p,s)` |
| `character varying(n)` | `VARCHAR(n)` |
| `character varying(65535)` | `VARCHAR(MAX)` |
| `double precision` | `FLOAT(53)` |
| `geometry` | `VARCHAR(MAX)` ⚠️ |
| `super` | `VARCHAR(MAX)` ⚠️ |
| `text` | `VARCHAR(MAX)` |

### Redshift → Fabric Warehouse: Function Mapping (T-SQL)

| Redshift | Fabric T-SQL |
|---|---|
| `NVL(a, b)` | `ISNULL(a, b)` |
| `NVL(a, b, c)` | `COALESCE(a, b, c)` |
| `COALESCE(a, b)` | `ISNULL(a, b)` |
| `DATE_TRUNC('week', x)` | `DATETRUNC(iso_week, x)` |
| `DATE_TRUNC('month', x)` | `DATETRUNC(month, x)` |
| `DATE_PART_YEAR(x)` | `DATEPART(YEAR, x)` |
| `CURRENT_DATE` | `CONVERT(DATE, GETDATE())` |
| `CAST(x AS type)` | `CONVERT(type, x)` |
| `LISTAGG(col, ',')` | `STRING_AGG(col, ',')` |
| `TRUNC(SYSDATE) - 1` | `DATEADD(DAY, -1, CAST(GETDATE() AS DATE))` |
| `CONVERT_TIMEZONE(...)` | `AT TIME ZONE` pattern |
| `INITCAP(x)` | `UPPER(x)` ⚠️ |
| `a \|\| b` | `a + b` |
| `IS TRUE / IS FALSE` | `= 1 / = 0` |
| `GROUP BY 1, 2, 3` | Expanded to column names |

### Redshift → Fabric Lakehouse: Function Mapping (Spark SQL)

| Redshift | Spark SQL |
|---|---|
| `NVL(a, b)` | `COALESCE(a, b)` |
| `ISNULL(a, b)` | `COALESCE(a, b)` |
| `x::timestamp` | `CAST(x AS TIMESTAMP)` |
| `x::int` | `CAST(x AS INT)` |
| `DATE_TRUNC('month', x)` | `DATE_TRUNC('month', x)` *(native)* |
| `DATE_PART_YEAR(x)` | `YEAR(x)` |
| `date_part('month', x)` | `EXTRACT(MONTH FROM x)` |
| `CONVERT_TIMEZONE('UTC', tz, x)` | `from_utc_timestamp(x, tz)` |
| `LISTAGG(col, ',') WITHIN GROUP (...)` | `ARRAY_JOIN(COLLECT_LIST(col), ',')` ⚠️ |
| `TRUNC(SYSDATE) - N` | `DATE_SUB(CURRENT_DATE, N)` |
| `INTERVAL '6 day'` | `INTERVAL 6 DAY` |
| `IS TRUE / IS FALSE` | `= true / = false` |
| `GROUP BY 1, 2, 3` | Expanded to column names |
| `md5()` | `md5()` *(native)* |
| `REGEXP_*` | `REGEXP_*` *(native)* |

### Redshift Clauses Automatically Stripped

`ENCODE` · `DISTKEY` · `DISTSTYLE` · `SORTKEY` · `INTERLEAVED SORTKEY` · `BACKUP NO` · `WITH NO SCHEMA BINDING`

<br/>

---

## 🚀 Quick Start

### Prerequisites

* Python 3.11 or higher
* pip (or `python -m pip`)

### Setup (Windows)

```powershell
# 1. Navigate to the project folder
cd redshift_to_fabric

# 2. Create virtual environment
python -m venv .venv

# 3. Allow scripts (if needed)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned

# 4. Activate virtual environment
.\.venv\Scripts\Activate.ps1

# 5. Install dependencies
python -m pip install -r requirements.txt

# 6. Start the server
python run.py server
```

### Setup (macOS / Linux)

```bash
cd redshift_to_fabric
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py server
```

### Open in browser

```
http://localhost:8000          ← Web UI
http://localhost:8000/docs     ← Interactive API docs
```

<br/>

---

## 💻 CLI Usage

```bash
# Run the built-in demo (no files needed)
python run.py demo

# Convert a DDL file
python run.py convert --file my_tables.sql

# Convert and save to a specific folder
python run.py convert --file my_tables.sql --out-dir ./fabric_output

# Convert inline SQL
python run.py convert --sql "CREATE TABLE sales.orders (id bigint ENCODE raw) DISTSTYLE AUTO;"

# Validate converted SQL for leftover Redshift syntax
python run.py validate --file data/outputs/my_job/combined/all_converted.sql

# Run all tests
python run.py test
```

<br/>

---

## 🔌 REST API

The same conversion engine is available as a REST API:

```bash
# Convert inline SQL (Warehouse SP, dynamic schema)
curl -X POST http://localhost:8000/api/v1/convert/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "CREATE TABLE sales.orders (id bigint ENCODE raw) DISTSTYLE AUTO;",
    "mv_target": "warehouse_sp",
    "schema_mode": "dynamic"
  }'

# Convert as Lakehouse MV with hardcoded schemas
curl -X POST http://localhost:8000/api/v1/convert/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "CREATE MATERIALIZED VIEW bi_schools.agg_orders AS SELECT ...",
    "mv_target": "lakehouse_mv",
    "schema_mode": "hardcoded"
  }'

# Upload a file (with query params)
curl -X POST "http://localhost:8000/api/v1/convert/file?mv_target=lakehouse_mv&schema_mode=dynamic" \
  -F "file=@my_views.sql"

# Validate converted SQL
curl -X POST http://localhost:8000/api/v1/convert/validate \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM ${rs_sales}.orders;"}'
```

Full interactive documentation available at `/docs` (Swagger UI) and `/redoc`.

<br/>

---

## 📁 Output Files

Every conversion job produces:

```
data/
├── outputs/<job_id>/
│   ├── combined/all_converted.sql     ← everything in one file
│   ├── tables/converted_tables.sql    ← tables only
│   └── views/converted_views.sql      ← views only
└── reports/<job_id>/
    ├── conversion_report.md           ← human-readable summary
    └── conversion_summary.json        ← machine-readable stats
```

<br/>

---

## 🏗 Project Structure

```
redshift_to_fabric/
├── run.py                    ← Entry point: convert / server / test / demo
├── requirements.txt
│
├── app/
│   ├── core/
│   │   ├── settings.py       ← All configuration & schema placeholder mappings
│   │   ├── rules.py          ← Datatype & function mapping rules
│   │   └── pipeline.py       ← Conversion orchestrator
│   ├── parser/               ← DDL parsing → intermediate representation
│   ├── transformer/
│   │   ├── view_transformer.py        ← Redshift → Fabric T-SQL (Views & Warehouse MVs)
│   │   ├── lakehouse_mv_transformer.py← Redshift → Spark SQL (Lakehouse MVs)
│   │   └── table_generator.py         ← Redshift → Fabric T-SQL (Tables)
│   ├── validator/            ← Post-conversion residual syntax checking
│   ├── output/               ← File writing & report generation
│   └── api/                  ← FastAPI routes & schemas
│
├── frontend/
│   └── index.html            ← Web UI (served at /)
│
└── tests/
    ├── unit/                 ← Unit tests
    ├── integration/          ← End-to-end pipeline tests
    └── fixtures/             ← Real Redshift DDL test inputs
```

<br/>

---

## ⚙️ Configuration

Schema placeholder mappings are automatically generated at runtime:

* Any source schema `xyz` → placeholder `${rs_xyz}`
* Any view output schema `xyz` → placeholder `${os_xyz}`

To override specific schemas (e.g. dev/prod sharing one placeholder), edit `app/core/settings.py`:

```python
schema_placeholder_map_overrides = {
    "bi_schools_dev": "${rs_bi_schools}",   # dev uses same placeholder as prod
    "sales_dev":      "${rs_sales}",
}
```

All other settings (port, upload limits, log level, output paths) can be configured via `.env`:

```env
API_PORT=8000
LOG_LEVEL=INFO
MAX_UPLOAD_SIZE_MB=50
```

<br/>

---

## 🧪 Running Tests

```bash
python run.py test                     # all tests
python run.py test --suite unit        # fast unit tests only
python run.py test --suite integration # end-to-end with real DDL fixtures
python run.py test --cov               # with coverage report
```

<br/>

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run the tests: `python run.py test`
5. Commit and push: `git commit -m "Add my feature"`
6. Open a Pull Request

Please ensure all tests pass before submitting a PR.

<br/>

---

<div align="center">

Built for enterprise SQL migration · Python + FastAPI

</div>