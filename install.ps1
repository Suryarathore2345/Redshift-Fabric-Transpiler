# install.ps1
# -----------------------------------------------------------------------
# One-shot setup for Redshift -> Fabric DDL Converter on Windows
# Works with Python 3.11 / 3.12 / 3.13 / 3.14
#
# HOW TO RUN (from the redshift_to_fabric folder):
#   1. Right-click PowerShell -> "Run as Administrator" (first time only)
#      OR run this first to allow local scripts:
#      Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
#   2. .\install.ps1
# -----------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Redshift -> Fabric DDL Converter  |  Windows Setup"   -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Locate Python ─────────────────────────────────────────────
Write-Host "[1/4] Locating Python..." -ForegroundColor Yellow

$PythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3\.(1[1-9]|[2-9]\d)") {
            $PythonCmd = $candidate
            Write-Host "      Found: $ver  ($candidate)" -ForegroundColor Green
            break
        }
    } catch { }
}

if (-not $PythonCmd) {
    Write-Host ""
    Write-Host "  ERROR: Python 3.11+ not found." -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "  Make sure to tick 'Add Python to PATH' during install." -ForegroundColor Red
    exit 1
}

# ── Step 2: Create virtual environment ───────────────────────────────
Write-Host "[2/4] Creating virtual environment (.venv)..." -ForegroundColor Yellow

$VenvPath = Join-Path $ProjectRoot ".venv"
if (Test-Path $VenvPath) {
    Write-Host "      .venv already exists, skipping creation." -ForegroundColor Gray
} else {
    & $PythonCmd -m venv .venv
    Write-Host "      .venv created." -ForegroundColor Green
}

# ── Step 3: Upgrade pip inside the venv ──────────────────────────────
Write-Host "[3/4] Upgrading pip..." -ForegroundColor Yellow

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip --quiet
Write-Host "      pip upgraded." -ForegroundColor Green

# ── Step 4: Install all dependencies ─────────────────────────────────
Write-Host "[4/4] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "      (This may take 1-2 minutes on first run)" -ForegroundColor Gray
Write-Host ""

& $VenvPython -m pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  ERROR: pip install failed. See output above." -ForegroundColor Red
    exit 1
}

# ── Done ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  HOW TO USE (run these in any new terminal):" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Step 1 - Activate the virtual environment:" -ForegroundColor White
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Step 2 - Run commands:" -ForegroundColor White
Write-Host "    python run.py demo" -ForegroundColor Yellow
Write-Host "    python run.py convert --file bi_alefdw_tables.sql" -ForegroundColor Yellow
Write-Host "    python run.py server" -ForegroundColor Yellow
Write-Host "    python run.py test" -ForegroundColor Yellow
Write-Host ""
Write-Host "  OR skip activation and call venv Python directly:" -ForegroundColor White
Write-Host "    .\.venv\Scripts\python.exe run.py demo" -ForegroundColor Yellow
Write-Host "    .\.venv\Scripts\python.exe run.py convert --file bi_alefdw_tables.sql" -ForegroundColor Yellow
Write-Host "    .\.venv\Scripts\python.exe run.py server" -ForegroundColor Yellow
Write-Host ""

# ── Auto-verify the install worked ───────────────────────────────────
Write-Host "  Verifying install..." -ForegroundColor Gray
$check = & $VenvPython -c "import fastapi, pydantic, structlog; print('OK')" 2>&1
if ($check -eq "OK") {
    Write-Host "  All packages verified. Ready to use." -ForegroundColor Green
} else {
    Write-Host "  WARNING: Verification failed: $check" -ForegroundColor Red
}
Write-Host ""
