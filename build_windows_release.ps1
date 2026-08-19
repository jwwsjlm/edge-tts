param(
    [string]$Python = "python",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $Root "releases/windows"))
$ExpectedPrefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

function Stop-ReleaseProcesses {
    param([string]$ExecutablePath)

    $ManagedPath = [IO.Path]::GetFullPath($ExecutablePath)
    $ExecutableName = [IO.Path]::GetFileName($ManagedPath).Replace("'", "''")
    $Matching = @(
        Get-CimInstance Win32_Process -Filter "Name = '$ExecutableName'" |
            Where-Object {
                $null -ne $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).Equals($ManagedPath, [StringComparison]::OrdinalIgnoreCase)
            }
    )
    foreach ($Running in $Matching) {
        Stop-Process -Id $Running.ProcessId -Force -ErrorAction SilentlyContinue
    }

    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        $Remaining = @(
            Get-CimInstance Win32_Process -Filter "Name = '$ExecutableName'" |
                Where-Object {
                    $null -ne $_.ExecutablePath -and
                    [IO.Path]::GetFullPath($_.ExecutablePath).Equals($ManagedPath, [StringComparison]::OrdinalIgnoreCase)
                }
        )
        if ($Remaining.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Unable to stop packaged server process: $ManagedPath"
}

if (-not $ReleaseRoot.StartsWith($ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to modify a release directory outside the repository: $ReleaseRoot"
}

Push-Location $Root
try {
    $PackagedExe = Join-Path $ReleaseRoot "edge-tts-windows-x64-standalone/edge-tts-server.exe"
    $StandaloneExe = Join-Path $ReleaseRoot "edge-tts-windows-x64.exe"
    Stop-ReleaseProcesses -ExecutablePath $PackagedExe
    Stop-ReleaseProcesses -ExecutablePath $StandaloneExe
    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null

    & $Python -m PyInstaller --clean --noconfirm "edge-tts-server.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $ExeSource = Join-Path $Root "dist/edge-tts-server.exe"
    if (-not (Test-Path -LiteralPath $ExeSource -PathType Leaf)) {
        throw "PyInstaller output is missing: $ExeSource"
    }

    # Publish a clearly named one-file executable in addition to the full ZIP.
    # The frozen CLI reads or creates config.yaml beside this executable.
    Copy-Item -LiteralPath $ExeSource -Destination $StandaloneExe

    $Bundle = Join-Path $ReleaseRoot "edge-tts-windows-x64-standalone"
    New-Item -ItemType Directory -Path $Bundle -Force | Out-Null
    Copy-Item -LiteralPath $ExeSource -Destination (Join-Path $Bundle "edge-tts-server.exe")
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/config.example.yaml") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/README-FIRST.txt") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/01-start-server.bat") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/02-open-swagger.url") -Destination $Bundle
    Copy-Item -LiteralPath (Join-Path $Root "packaging/windows/03-call-example.ps1") -Destination $Bundle

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
            $Process = Start-Process -FilePath $PackagedExe -ArgumentList $Arguments -PassThru -WindowStyle Hidden
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
            Stop-ReleaseProcesses -ExecutablePath $PackagedExe
            Remove-Item -LiteralPath $TempConfig -Force -ErrorAction SilentlyContinue
        }

        # Verify the published single-file EXE reads config.yaml beside itself.
        $StandaloneConfig = Join-Path $ReleaseRoot "config.yaml"
        @"
api_key: "release-single-file-smoke"
host: "127.0.0.1"
port: $Port
"@ | Set-Content -LiteralPath $StandaloneConfig -Encoding UTF8
        $StandaloneProcess = $null
        try {
            $StandaloneProcess = Start-Process -FilePath $StandaloneExe -PassThru -WindowStyle Hidden
            $Healthy = $false
            for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
                if ($StandaloneProcess.HasExited) {
                    throw "Single-file EXE exited before becoming healthy (exit code $($StandaloneProcess.ExitCode))"
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
                throw "Single-file EXE did not answer /health"
            }
        } finally {
            if ($null -ne $StandaloneProcess -and -not $StandaloneProcess.HasExited) {
                Stop-Process -Id $StandaloneProcess.Id -Force
                $StandaloneProcess.WaitForExit()
            }
            Stop-ReleaseProcesses -ExecutablePath $StandaloneExe
            Remove-Item -LiteralPath $StandaloneConfig -Force -ErrorAction SilentlyContinue
        }
    }

    $Archive = Join-Path $ReleaseRoot "edge-tts-windows-x64-standalone.zip"
    Compress-Archive -Path $Bundle -DestinationPath $Archive -CompressionLevel Optimal
    Write-Host "Windows release created: $Archive"
    Write-Host "Windows single-file executable created: $StandaloneExe"
} finally {
    Pop-Location
}
