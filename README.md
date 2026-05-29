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

## Project Structure

```
redshift_to_fabric/
│
├── run.py                          ← CLI entry point (convert / validate / server / test / demo)
├── requirements.txt
├── pyproject.toml
├── pytest.ini
├── .env.example                    ← copy to .env and edit
│
├── app/                            ← Python package (all backend logic)
│   ├── __init__.py
│   │
│   ├── core/                       ← Domain models, pipeline, settings, rules
│   │   ├── __init__.py
│   │   ├── models.py               ← IR dataclasses: TableIR, ViewIR, ColumnIR, ConversionResult
│   │   ├── pipeline.py             ← Master orchestrator: convert_sql()
│   │   ├── rules.py                ← Rule registry: DATATYPE_MAP, FUNCTION_MAP, BOOLEAN_REWRITES
│   │   └── settings.py             ← Pydantic Settings: placeholders, paths, thresholds
│   │
│   ├── parser/                     ← DDL parsing layer
│   │   ├── __init__.py
│   │   ├── splitter.py             ← Split + classify multi-statement SQL files
│   │   ├── table_parser.py         ← CREATE TABLE → TableIR
│   │   └── view_parser.py          ← CREATE [MATERIALIZED] VIEW → ViewIR
│   │
│   ├── transformer/                ← Code generation layer
│   │   ├── __init__.py
│   │   ├── table_generator.py      ← TableIR → Fabric T-SQL CREATE TABLE
│   │   └── view_transformer.py     ← ViewIR → Fabric T-SQL CREATE OR ALTER VIEW
│   │
│   ├── validator/                  ← Post-conversion validation
│   │   ├── __init__.py
│   │   └── validator.py            ← Residual Redshift syntax detection + confidence scoring
│   │
│   ├── reporter/                   ← Report building
│   │   ├── __init__.py
│   │   └── reporter.py             ← ConversionReport: rule stats, warning aggregation
│   │
│   ├── output/                     ← File output
│   │   ├── __init__.py
│   │   └── generator.py            ← Write .sql + .md + .json output files
│   │
│   ├── logging/                    ← Structured logging
│   │   ├── __init__.py
│   │   └── logger.py               ← structlog configuration
│   │
│   └── api/                        ← FastAPI REST layer
│       ├── __init__.py
│       ├── app.py                  ← Application factory: create_app()
│       ├── schemas.py              ← Pydantic v2 request/response schemas
│       └── routes/
│           ├── __init__.py
│           ├── health.py           ← GET /api/v1/health
│           ├── convert.py          ← POST /api/v1/convert/sql|file|validate, GET /download
│           └── reports.py          ← GET /api/v1/reports/
│
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_splitter.py        ← 9 tests: statement splitting + classification
│   │   ├── test_table_parser.py    ← 17 tests: type mapping, ENCODE strip, distkey, sortkey
│   │   ├── test_table_generator.py ← 12 tests: idempotent DDL, placeholders, bracket quoting
│   │   └── test_view_transformer.py ← 36 tests: schema refs, boolean, NVL, DATE_TRUNC, LISTAGG, etc.
│   │
│   ├── integration/
│   │   ├── __init__.py
│   │   └── test_pipeline_integration.py ← end-to-end using real bi_alefdw_tables.sql
│   │
│   └── fixtures/
│       ├── input/
│       │   ├── bi_alefdw_tables.sql    ← Real Redshift source DDL (29 tables)
│       │   └── sample_views.sql        ← Redshift view fixtures (3 views)
│       └── output/                     ← Expected output snapshots (add as needed)
│
├── config/                         ← YAML rule overrides (future: externalise DATATYPE_MAP)
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── scripts/                        ← Utility scripts (batch conversion, CI helpers)
│
├── docs/                           ← Architecture docs, ADRs
│
└── data/                           ← Runtime data (git-ignored)
    ├── uploads/                    ← Uploaded SQL files
    ├── outputs/                    ← Generated Fabric T-SQL + combined files
    ├── reports/                    ← Markdown + JSON conversion reports
    └── logs/                       ← Structured JSON logs
```

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

## Conversion Rules

### Datatype mapping

