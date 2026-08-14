# Block Captain Meshtastic App — first-time setup (Windows)
#
# Usage (run these as TWO separate commands if scripts are blocked):
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#   .\setup.ps1
#
# Or run once without changing policy:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "=== Block Captain Meshtastic Setup ==="

function Get-PythonCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @("python3")
    }

    Write-Error "ERROR: Python 3 is required but not found. Install from https://www.python.org/downloads/ and enable 'Add Python to PATH'."
    exit 1
}

function Invoke-Python {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$PythonArgs
    )

    & $script:Py @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$script:Py = Get-PythonCommand
$pyVersion = (& $script:Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
Write-Host "Python version: $pyVersion"

$venvDir = Join-Path $PSScriptRoot ".venv"
$activateScript = Join-Path $venvDir "Scripts\Activate.ps1"

if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment in .venv ..."
    Invoke-Python -m venv $venvDir
}

if (-not (Test-Path $activateScript)) {
    Write-Error "Virtual environment activation script not found at $activateScript"
    exit 1
}

. $activateScript

Write-Host "Installing dependencies ..."
Invoke-Python -m pip install --upgrade pip
Invoke-Python -m pip install -r requirements.txt

Write-Host "Initializing neighborhood status CSV ..."
Invoke-Python -c "from csv_store import init_csv, ensure_default_houses; init_csv(); added = ensure_default_houses(); print('  ->', init_csv()); print(f'  -> added {added} default houses')"

Write-Host "Initializing local house addresses (UI only) ..."
Invoke-Python -c "from address_store import init_addresses, ensure_default_addresses; init_addresses(); added = ensure_default_addresses(); print('  ->', init_addresses()); print(f'  -> added {added} default addresses')"

Write-Host ""
Write-Host "Serial port scan:"
Invoke-Python -c @"
try:
    from meshtastic_client import list_serial_ports
    ports = list_serial_ports()
    if ports:
        for p in ports:
            print(f'  Found: {p}')
    else:
        print('  No Meshtastic/serial ports detected (mock mode will be used).')
except Exception as exc:
    print(f'  Port scan skipped: {exc}')
"@

Write-Host ""
Write-Host "Bluetooth: pair the radio in the OS first, then use Radio settings -> Bluetooth in the app."
Write-Host "  CLI check: meshtastic --ble-scan"

Write-Host ""
Write-Host "Testing Meshtastic connection fallback ..."
Invoke-Python -c @"
from meshtastic_client import MeshtasticClient

client = MeshtasticClient()
info = client.connect()
print(f'  Connected: {info.connected}')
print(f'  Mock mode: {info.mock_mode}')
print(f'  Message:   {info.message}')

ok, msg = client.send_text('NS:H001:G')
print(f'  Test send: ok={ok}, {msg}')
client.close()
print('  Fallback test passed.')
"@

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host ""
Write-Host "Start the app:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  streamlit run app.py"
Write-Host ""
Write-Host "Open the URL shown (usually http://localhost:8501)"
Write-Host ""
Write-Host "If script execution is blocked, run these as TWO separate commands:"
Write-Host "  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
Write-Host "  .\setup.ps1"
Write-Host ""
Write-Host "Or run setup once without changing policy:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\setup.ps1"
