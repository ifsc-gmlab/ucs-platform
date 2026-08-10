[CmdletBinding()]
param(
    [string]$ArchivePath = 'D:\Down\PX4_Ubuntu_20260731.tar.gz',
    [string]$TarPath = 'D:\Down\PX4_Ubuntu_20260731.tar',
    [string]$DistroName = 'PX4-Ubuntu',
    [string]$InstallRoot = 'D:\WSL',
    [string]$LinuxUser = 'reisei0824',
    [string]$ExpectedSha256 = '0B2343CBB49737A699F6AA20204810778DF2753E845A301716716EB487975F78',
    [string]$ExpectedCommit = 'a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f',
    [string]$PreviousReport = 'D:\Down\PX4_WSL_IMPORT_REPORT.txt',
    [string]$ReportPath = 'D:\Down\PX4_WSL_RECOVERY_REPORT.txt'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$priorRecoveryText = if (Test-Path -LiteralPath $ReportPath -PathType Leaf) {
    Get-Content -LiteralPath $ReportPath -Raw
} else {
    ''
}

function Write-Report {
    param([string]$Message)
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    $line | Tee-Object -FilePath $ReportPath -Append
}

function Get-WslDistros {
    $raw = (& wsl.exe --list --quiet 2>&1 | Out-String) -replace "`0", ''
    return @($raw -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Invoke-WslLogged {
    param([string[]]$Arguments, [string]$Label)
    Write-Report "$Label=RUNNING"
    $output = (& wsl.exe @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    if ($output) {
        foreach ($line in ($output -split "`r?`n")) {
            Write-Report "$Label OUTPUT: $line"
        }
    }
    Write-Report "$Label`_EXIT=$exitCode"
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
    }
    return $output
}

try {
    Set-Content -LiteralPath $ReportPath -Value ('PX4 WSL safe recovery started: {0:O}' -f (Get-Date)) -Encoding UTF8
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

    $priorHashPass = $false
    if (Test-Path -LiteralPath $PreviousReport -PathType Leaf) {
        $priorText = Get-Content -LiteralPath $PreviousReport -Raw
        $priorHashPass = $priorText -match [regex]::Escape("REMOTE_SHA256=$ExpectedSha256") -and
                         $priorText -match 'REMOTE_SHA256_CHECK=PASS'
    }
    if ($priorHashPass) {
        Write-Report "REMOTE_SHA256_REUSED=$ExpectedSha256"
        Write-Report 'REMOTE_SHA256_CHECK=PASS_FROM_PREVIOUS_REPORT'
    }
    else {
        Write-Report 'REMOTE_SHA256_CHECK=RUNNING_FALLBACK'
        $actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToUpperInvariant()
        Write-Report "REMOTE_SHA256=$actualHash"
        if ($actualHash -ne $ExpectedSha256) {
            throw "Remote SHA-256 mismatch. Expected $ExpectedSha256, got $actualHash"
        }
        Write-Report 'REMOTE_SHA256_CHECK=PASS_FALLBACK'
    }

    $status = (& wsl.exe --status 2>&1 | Out-String).Trim()
    Write-Report ('WSL_STATUS=' + ($status -replace "`r?`n", ' | '))
    $verbose = (& wsl.exe --list --verbose 2>&1 | Out-String).Trim()
    Write-Report ('WSL_LIST_VERBOSE=' + ($verbose -replace "`r?`n", ' | '))
    $dVolume = Get-Volume -DriveLetter D -ErrorAction Stop
    Write-Report "D_FILESYSTEM=$($dVolume.FileSystem)"
    Write-Report "D_HEALTH=$($dVolume.HealthStatus)"
    Write-Report "D_ALLOCATION_UNIT_SIZE=$($dVolume.AllocationUnitSize)"

    $distros = Get-WslDistros
    Write-Report ('WSL_DISTROS_BEFORE=' + ($distros -join ','))
    if ($distros -contains $DistroName) {
        Write-Report 'IMPORT_SKIPPED=TARGET_ALREADY_REGISTERED'
    }
    else {
        $sourceDistro = $distros | Where-Object {
            $_ -match '^Ubuntu(?:[- ]?24\.04)?$' -or $_ -match '^Ubuntu[- ]24\.04$'
        } | Select-Object -First 1
        if (-not $sourceDistro) {
            throw 'No existing Ubuntu WSL distro is available to decompress the gzip archive offline.'
        }
        Write-Report "DECOMPRESS_DISTRO=$sourceDistro"

        if (Test-Path -LiteralPath $TarPath -PathType Leaf) {
            $tar = Get-Item -LiteralPath $TarPath
            Write-Report "UNCOMPRESSED_TAR_REUSED=$TarPath"
            Write-Report "UNCOMPRESSED_TAR_BYTES=$($tar.Length)"
            if ($tar.Length -le $archive.Length) {
                throw 'Existing uncompressed tar is unexpectedly small; preserving it for inspection.'
            }
        }
        else {
            $drive = Get-PSDrive -Name D
            Write-Report "D_FREE_BYTES=$($drive.Free)"
            if ($drive.Free -lt 40GB) {
                throw 'Less than 40 GiB is free on server D:. Refusing to create the uncompressed tar.'
            }

            $partialPath = "$TarPath.partial.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
            $partialWsl = '/mnt/d/Down/' + [IO.Path]::GetFileName($partialPath)
            Write-Report "DECOMPRESS_TARGET=$partialPath"
            $bash = "set -euo pipefail; gzip -dc '/mnt/d/Down/PX4_Ubuntu_20260731.tar.gz' > '$partialWsl'; tar -tf '$partialWsl' >/dev/null"
            Invoke-WslLogged -Arguments @('-d', $sourceDistro, '-u', 'root', '--', 'bash', '-lc', $bash) -Label 'DECOMPRESS_AND_VALIDATE_TAR' | Out-Null
            Move-Item -LiteralPath $partialPath -Destination $TarPath
            $tar = Get-Item -LiteralPath $TarPath
            Write-Report "UNCOMPRESSED_TAR=$TarPath"
            Write-Report "UNCOMPRESSED_TAR_BYTES=$($tar.Length)"
        }

        $attempt = 2
        do {
            $installPath = Join-Path $InstallRoot ("PX4-Ubuntu-import{0}" -f $attempt)
            $attempt++
        } while (Test-Path -LiteralPath $installPath)
        New-Item -ItemType Directory -Path $installPath -Force | Out-Null
        Write-Report "PRESERVED_FAILED_INSTALL_PATH=D:\WSL\PX4-Ubuntu"
        $skipDirectV2 = $priorRecoveryText -match 'IMPORT_UNCOMPRESSED_TAR failed with exit code'
        if (-not $skipDirectV2) {
            Write-Report "IMPORT_V2_DIRECT=RUNNING name=$DistroName path=$installPath source=$TarPath"
            try {
                Invoke-WslLogged -Arguments @('--import', $DistroName, $installPath, $TarPath, '--version', '2') -Label 'IMPORT_UNCOMPRESSED_TAR' | Out-Null
                Write-Report 'IMPORT_V2_DIRECT=PASS'
            }
            catch {
                Write-Report ('IMPORT_V2_DIRECT=FAILED_PRESERVED error=' + $_.Exception.Message)
            }
        }
        else {
            Write-Report 'IMPORT_V2_DIRECT=SKIPPED_PREVIOUS_FAILURE'
        }

        $afterDirect = Get-WslDistros
        if ($afterDirect -notcontains $DistroName) {
            do {
                $installPath = Join-Path $InstallRoot ("PX4-Ubuntu-import{0}" -f $attempt)
                $attempt++
            } while (Test-Path -LiteralPath $installPath)
            New-Item -ItemType Directory -Path $installPath -Force | Out-Null
            Write-Report "IMPORT_V1_COMPAT=RUNNING name=$DistroName path=$installPath source=$TarPath"
            Invoke-WslLogged -Arguments @('--import', $DistroName, $installPath, $TarPath, '--version', '1') -Label 'IMPORT_V1_COMPAT' | Out-Null
            Write-Report 'IMPORT_V1_COMPAT=PASS'
            Invoke-WslLogged -Arguments @('--set-version', $DistroName, '2') -Label 'CONVERT_IMPORTED_DISTRO_TO_WSL2' | Out-Null
            Write-Report 'CONVERT_TO_WSL2=PASS'
        }
        Write-Report 'IMPORT=PASS'
    }

    $distrosAfter = Get-WslDistros
    Write-Report ('WSL_DISTROS_AFTER=' + ($distrosAfter -join ','))
    if ($distrosAfter -notcontains $DistroName) {
        throw "Distro '$DistroName' is not registered after recovery"
    }

    $uid = Invoke-WslLogged -Arguments @('-d', $DistroName, '-u', 'root', '--', 'bash', '-lc', "id -u '$LinuxUser'") -Label 'LINUX_USER_CHECK'
    if ($uid -notmatch '^\d+$') {
        throw "Linux user '$LinuxUser' validation returned unexpected output: $uid"
    }
    Write-Report "LINUX_USER_UID=$uid"

    $commit = Invoke-WslLogged -Arguments @('-d', $DistroName, '-u', $LinuxUser, '--', 'bash', '-lc', 'git -C ~/PX4-Autopilot rev-parse HEAD') -Label 'PX4_COMMIT_CHECK'
    Write-Report "PX4_COMMIT=$commit"
    if ($commit -ne $ExpectedCommit) {
        throw "PX4 commit mismatch. Expected $ExpectedCommit, got $commit"
    }

    $binary = Invoke-WslLogged -Arguments @('-d', $DistroName, '-u', $LinuxUser, '--', 'bash', '-lc', 'test -x ~/PX4-Autopilot/build/px4_sitl_default/bin/px4 && echo PX4_BINARY_OK') -Label 'PX4_BINARY_CHECK'
    if ($binary -ne 'PX4_BINARY_OK') {
        throw "PX4 binary check returned unexpected output: $binary"
    }

    $pymavlink = Invoke-WslLogged -Arguments @('-d', $DistroName, '-u', $LinuxUser, '--', 'bash', '-lc', "python3 -c 'import pymavlink; print(pymavlink.__file__)'") -Label 'PYMAVLINK_CHECK'
    Write-Report "PYMAVLINK=$pymavlink"

    Invoke-WslLogged -Arguments @('--set-default', $DistroName) -Label 'SET_DEFAULT_DISTRO' | Out-Null
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
