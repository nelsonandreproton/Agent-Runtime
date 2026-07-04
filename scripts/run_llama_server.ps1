# Starts llama.cpp's llama-server with an OpenAI-compatible API and Jinja-based
# tool-calling enabled. Requires a llama.cpp build with llama-server.exe on PATH
# and a chat model that supports tool calling (e.g. Qwen2.5-Instruct,
# Llama-3.1-Instruct, Hermes-2-Pro) in GGUF format.
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ModelPath,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)
$ErrorActionPreference = "Stop"

llama-server `
    --model $ModelPath `
    --host 127.0.0.1 `
    --port 8080 `
    --jinja `
    --ctx-size 8192 `
    @ExtraArgs
