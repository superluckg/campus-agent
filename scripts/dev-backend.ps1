[CmdletBinding()]
param(
    [string]$PythonPath = "E:\Anaconda\envs\agent\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$envFile = Join-Path $projectRoot ".env"

foreach ($requiredPath in @($pythonPath, $envFile, $backendRoot)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required development path is missing: $requiredPath"
    }
}

function Invoke-EnvPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PythonArguments,

        [Parameter(Mandatory = $true)]
        [string]$StepName
    )

    & $pythonPath -m dotenv -f $envFile run -- $pythonPath @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE"
    }
}

Write-Host "[backend] Project root: $projectRoot"
Write-Host "[backend] Python: $pythonPath"
Write-Host "[backend] Checking development dependencies..."
& $pythonPath -c "import dotenv, sqlalchemy, uvicorn"
if ($LASTEXITCODE -ne 0) {
    throw "The Python environment at $pythonPath is missing SQLAlchemy, python-dotenv, or uvicorn."
}

Push-Location $backendRoot
try {
    Write-Host "[backend] Initializing or validating the database structure..."
    Invoke-EnvPython `
        -PythonArguments @("-m", "app.db.initialize") `
        -StepName "Database initialization"

    Write-Host "[backend] Starting http://127.0.0.1:8000 with Python auto-reload"
    & $pythonPath -m dotenv -f $envFile run -- `
        $pythonPath -m uvicorn app.main:app `
        --host 127.0.0.1 `
        --port 8000 `
        --reload

    if ($LASTEXITCODE -notin @(0, 130)) {
        throw "Backend development server exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
