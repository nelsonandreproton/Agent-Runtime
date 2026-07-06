# Starts the full local stack as three tabs in one Windows Terminal window
# (LLM, NGROK, GATEWAY): llama.cpp's local LLM, the ngrok tunnel (static
# domain), and the A2A gateway, in that order, waiting for each to be ready
# before starting the next. Requires Windows Terminal (wt.exe) on PATH.
#
# Edit the constants below to match your setup (model, ngrok domain, llama.cpp
# install path) before running.
#
# ASCII only in this file, deliberately: Windows PowerShell 5.1 reading a
# non-BOM UTF-8 file under a non-UTF-8 console codepage can misdecode
# multi-byte characters (e.g. an em dash) mid-string, breaking string
# termination with a "missing terminator" parse error that only appears when
# run for real, not from a pre-parsed AST check.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command wt.exe -ErrorAction SilentlyContinue)) {
    throw "Windows Terminal (wt.exe) was not found on PATH. Install it from the Microsoft Store, or run llama.cpp/ngrok/the gateway manually per the README."
}

# --- Configuration -----------------------------------------------------

$LlamaExe    = "C:\Tools\llama.cpp\llama.exe"
$LlamaModel  = "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
$LlamaHost   = "127.0.0.1"
$LlamaPort   = 8080

$NgrokDomain = "author-dolphin-lugged.ngrok-free.dev"
$GatewayPort = 9000

# --- 1. llama.cpp local LLM ---------------------------------------------

Write-Host "Opening LLM tab (model: $LlamaModel)..."
$llamaCommand = "& `"$LlamaExe`" serve -hf $LlamaModel -ngl 99 --parallel 1 --ctx-size 32768 --jinja"
wt.exe new-tab --title "LLM" powershell -NoExit -Command $llamaCommand

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
        # Not up yet (or still loading the model) - keep polling.
    }
    Start-Sleep -Seconds 2
}
if (-not $llamaReady) {
    Write-Warning "llama.cpp did not report healthy within 4 minutes - continuing anyway. Check the LLM tab for errors (e.g. still downloading/loading a large model)."
} else {
    Write-Host "llama.cpp is ready."
}

# --- 2. ngrok tunnel (static domain) ------------------------------------

Write-Host "Opening NGROK tab (https://$NgrokDomain -> localhost:$GatewayPort)..."
# -w 0 targets the most-recently-used wt window, so this lands as a new tab
# in the same window the LLM tab opened above, instead of a second window.
wt.exe -w 0 new-tab --title "NGROK" powershell -NoExit -Command "ngrok http --url=$NgrokDomain $GatewayPort"

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
        # ngrok's local API not up yet - keep polling.
    }
    Start-Sleep -Seconds 1
}
if (-not $ngrokReady) {
    Write-Warning "Could not confirm the ngrok tunnel is up within 30s - continuing anyway. Check the NGROK tab."
} else {
    Write-Host "ngrok tunnel is up at https://$NgrokDomain"
}

# --- 3. A2A gateway ------------------------------------------------------

Write-Host "Opening GATEWAY tab..."
Write-Host "Reminder: config\.env's AGENT_RUNTIME_PUBLIC_URL must be https://$NgrokDomain/ - the Agent Card is only built once at startup, so a mismatch here means ODC's Test Connection will fail even though Get Details succeeds."
# wt.exe treats an unescaped ';' as its OWN command separator (chaining
# another wt subcommand), not as part of the -Command argument being passed
# through to powershell -- a literal ';' in $gatewayCommand below was
# splitting this into two wt invocations, opening a stray extra tab and
# truncating the real command. Avoid the problem entirely by calling
# run_gateway.ps1 directly instead of chaining Set-Location + it with ';'.
wt.exe -w 0 new-tab --title "GATEWAY" powershell -NoExit -Command "& `"$PSScriptRoot\run_gateway.ps1`""

Write-Host "All three processes started as tabs (LLM, NGROK, GATEWAY) in one Windows Terminal window. Close/Ctrl+C each tab individually to stop it."
