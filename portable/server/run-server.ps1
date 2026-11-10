$ErrorActionPreference = "Stop"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    [Console]::Error.WriteLine("Run install.ps1 before starting this server.")
    exit 1
}
& $venvPython -m pyrogenesis_mcp.server
exit $LASTEXITCODE
