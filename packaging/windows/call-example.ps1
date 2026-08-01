param(
    [string]$Text = "你好，世界",
    [string]$Voice = "zh-CN-XiaoxiaoNeural",
    [string]$Output = "speech.mp3"
)

$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $PSScriptRoot "config.yaml"
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "config.yaml 不存在。请先双击 edge-tts-server.exe。"
}

$KeyLine = Get-Content -LiteralPath $ConfigPath | Where-Object { $_ -match '^\s*api_key\s*:' } | Select-Object -First 1
if ($null -eq $KeyLine) {
    throw "config.yaml 中缺少 api_key。"
}
$ApiKey = (($KeyLine -split ':', 2)[1]).Trim().Trim('"').Trim("'")
if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "config.yaml 中的 api_key 不能为空。"
}

$Body = @{
    text = $Text
    voice = $Voice
} | ConvertTo-Json

Invoke-WebRequest `
    -Uri "http://127.0.0.1:5050/v1/tts" `
    -Method Post `
    -Headers @{ "X-API-Key" = $ApiKey } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($Body)) `
    -OutFile $Output `
    -UseBasicParsing

Write-Host "已生成: $Output"