| Redshift Type | Fabric T-SQL | Notes |
|---------------|-------------|-------|
| `bigint` / `int8` | `BIGINT` | Direct |
| `integer` / `int4` | `INT` | Direct |
| `smallint` / `int2` | `SMALLINT` | Direct |
| `double precision` / `float8` | `FLOAT(53)` | Direct |
| `numeric(p,s)` / `decimal(p,s)` | `DECIMAL(p,s)` | Precision preserved |
| `character varying(n)` / `varchar(n)` | `VARCHAR(n)` | Direct |
| `character varying(65535)` or `> 8000` | `VARCHAR(MAX)` | Threshold-based |
| `boolean` / `bool` | `BIT` | Direct |
| `timestamp without time zone` | `DATETIME2(6)` | Direct |
| `timestamp with time zone` | `DATETIME2(6)` | ⚠️ Timezone stripped |
| `date` | `DATE` | Direct |
| `text` | `VARCHAR(MAX)` | ⚠️ Warning issued |
| `geometry` | `VARCHAR(MAX)` | ⚠️ No spatial support |
| `super` | `VARCHAR(MAX)` | ⚠️ JSON as string |
| `varbyte` | `VARBINARY(MAX)` | ⚠️ Warning issued |
| `hllsketch` | `VARCHAR(MAX)` | 🔍 Manual review |

### Function mapping

| Redshift | Fabric T-SQL | Confidence |
|----------|-------------|-----------|
| `NVL(x,y)` | `ISNULL(x,y)` | ✅ High |
| `DATE_TRUNC('week', e)` | `DATETRUNC(iso_week, e)` | ✅ High |
| `DATE_TRUNC('month', e)` | `DATETRUNC(month, e)` | ✅ High |
| `CURRENT_DATE` | `CONVERT(DATE, GETDATE())` | ✅ High |
| `LISTAGG(col,',')` | `STRING_AGG(col,',')` | ⚠️ DISTINCT unsupported |
| `DECODE(e,v,r,…)` | `CASE WHEN …` | ⚠️ Review NULL semantics |
| `IS TRUE / IS FALSE` | `= 1 / = 0` | ✅ High |
| `expr::date` | `CONVERT(DATE, expr)` | ✅ High |
| `date(expr)` | `CONVERT(DATE, expr)` | ✅ High |
| `GETDATE()` | `GETDATE()` | ✅ High |
| `DATEADD()` / `DATEDIFF()` | Same | ✅ High |
| `REGEXP_*` | — | 🔍 Manual (unsupported) |
| `QUALIFY` | Subquery pattern | ⚠️ Manual rewrite |

### Redshift clauses stripped (no Fabric equivalent)

- `ENCODE az64 / lzo / raw / bytedict / zstd`
- `DISTSTYLE AUTO / KEY / ALL / EVEN`
- `DISTKEY(column)`
- `SORTKEY(columns)` / `COMPOUND SORTKEY` / `INTERLEAVED SORTKEY`
- `BACKUP NO / YES`
- `WITH NO SCHEMA BINDING`

### Materialised views

Converted to a stored procedure + CTAS refresh pattern:

```sql
CREATE OR ALTER PROCEDURE ${os_bi_alefdw}.usp_refresh_<name> AS
BEGIN
    DROP TABLE IF EXISTS ${os_bi_alefdw}.<name>_staging;
    CREATE TABLE ${os_bi_alefdw}.<name>_staging AS <original SELECT>;
    DROP TABLE IF EXISTS ${os_bi_alefdw}.<name>;
    EXEC sp_rename '<name>_staging', '<name>';
END;
```

---

## Extending the System

### Add a new source dialect (e.g. Snowflake)

1. Create `app/parser/snowflake_parser.py` implementing `parse_table()` and `parse_view()` returning the same `TableIR` / `ViewIR` models.
2. Create `app/transformer/snowflake_transformer.py` if Snowflake → Fabric needs different rules.
3. Add a `dialect` parameter to `convert_sql()` in `pipeline.py` to route to the right parser.
4. Add Snowflake-specific entries to `rules.py`.

No changes needed to the validator, reporter, output generator, or API.

### Add a new transformation rule

1. Add the function mapping to `FUNCTION_MAP` in `core/rules.py`.
2. Add a transformation function in `view_transformer.py` following the `(sql) → (sql, warnings)` signature.
3. Add it to the `pipeline` list inside `transform_view()`.
4. Add a unit test in `tests/unit/test_view_transformer.py`.

### Add a new datatype mapping

Add the entry to `DATATYPE_MAP` in `core/rules.py` — the table parser reads it automatically.
