param(
    [string]$Python = "python",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $Root "releases/windows"))
$ExpectedPrefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

if (-not $ReleaseRoot.StartsWith($ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to modify a release directory outside the repository: $ReleaseRoot"
}

Push-Location $Root
try {
    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }

    & $Python -m PyInstaller --clean --noconfirm "edge-tts-server.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $ExeSource = Join-Path $Root "dist/edge-tts-server.exe"
    if (-not (Test-Path -LiteralPath $ExeSource -PathType Leaf)) {
        throw "PyInstaller output is missing: $ExeSource"
    }

    $Bundle = Join-Path $ReleaseRoot "edge-tts-server-windows-x64"
    New-Item -ItemType Directory -Path $Bundle -Force | Out-Null
    Copy-Item -LiteralPath $ExeSource -Destination (Join-Path $Bundle "edge-tts-server.exe")
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/config.example.yaml") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/README.txt") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/call-example.ps1") -Destination $Bundle

    if (-not $SkipSmokeTest) {
        $Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
        $Listener.Start()
        $Port = ([Net.IPEndPoint]$Listener.LocalEndpoint).Port
        $Listener.Stop()

        $TempConfig = Join-Path ([IO.Path]::GetTempPath()) ("edge-tts-server-{0}.yaml" -f [guid]::NewGuid())
        @"
api_key: "release-smoke-test"
host: "127.0.0.1"
port: $Port
"@ | Set-Content -LiteralPath $TempConfig -Encoding UTF8

        $Process = $null
        try {
            $Arguments = @("--config", ('"{0}"' -f $TempConfig))
            $Process = Start-Process -FilePath (Join-Path $Bundle "edge-tts-server.exe") -ArgumentList $Arguments -PassThru -WindowStyle Hidden
            $Healthy = $false
            for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
                if ($Process.HasExited) {
                    throw "Packaged server exited before becoming healthy (exit code $($Process.ExitCode))"
                }
                try {
                    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
                    if ($Health.status -eq "ok") {
                        $Healthy = $true
                        break
                    }
                } catch {
                    Start-Sleep -Milliseconds 250
                }
            }
            if (-not $Healthy) {
                throw "Packaged server did not answer /health"
            }
        } finally {
            if ($null -ne $Process -and -not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force
                $Process.WaitForExit()
            }
            Remove-Item -LiteralPath $TempConfig -Force -ErrorAction SilentlyContinue
        }
    }

    $Archive = Join-Path $ReleaseRoot "edge-tts-server-windows-x64.zip"
    Compress-Archive -Path $Bundle -DestinationPath $Archive -CompressionLevel Optimal
    Write-Host "Windows release created: $Archive"
} finally {
    Pop-Location
}
