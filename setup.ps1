$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --frozen python tools/setup_deploy.py @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 tools/setup_deploy.py @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python tools/setup_deploy.py @args
} else {
    throw 'Install Python 3.9+ or uv, then run setup.ps1 again.'
}
exit $LASTEXITCODE
