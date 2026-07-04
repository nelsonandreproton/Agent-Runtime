# Loads config\.env (if present) and starts the A2A gateway for the configured agents.
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

& $python -m agent_runtime.cli
