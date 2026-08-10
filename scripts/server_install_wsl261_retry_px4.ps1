[CmdletBinding()]
param(
    [string]$SourceMsi = "\\tsclient\D\Installers\wsl.2.6.1.0.x64.microsoft-signed.msi",
    [string]$ServerMsi = "D:\Down\wsl.2.6.1.0.x64.microsoft-signed.msi",
    [string]$Archive = "D:\Down\PX4_Ubuntu_20260731.tar",
    [string]$Distro = "PX4-Ubuntu",
    [string]$InstallDir = "D:\WSL\PX4-Ubuntu-wsl261",
    [string]$LinuxUser = "reisei0824",
    [string]$ExpectedCommit = "a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f",
    [string]$LocalResultDir = "\\tsclient\D\Installers\server-results",
    [switch]$SkipMsi
)

$ErrorActionPreference = "Stop"
$ExpectedMsiHash = "593CACE63530E67BD3A19FDE9AC2A272EFE3734D03AFDDACB8394DE7055B6235"
$Report = "D:\Down\PX4_WSL_261_INSTALL_AND_IMPORT_REPORT.txt"
$Transcript = "D:\Down\PX4_WSL_261_INSTALL_AND_IMPORT_TRANSCRIPT.txt"

New-Item -ItemType Directory -Path "D:\Down", "D:\WSL" -Force | Out-Null
Remove-Item -LiteralPath $Report, $Transcript -Force -ErrorAction SilentlyContinue
Start-Transcript -LiteralPath $Transcript -Force | Out-Null

function Write-Report {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $Report -Value $line -Encoding UTF8
}

try {
    Write-Report "START WSL 2.6.1 offline install and PX4 import retry"

    if (-not $SkipMsi -and -not (Test-Path -LiteralPath $SourceMsi -PathType Leaf)) {
        throw "Local redirected MSI not found: $SourceMsi"
    }
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        throw "Server PX4 tar not found: $Archive"
    }

    $archiveInfo = Get-Item -LiteralPath $Archive
    Write-Report "PX4 tar bytes=$($archiveInfo.Length) path=$Archive"
    if ($archiveInfo.Length -ne 25434255360) {
        throw "Unexpected PX4 tar size: $($archiveInfo.Length)"
    }

    if (-not $SkipMsi) {
        Write-Report "Copying Microsoft-signed WSL MSI to server D drive"
        Copy-Item -LiteralPath $SourceMsi -Destination $ServerMsi -Force

        $msiInfo = Get-Item -LiteralPath $ServerMsi
        $msiHash = (Get-FileHash -LiteralPath $ServerMsi -Algorithm SHA256).Hash
        $signature = Get-AuthenticodeSignature -LiteralPath $ServerMsi
        Write-Report "MSI bytes=$($msiInfo.Length) SHA256=$msiHash signature=$($signature.Status)"
        if ($msiHash -ne $ExpectedMsiHash) {
            throw "WSL MSI SHA256 mismatch"
        }
        if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
            throw "WSL MSI Microsoft signature validation failed"
        }

        Write-Report "Launching the WSL MSI. Complete the UAC/install window; this script will wait."
        $msi = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", ('"{0}"' -f $ServerMsi)) -Wait -PassThru
        Write-Report "MSI exit code=$($msi.ExitCode)"
        if ($msi.ExitCode -eq 3010) {
            throw "WSL installed but Windows requested a reboot. Reboot the server, then run this script again."
        }
        if ($msi.ExitCode -ne 0) {
            throw "WSL MSI installation failed with exit code $($msi.ExitCode)"
        }
    } else {
        Write-Report "Skipping MSI installation and resuming with the already installed WSL"
    }

    & wsl.exe --shutdown 2>$null
    Start-Sleep -Seconds 3
    $versionText = ((& wsl.exe --version 2>&1 | Out-String) -replace "\x00", "").Trim()
    Write-Report ("WSL version output: " + ($versionText -replace "\r?\n", " | "))
    if ($versionText -notmatch "2\.6\.1") {
        throw "Modern WSL 2.6.1 was not detected after MSI installation"
    }

    $registered = (& wsl.exe --list --quiet 2>$null) -replace "\x00", ""
    if ($registered -contains $Distro) {
        throw "Distro name already registered; refusing to overwrite: $Distro"
    }
    if (Test-Path -LiteralPath $InstallDir) {
        $children = @(Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue)
        if ($children.Count -gt 0) {
            throw "Install directory already contains files; refusing to overwrite: $InstallDir"
        }
    } else {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    Write-Report "Importing $Distro from the validated server tar. This can take several minutes."
    & wsl.exe --import $Distro $InstallDir $Archive --version 2 2>&1 |
        ForEach-Object { Write-Host $_; Add-Content -LiteralPath $Report -Value $_ -Encoding UTF8 }
    $importExit = $LASTEXITCODE
    Write-Report "wsl --import exit code=$importExit"
    if ($importExit -ne 0) {
        throw "WSL import failed with exit code $importExit"
    }

    & wsl.exe -d $Distro -u root -- bash -lc "id -u '$LinuxUser' >/dev/null"
    if ($LASTEXITCODE -ne 0) { throw "Expected Linux user is missing: $LinuxUser" }

    & wsl.exe -d $Distro -u root -- bash -lc "printf '[user]\ndefault=$LinuxUser\n' > /etc/wsl.conf"
    if ($LASTEXITCODE -ne 0) { throw "Failed to configure default Linux user" }
    & wsl.exe --shutdown

    $commit = (& wsl.exe -d $Distro -u $LinuxUser -- bash -lc "git -C ~/PX4-Autopilot rev-parse HEAD" 2>&1 | Out-String).Trim()
    Write-Report "PX4 commit=$commit"
    if ($commit -ne $ExpectedCommit) { throw "PX4 commit mismatch" }

    & wsl.exe -d $Distro -u $LinuxUser -- bash -lc "test -x ~/PX4-Autopilot/build/px4_sitl_default/bin/px4"
    if ($LASTEXITCODE -ne 0) { throw "PX4 SITL binary is missing" }
    Write-Report "PX4 binary=PASS"

    & wsl.exe -d $Distro -u $LinuxUser -- bash -lc "python3 -c 'import pymavlink; print(pymavlink.__file__)'"
    if ($LASTEXITCODE -ne 0) { throw "pymavlink validation failed" }
    Write-Report "pymavlink=PASS"

    & wsl.exe --set-default $Distro
    if ($LASTEXITCODE -ne 0) { throw "Failed to set default WSL distro" }
    Write-Report "Default distro=$Distro"
    Write-Report "PASS WSL 2.6.1 installed and PX4-Ubuntu imported. UE/PX4/QGC were not started."
    exit 0
}
catch {
    Write-Report ("FAIL " + $_.Exception.Message)
    Write-Report "See transcript: $Transcript"
    exit 1
}
finally {
    Stop-Transcript | Out-Null
    try {
        New-Item -ItemType Directory -Path $LocalResultDir -Force | Out-Null
        if (Test-Path -LiteralPath $Report) {
            Copy-Item -LiteralPath $Report -Destination $LocalResultDir -Force
        }
        if (Test-Path -LiteralPath $Transcript) {
            Copy-Item -LiteralPath $Transcript -Destination $LocalResultDir -Force
        }
    }
    catch {
        Write-Warning "Could not copy reports back to the redirected local D drive: $($_.Exception.Message)"
    }
}
