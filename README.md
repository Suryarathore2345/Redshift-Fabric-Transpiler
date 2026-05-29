# Redshift → Microsoft Fabric DDL Converter

Enterprise-grade backend for converting AWS Redshift TABLE and VIEW DDL into
parameterised Microsoft Fabric Warehouse T-SQL, with full validation, reporting,
REST API, and CLI.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Architecture Overview](#architecture-overview)
3. [Quick Start (Local Setup)](#quick-start)
4. [CLI Usage](#cli-usage)
5. [REST API Usage](#rest-api-usage)
6. [Running Tests](#running-tests)
7. [Docker](#docker)
8. [Configuration Reference](#configuration-reference)
9. [Conversion Rules](#conversion-rules)
10. [Extending the System](#extending-the-system)

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

## Architecture Overview

```
                        ┌─────────────────────────────────────────────────┐
                        │               run.py  /  FastAPI                │
                        │   CLI: convert | validate | server | test | demo│
                        └────────────────────┬────────────────────────────┘
                                             │
                                             ▼
                        ┌─────────────────────────────────────────────────┐
                        │              core/pipeline.py                   │
                        │         convert_sql(sql, filename)              │
                        └──────┬────────────────────────────┬─────────────┘
                               │                            │
               ┌───────────────▼──────┐       ┌────────────▼────────────┐
               │  parser/splitter.py  │       │  (same for each stmt)   │
               │  split_statements()  │       │                         │
               │  classify_all()      │       │                         │
               └───────────┬──────────┘       └─────────────────────────┘
                           │
          ┌────────────────┼─────────────────┐
          │                │                 │
          ▼                ▼                 ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐
  │ table_parser │ │ view_parser  │ │  (future: proc_parser│
  │ parse_table()│ │ parse_view() │ │   schema_parser)     │
  │  → TableIR  │ │  → ViewIR   │ └──────────────────────┘
  └──────┬───────┘ └──────┬───────┘
         │                │
         ▼                ▼
  ┌──────────────┐ ┌───────────────────┐
  │table_generator│ │view_transformer   │
  │generate_table│ │transform_view()   │
  │  → T-SQL    │ │  → T-SQL          │
  └──────┬───────┘ └──────┬────────────┘
         │                │
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  validator.py   │
         │ validate_result │
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  output/        │
         │  generator.py   │
         │  write_outputs  │
         └────────┬────────┘
                  ▼
          data/outputs/<job>/
          ├── tables/converted_tables.sql
          ├── views/converted_views.sql
          └── combined/all_converted.sql
          data/reports/<job>/
          ├── conversion_report.md
          └── conversion_summary.json
```

### Key Design Principles

| Principle | Implementation |
|-----------|----------------|
| **IR decoupling** | Parser → IR → Generator. Parser never emits SQL. Generator never reads raw SQL. |
| **Rule registry** | All type/function/syntax mappings centralised in `core/rules.py`. No hardcoded strings in parsers. |
| **Pipeline pattern** | `view_transformer.py` runs transformations as an ordered, independent function list. |
| **Confidence scoring** | Every object gets a `0.0–1.0` score. Warnings penalise the score. Validator adds further penalties. |
| **Idempotent output** | Tables wrapped in `IF OBJECT_ID(...) IS NULL BEGIN ... END` (matches reference repo). |
| **Parameterisation** | All schema names replaced with Flyway placeholders (`${schema}`, `${rs_bi_alefdw}`, etc.). |

---

## Quick Start

### Prerequisites

- Python 3.11+
- pip

### 1. Clone / copy the project

```bash
cd redshift_to_fabric
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# OR
.venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Copy environment config

```bash
cp .env.example .env
# Edit .env if you need to change placeholder names or directories
```

### 5. Run the built-in demo (no files needed)

```bash
python run.py demo
```

You will see a full conversion of 5 sample objects with the output printed to terminal
and files written to `data/outputs/` and `data/reports/`.

---

## CLI Usage

### Convert a real Redshift DDL file

```bash
python run.py convert --file path/to/bi_alefdw_tables.sql
```

### Convert and write to a custom output directory

```bash
python run.py convert --file bi_alefdw_tables.sql --out-dir ./my_results
```

### Convert inline SQL directly

```bash
python run.py convert --sql "
CREATE TABLE bi_alefdw.student_login (
    login_date_dw_id bigint ENCODE raw,
    school_dw_id     bigint ENCODE raw DISTKEY,
    outside_school_flag boolean ENCODE raw
) DISTSTYLE AUTO SORTKEY (school_dw_id, login_date_dw_id);
"
```

### Validate converted T-SQL for residual Redshift syntax

```bash
python run.py validate --file data/outputs/my_job/combined/all_converted.sql
```

### Start the REST API server

```bash
python run.py server
# Docs at: http://localhost:8000/docs
```

### Start with auto-reload (development)

```bash
python run.py server --port 9000 --reload
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All objects converted with high confidence |
| `1` | Partial success — some warnings or manual review items |
| `2` | All objects failed, or input file not found |

---

## REST API Usage

Start the server first:

```bash
python run.py server
```

### Convert inline SQL

```bash
curl -X POST http://localhost:8000/api/v1/convert/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "CREATE TABLE bi_alefdw.scaffold (key integer ENCODE az64) DISTSTYLE AUTO;",
    "source_filename": "test.sql"
  }'
```

### Upload and convert a file

```bash
curl -X POST http://localhost:8000/api/v1/convert/file \
  -F "file=@bi_alefdw_tables.sql"
```

### Validate converted SQL

```bash
curl -X POST http://localhost:8000/api/v1/convert/validate \
  -H "Content-Type: application/json" \
  -d '{"sql": "CREATE TABLE ${schema}.foo (id BIGINT) DISTSTYLE AUTO;"}'
```

### List all conversion reports

```bash
curl http://localhost:8000/api/v1/reports/
```

### Fetch a specific JSON report

```bash
curl http://localhost:8000/api/v1/reports/20240101_120000_abc12345
```

### Interactive API docs (Swagger UI)

Open in browser: **http://localhost:8000/docs**

---

## Running Tests

### Run all tests

```bash
python run.py test
# OR directly:
pytest tests/ -v
```

### Run only unit tests (fast, no file I/O)

```bash
python run.py test --suite unit
```

### Run only integration tests (uses real fixture files)

```bash
python run.py test --suite integration
```

### Run with coverage report

```bash
python run.py test --cov
# Opens HTML report at htmlcov/index.html
```

### Run a specific test by name

```bash
python run.py test -k "test_boolean_maps_to_bit"
```

### Run pytest directly with more options

```bash
pytest tests/unit/test_table_parser.py -v --tb=long
pytest tests/ -v -k "not slow"
```

### Expected test results

```
tests/unit/test_splitter.py         .... 9 tests
tests/unit/test_table_parser.py     .... 17 tests
tests/unit/test_table_generator.py  .... 12 tests
tests/unit/test_view_transformer.py .... 36 tests
tests/integration/...               .... 25 tests
─────────────────────────────────────────────────
Total: 74+ tests  |  Expected: all pass
```

---

## Docker

### Build and run with Docker Compose

```bash
cd docker
docker-compose up --build
```

API will be available at **http://localhost:8000**

### Build standalone image

```bash
docker build -f docker/Dockerfile -t redshift-fabric-converter .
docker run -p 8000:8000 redshift-fabric-converter
```

---

## Configuration Reference

All settings are in `app/core/settings.py` and can be overridden via `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level |
| `API_PORT` | `8000` | Server port |
| `UPLOAD_DIR` | `data/uploads` | Where uploaded files are stored |
| `OUTPUT_DIR` | `data/outputs` | Where converted SQL files go |
| `REPORTS_DIR` | `data/reports` | Where MD + JSON reports go |
| `MAX_UPLOAD_SIZE_MB` | `50` | Upload file size limit |
| `FLYWAY_SCHEMA_PLACEHOLDER` | `${schema}` | Schema placeholder in TABLE DDL |
| `OUTPUT_SCHEMA_PLACEHOLDER` | `${os_bi_alefdw}` | Schema placeholder in VIEW DDL |
| `STRIP_TABLE_SUFFIXES_IN_VIEWS` | `true` | Strip `_mv`/`_view` from table refs |

### Schema placeholder mapping

Edit `schema_placeholder_map` in `settings.py` to add new source→target schema mappings:

```python
schema_placeholder_map = {
    "bi_alefdw":     "${rs_bi_alefdw}",
    "bi_alefdw_dev": "${rs_bi_alefdw}",
    "alefdw":        "${rs_alefdw}",
}
```

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