[CmdletBinding()]
param(
    [string]$ArchivePath = 'D:\Down\PX4_Ubuntu_20260731.tar.gz',
    [string]$DistroName = 'PX4-Ubuntu',
    [string]$InstallPath = 'D:\WSL\PX4-Ubuntu',
    [string]$LinuxUser = 'reisei0824',
    [string]$ExpectedSha256 = '0B2343CBB49737A699F6AA20204810778DF2753E845A301716716EB487975F78',
    [string]$ExpectedCommit = 'a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f',
    [string]$ReportPath = 'D:\Down\PX4_WSL_IMPORT_REPORT.txt'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Report {
    param([string]$Message)
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    $line | Tee-Object -FilePath $ReportPath -Append
}
try {
    Set-Content -LiteralPath $ReportPath -Value ('PX4 offline WSL import started: {0:O}' -f (Get-Date)) -Encoding UTF8
    Write-Report "REMOTE_USER=$env:USERDOMAIN\$env:USERNAME"
    if ($env:USERNAME -ne 'djy') {
        throw "Refusing to register a per-user WSL distro under unexpected account '$env:USERNAME'. Expected 'djy'."
    }

    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
        throw "Archive not found: $ArchivePath"
    }

    $archive = Get-Item -LiteralPath $ArchivePath
    Write-Report "ARCHIVE_BYTES=$($archive.Length)"
    if ($archive.Length -ne 8279621241) {
        throw "Archive size mismatch: $($archive.Length) bytes"
    }

    Write-Report 'REMOTE_SHA256_CHECK=RUNNING'
    $actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToUpperInvariant()
    Write-Report "REMOTE_SHA256=$actualHash"
    if ($actualHash -ne $ExpectedSha256) {
        throw "Remote SHA-256 mismatch. Expected $ExpectedSha256, got $actualHash"
    }
    Write-Report 'REMOTE_SHA256_CHECK=PASS'

    $rawDistros = (& wsl.exe --list --quiet 2>&1 | Out-String) -replace "`0", ''
    $distros = @($rawDistros -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    Write-Report ('WSL_DISTROS_BEFORE=' + ($distros -join ','))

    if ($distros -contains $DistroName) {
        Write-Report 'IMPORT_SKIPPED=EXISTING_DISTRO'
    }
    else {
        if ((Test-Path -LiteralPath $InstallPath) -and (Get-ChildItem -LiteralPath $InstallPath -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            throw "Install path is non-empty while distro is unregistered; preserving it for inspection: $InstallPath"
        }
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
        Write-Report "IMPORT=RUNNING name=$DistroName path=$InstallPath"
        & wsl.exe --import $DistroName $InstallPath $ArchivePath --version 2
        if ($LASTEXITCODE -ne 0) {
            throw "wsl --import failed with exit code $LASTEXITCODE"
        }
        Write-Report 'IMPORT=PASS'
    }

    $rawDistrosAfter = (& wsl.exe --list --quiet 2>&1 | Out-String) -replace "`0", ''
    $distrosAfter = @($rawDistrosAfter -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($distrosAfter -notcontains $DistroName) {
        throw "Imported distro '$DistroName' not present in wsl --list output"
    }
    Write-Report ('WSL_DISTROS_AFTER=' + ($distrosAfter -join ','))

    $userCheck = (& wsl.exe -d $DistroName -u root -- bash -lc "id -u '$LinuxUser'" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $userCheck -notmatch '^\d+$') {
        throw "Linux user '$LinuxUser' is missing from imported distro: $userCheck"
    }
    Write-Report "LINUX_USER_UID=$userCheck"

    $commit = (& wsl.exe -d $DistroName -u $LinuxUser -- bash -lc 'git -C ~/PX4-Autopilot rev-parse HEAD' 2>&1 | Out-String).Trim()
    Write-Report "PX4_COMMIT=$commit"
    if ($LASTEXITCODE -ne 0 -or $commit -ne $ExpectedCommit) {
        throw "PX4 commit mismatch. Expected $ExpectedCommit, got $commit"
    }

    $binaryCheck = (& wsl.exe -d $DistroName -u $LinuxUser -- bash -lc 'test -x ~/PX4-Autopilot/build/px4_sitl_default/bin/px4 && echo PX4_BINARY_OK' 2>&1 | Out-String).Trim()
    Write-Report "PX4_BINARY_CHECK=$binaryCheck"
    if ($LASTEXITCODE -ne 0 -or $binaryCheck -ne 'PX4_BINARY_OK') {
        throw 'PX4 SITL binary is missing or not executable'
    }

    $pymavlink = (& wsl.exe -d $DistroName -u $LinuxUser -- bash -lc "python3 -c 'import pymavlink; print(pymavlink.__file__)'" 2>&1 | Out-String).Trim()
    Write-Report "PYMAVLINK=$pymavlink"
    if ($LASTEXITCODE -ne 0 -or -not $pymavlink) {
        throw 'pymavlink validation failed'
    }

    & wsl.exe --terminate $DistroName | Out-Null
    Write-Report 'OVERALL=PASS'
    Write-Report 'DONE'
    exit 0
}
catch {
    Write-Report ('ERROR=' + $_.Exception.Message)
    Write-Report 'OVERALL=FAIL'
    Write-Report 'DONE'
    exit 1
}
