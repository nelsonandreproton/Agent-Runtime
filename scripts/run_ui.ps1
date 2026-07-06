# Loads config\.env (if present) and starts the local admin UI (Agents, MCP
# Servers, Logs tabs). Binds to 127.0.0.1 only, hardcoded in ui_cli.py -- this
# must never be exposed via ngrok/Cloudflare Tunnel/any public tunnel.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$envFile = "config\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $key, $value = $line -split "=", 2
        if ($null -ne $value) {
            Set-Item -Path "Env:$($key.Trim())" -Value $value.Trim()
        }
    }
}

$venvPython = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python -m agent_runtime.ui_cli
