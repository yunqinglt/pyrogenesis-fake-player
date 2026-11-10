param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$bundleRoot = $PSScriptRoot
$venvRoot = Join-Path $bundleRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

& $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required')"
if ($LASTEXITCODE -ne 0) { throw "The selected Python cannot run this server." }

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $venvRoot) {
        throw "An incomplete .venv exists. Select a fresh bundle directory or repair it explicitly."
    }
    & $PythonExe -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create the bundle virtual environment." }
}

& $venvPython -m pip install --disable-pip-version-check --no-input --constraint (Join-Path $bundleRoot "constraints-tested.txt") $bundleRoot
if ($LASTEXITCODE -ne 0) { throw "Package installation failed. Check the network and package index." }
& $venvPython (Join-Path $bundleRoot "check_portable.py")
if ($LASTEXITCODE -ne 0) { throw "The installed server did not pass its non-game self-check." }

Write-Output "Installed. Configure the MCP client with the absolute path to $venvPython."
