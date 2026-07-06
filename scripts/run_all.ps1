# Starts the full local stack in three separate windows: llama.cpp's local
# LLM, the ngrok tunnel (static domain), and the A2A gateway — in that order,
# waiting for each to be ready before starting the next.
#
# Edit the constants below to match your setup (model, ngrok domain, llama.cpp
# install path) before running.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# --- Configuration -----------------------------------------------------

$LlamaExe    = "C:\Tools\llama.cpp\llama.exe"
$LlamaModel  = "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
$LlamaHost   = "127.0.0.1"
$LlamaPort   = 8080

$NgrokDomain = "author-dolphin-lugged.ngrok-free.dev"
$GatewayPort = 9000

# --- 1. llama.cpp local LLM ---------------------------------------------

Write-Host "Starting llama.cpp (model: $LlamaModel)..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "& `"$LlamaExe`" serve -hf $LlamaModel -ngl 99 --parallel 1 --ctx-size 32768 --jinja"
)

Write-Host "Waiting for llama.cpp to become ready on http://${LlamaHost}:${LlamaPort}/health ..."
$llamaReady = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://${LlamaHost}:${LlamaPort}/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $llamaReady = $true
            break
        }
    } catch {
        # Not up yet (or still loading the model) — keep polling.
    }
    Start-Sleep -Seconds 2
}
if (-not $llamaReady) {
    Write-Warning "llama.cpp did not report healthy within 4 minutes — continuing anyway. Check its window for errors (e.g. still downloading/loading a large model)."
} else {
    Write-Host "llama.cpp is ready."
}

# --- 2. ngrok tunnel (static domain) ------------------------------------

Write-Host "Starting ngrok tunnel (https://$NgrokDomain -> localhost:$GatewayPort)..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "ngrok http --url=$NgrokDomain $GatewayPort"
)

Write-Host "Waiting for ngrok's local API to confirm the tunnel is up..."
$ngrokReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
        if ($tunnels.tunnels | Where-Object { $_.public_url -like "*$NgrokDomain*" }) {
            $ngrokReady = $true
            break
        }
    } catch {
        # ngrok's local API not up yet — keep polling.
    }
    Start-Sleep -Seconds 1
}
if (-not $ngrokReady) {
    Write-Warning "Could not confirm the ngrok tunnel is up within 30s — continuing anyway. Check its window."
} else {
    Write-Host "ngrok tunnel is up at https://$NgrokDomain"
}

# --- 3. A2A gateway ------------------------------------------------------

Write-Host "Starting the A2A gateway..."
Write-Host "Reminder: config\.env's AGENT_RUNTIME_PUBLIC_URL must be https://$NgrokDomain/ — the Agent Card is only built once at startup, so a mismatch here means ODC's 'Test Connection' will fail even though 'Get Details' succeeds."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location `"$PSScriptRoot`"; .\run_gateway.ps1"
)

Write-Host "All three processes started in separate windows. Close/Ctrl+C each window individually to stop it."
