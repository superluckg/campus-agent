[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$packageJson = Join-Path $frontendRoot "package.json"
$viteCommand = Join-Path $frontendRoot "node_modules\.bin\vite.cmd"

if (-not (Test-Path -LiteralPath $packageJson)) {
    throw "Frontend package.json was not found: $packageJson"
}

$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($null -eq $node -or $null -eq $npm) {
    throw "Node.js or npm was not found. Prepare Node 22 LTS first."
}

if (-not (Test-Path -LiteralPath $viteCommand)) {
    throw "Frontend dependencies are missing. Run npm install in frontend first."
}

Write-Host "[frontend] Project root: $frontendRoot"
Write-Host "[frontend] Node: $($node.Source)"
Write-Host "[frontend] Starting http://localhost:3000 with HMR"

Push-Location $frontendRoot
try {
    & $npm.Source run dev
    if ($LASTEXITCODE -notin @(0, 130)) {
        throw "Frontend development server exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
