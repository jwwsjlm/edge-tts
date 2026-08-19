param(
    [string]$Python = "python",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $Root "releases/windows"))
$StandaloneExe = Join-Path $ReleaseRoot "edge-tts-windows-x64.exe"

function Stop-PackagedProcess {
    param([string]$ExecutablePath)

    $managed = [IO.Path]::GetFullPath($ExecutablePath)
    $name = [IO.Path]::GetFileName($managed).Replace("'", "''")
    Get-CimInstance Win32_Process -Filter "Name = '$name'" |
        Where-Object {
            $null -ne $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $managed, [StringComparison]::OrdinalIgnoreCase
            )
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Push-Location $Root
try {
    Stop-PackagedProcess $StandaloneExe
    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null

    & $Python -m PyInstaller --clean --noconfirm "edge-tts-server.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $source = Join-Path $Root "dist/edge-tts-server.exe"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "PyInstaller output is missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination $StandaloneExe -Force

    if (-not $SkipSmokeTest) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
        $listener.Stop()
        $config = Join-Path $ReleaseRoot "config.yaml"
        @"
api_key: "release-smoke-test"
host: "127.0.0.1"
port: $port
"@ | Set-Content -LiteralPath $config -Encoding UTF8

        $process = $null
        try {
            $process = Start-Process -FilePath $StandaloneExe -PassThru -WindowStyle Hidden
            $healthy = $false
            for ($attempt = 0; $attempt -lt 40; $attempt++) {
                if ($process.HasExited) {
                    throw "Single-file EXE exited before becoming healthy (exit code $($process.ExitCode))"
                }
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1
                    if ($health.status -eq "ok") {
                        $healthy = $true
                        break
                    }
                } catch {
                    Start-Sleep -Milliseconds 250
                }
            }
            if (-not $healthy) {
                throw "Single-file EXE did not answer /health"
            }
        } finally {
            if ($null -ne $process -and -not $process.HasExited) {
                Stop-Process -Id $process.Id -Force
                $process.WaitForExit()
            }
            Stop-PackagedProcess $StandaloneExe
            Remove-Item -LiteralPath $config -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "Windows single-file EXE created: $StandaloneExe"
} finally {
    Pop-Location
}
