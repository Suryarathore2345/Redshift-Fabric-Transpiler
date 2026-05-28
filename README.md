<div align="center">

<br/>

<!-- Hero Banner using shields.io badges as visual accent -->
<img src="https://img.shields.io/badge/AWS%20Redshift-%23FF9900?style=for-the-badge&logo=amazonaws&logoColor=white" alt="AWS Redshift"/>
&nbsp;&nbsp;
<img src="https://img.shields.io/badge/%E2%86%92-2d2d2d?style=for-the-badge" alt="→"/>
&nbsp;&nbsp;
<img src="https://img.shields.io/badge/Microsoft%20Fabric-%230078D4?style=for-the-badge&logo=microsoft&logoColor=white" alt="Microsoft Fabric"/>
 
<br/><br/>

# Redshift → Fabric DDL Converter

**Enterprise-grade SQL migration tool that converts AWS Redshift DDL into production-ready Microsoft Fabric T-SQL.**

Upload a `.sql` file or paste DDL directly — get parameterised Fabric T-SQL with full diagnostics, inline warnings, and downloadable output in seconds.

<br/>

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-74%20passing-brightgreen?style=flat-square&logo=pytest&logoColor=white)](tests/)
[![License](https://img.shields.io/badge/License-MIT-purple?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/Version-v10-orange?style=flat-square)](CHANGELOG.md)

<br/>

</div>

---

## ✨ What It Does

Migrating from **AWS Redshift** to **Microsoft Fabric** requires rewriting every `CREATE TABLE` and `CREATE VIEW` — Redshift uses PostgreSQL-dialect SQL with proprietary storage hints, while Fabric uses T-SQL with its own function names, type system, and syntax rules.

This tool automates that conversion entirely:

- 📄 &nbsp;**Paste DDL or upload a `.sql` / `.txt` file** — both work the same way
- ⚡ &nbsp;**Converts Tables, Views, and Materialised Views** in a single pass
- 🏷 &nbsp;**Injects Flyway-compatible placeholders** like `${schema}`, `${rs_sales}`, `${os_reporting}` automatically — no config needed for new schemas
- ⚠️ &nbsp;**Inline warnings on every affected line** — you see exactly what needs review, right in the SQL
- 📊 &nbsp;**Confidence scoring per object** — HIGH CONFIDENCE / PARTIAL / MANUAL REVIEW / FAILED
- 📥 &nbsp;**Download converted SQL and Markdown reports** from the UI

<br/>

---

## 🖥 Web UI

Start the server and open `http://localhost:8000` in your browser.

```
┌─────────────────────────────────────────────────────────┐
│  ✏️  PASTE SQL          📁  UPLOAD FILE                  │
│  ─────────────────      ─────────────────────────────── │
│  [ Paste Redshift    ]  [ Drop .sql / .txt here       ] │
│  [ DDL here...       ]  [ or click to browse          ] │
│                                                         │
│              [ ⚡ Convert to Fabric T-SQL ]              │
└─────────────────────────────────────────────────────────┘
```

After conversion, results appear as expandable cards — one per object — showing the converted T-SQL with syntax highlighting, inline warnings, and applied transformation rules. All output is downloadable.

<br/>

---

## 🔄 Conversion Examples

### Table — `CREATE TABLE`

**Input (Redshift):**

```sql
CREATE TABLE ST_Details.student_login_Logs (
    login_date_id      bigint ENCODE raw,
    school_id          bigint ENCODE raw DISTKEY,
    outside_school_flag   boolean ENCODE raw,
    login_time timestamp without time zone ENCODE az64
) DISTSTYLE AUTO SORTKEY (school_id, login_date_id);
```

**Output (Fabric T-SQL):**

```sql
-- ══════════════════════════════════════════════════════════════════
-- TABLE  : ST_Details.student_login_Logs
-- Target : ${schema}.student_login_Logs
-- Status : ✅ HIGH_CONFIDENCE  |  Confidence: 100%
-- Warnings: 0  ← clean conversion
-- ══════════════════════════════════════════════════════════════════
IF OBJECT_ID('${schema}.student_login_Logs', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.student_login_Logs (
        login_date_id      BIGINT,
        school_id          BIGINT,
        outside_school_flag   BIT,
        login_time DATETIME2(6)
    );
END;
```

> ENCODE, DISTKEY, DISTSTYLE, SORTKEY stripped · `boolean → BIT` · `timestamp → DATETIME2(6)` · Idempotent `IF OBJECT_ID` wrapper added · Schema parameterised as `${schema}`

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

> `CREATE OR REPLACE VIEW → CREATE OR ALTER VIEW` · `DATE_TRUNC → DATETRUNC` · `NVL → ISNULL` · `COALESCE(a,b) → ISNULL(a,b)` · `GROUP BY 1,2,3 → explicit columns` · All schema names auto-parameterised · Table aliases (`p`, `c`, `cl`) preserved correctly

<br/>

---

## 🗺 Automatic Schema Parameterisation

One of the most powerful features: **any schema name is automatically converted to a Flyway placeholder** with zero configuration.

| Redshift SQL | Fabric Output | How |
|---|---|---|
| `FROM insurance.policy` | `FROM ${rs_insurance}.policy` | Auto-generated |
| `FROM sales.orders` | `FROM ${rs_sales}.orders` | Auto-generated |
| `CREATE VIEW reporting.v_foo` | `CREATE OR ALTER VIEW ${os_reporting}.v_foo` | Auto-generated |
| `FROM ST_Details.t` | `FROM ${rs_bi_alefdw}.t` | Override (dev→prod) |

Only add to `settings.py` when you need **dev and prod to share the same placeholder**. For everything else, it just works.

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

### Redshift → Fabric: Function Mapping

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

### Redshift Clauses Automatically Stripped

`ENCODE` · `DISTKEY` · `DISTSTYLE` · `SORTKEY` · `INTERLEAVED SORTKEY` · `BACKUP NO` · `WITH NO SCHEMA BINDING`

### Materialised Views

Converted to a **stored procedure + CTAS refresh pattern** since Fabric Warehouse does not support materialised views natively:

```sql
CREATE OR ALTER PROCEDURE ${os_bi_alefdw}.usp_refresh_agg_login AS
BEGIN
    DROP TABLE IF EXISTS ${os_bi_alefdw}.agg_login_staging;
    CREATE TABLE ${os_bi_alefdw}.agg_login_staging AS <original SELECT>;
    DROP TABLE IF EXISTS ${os_bi_alefdw}.agg_login;
    EXEC sp_rename 'agg_login_staging', 'agg_login';
END;
```

<br/>

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- pip (or `python -m pip`)

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
python run.py convert --file bi_alefdw_tables.sql

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
# Convert inline SQL
curl -X POST http://localhost:8000/api/v1/convert/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "CREATE TABLE sales.orders (id bigint ENCODE raw) DISTSTYLE AUTO;"}'

# Upload a file
curl -X POST http://localhost:8000/api/v1/convert/file \
  -F "file=@bi_alefdw_tables.sql"

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
│   ├── transformer/          ← Redshift → Fabric T-SQL transformation
│   ├── validator/            ← Post-conversion residual syntax checking
│   ├── output/               ← File writing & report generation
│   └── api/                  ← FastAPI routes & schemas
│
├── frontend/
│   └── index.html            ← Web UI (served at /)
│
└── tests/
    ├── unit/                 ← 74 unit tests
    ├── integration/          ← End-to-end pipeline tests
    └── fixtures/             ← Real Redshift DDL test inputs
```

<br/>

---

## ⚙️ Configuration

Schema placeholder mappings are **automatically generated** at runtime:

- Any source schema `xyz` → placeholder `${rs_xyz}`  
- Any view output schema `xyz` → placeholder `${os_xyz}`

To override specific schemas (e.g. dev/prod sharing one placeholder), edit `app/core/settings.py`:

```python
schema_placeholder_map_overrides = {
    "ST_DETAILS_DEV": "${rs_ST_DETAILS}",   # dev uses same placeholder as prod
    "TR_DETAILS":    "${rs_TR_DETAILS}",
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
python run.py test                     # all 74 tests
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

Please ensure all 74 tests pass before submitting a PR.

<br/>

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

<br/>

---

<div align="center">

Built for enterprise SQL migration · Python + FastAPI · MIT License

</div>