$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    python run.py @args
} finally {
    Pop-Location
}
