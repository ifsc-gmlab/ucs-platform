[CmdletBinding()]
param(
    [string]$Server = '172.16.33.199',
    [string]$UserName = 'djy',
    [string]$SourceArchive = 'D:\Installers\PX4_Ubuntu_20260731.tar.gz',
    [string]$RemoteFolder = 'D:\Down',
    [string]$ExpectedSha256 = '0B2343CBB49737A699F6AA20204810778DF2753E845A301716716EB487975F78'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$localLog = Join-Path $workspaceRoot 'outputs\server_px4_migration_launcher.log'
$remoteImportScript = Join-Path $PSScriptRoot 'server_import_px4_offline.ps1'
$archiveName = Split-Path -Leaf $SourceArchive
$expectedBytes = 8279621241L

function Write-Log {
    param([string]$Message)
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    $line | Tee-Object -FilePath $localLog -Append
}

Set-Content -LiteralPath $localLog -Value ('Server PX4 migration launcher started: {0:O}' -f (Get-Date)) -Encoding UTF8
$remoteDrive = $null
$cim = $null

try {
    if (-not (Test-Path -LiteralPath $SourceArchive -PathType Leaf)) {
        throw "Source archive not found: $SourceArchive"
    }
    if (-not (Test-Path -LiteralPath $remoteImportScript -PathType Leaf)) {
        throw "Remote import script not found: $remoteImportScript"
    }

    $sourceItem = Get-Item -LiteralPath $SourceArchive
    Write-Log "SOURCE_BYTES=$($sourceItem.Length)"
    if ($sourceItem.Length -ne $expectedBytes) {
        throw "Source archive size mismatch: $($sourceItem.Length) bytes"
    }

    Write-Log 'LOCAL_SHA256_CHECK=RUNNING'
    $sourceHash = (Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256).Hash.ToUpperInvariant()
    Write-Log "LOCAL_SHA256=$sourceHash"
    if ($sourceHash -ne $ExpectedSha256) {
        throw "Local SHA-256 mismatch. Expected $ExpectedSha256, got $sourceHash"
    }
    Write-Log 'LOCAL_SHA256_CHECK=PASS'

    Write-Host ''
    Write-Host 'Enter the IFSC server password for djy in the Windows credential dialog. The password is not written to scripts or logs.' -ForegroundColor Cyan
    $credential = Get-Credential -UserName $UserName -Message "IFSC server $Server - enter the password for $UserName"
    if (-not $credential) {
        throw 'Credential entry was cancelled'
    }

    $remoteDrive = New-PSDrive -Name 'PX4Remote' -PSProvider FileSystem -Root "\\$Server\D$" -Credential $credential -Scope Script
    $remoteFolderViaDrive = 'PX4Remote:\Down'
    New-Item -ItemType Directory -Path $remoteFolderViaDrive -Force | Out-Null
    Write-Log 'SMB_D_DRIVE=PASS'

    $remoteArchive = "\\$Server\D$\Down\$archiveName"
    $sourceStream = [System.IO.File]::Open($SourceArchive, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $destStream = [System.IO.File]::Open($remoteArchive, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            if ($destStream.Length -gt $sourceStream.Length) {
                $destStream.SetLength(0)
            }
            $offset = $destStream.Length
            if ($offset -eq $sourceStream.Length) {
                Write-Log 'COPY_SKIPPED=REMOTE_SIZE_ALREADY_MATCHES'
            }
            else {
                [void]$sourceStream.Seek($offset, [System.IO.SeekOrigin]::Begin)
                [void]$destStream.Seek($offset, [System.IO.SeekOrigin]::Begin)
                Write-Log "COPY=RUNNING resume_offset=$offset"
                $buffer = New-Object byte[] (8MB)
                $lastReported = $offset
                while (($read = $sourceStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $destStream.Write($buffer, 0, $read)
                    $offset += $read
                    if (($offset - $lastReported) -ge 536870912 -or $offset -eq $sourceStream.Length) {
                        $percent = [math]::Round(($offset * 100.0) / $sourceStream.Length, 1)
                        Write-Log "COPY_PROGRESS=$percent% bytes=$offset"
                        $lastReported = $offset
                    }
                }
                $destStream.Flush($true)
                Write-Log 'COPY=PASS'
            }
        }
        finally {
            $destStream.Dispose()
        }
    }
    finally {
        $sourceStream.Dispose()
    }

    Copy-Item -LiteralPath $remoteImportScript -Destination "\\$Server\D$\Down\server_import_px4_offline.ps1" -Force
    Write-Log 'REMOTE_SCRIPT_COPY=PASS'

    $cimOption = New-CimSessionOption -Protocol Dcom
    $cim = New-CimSession -ComputerName $Server -Credential $credential -SessionOption $cimOption
    $os = Get-CimInstance -CimSession $cim -ClassName Win32_OperatingSystem
    Write-Log "REMOTE_CIM=PASS computer=$($os.CSName) windows=$($os.Caption)"

    $commandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\Down\server_import_px4_offline.ps1"'
    $created = Invoke-CimMethod -CimSession $cim -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $commandLine }
    if ($created.ReturnValue -ne 0 -or -not $created.ProcessId) {
        throw "Remote process creation failed: ReturnValue=$($created.ReturnValue)"
    }
    Write-Log "REMOTE_IMPORT_PROCESS=STARTED pid=$($created.ProcessId)"

    $remoteReport = "\\$Server\D$\Down\PX4_WSL_IMPORT_REPORT.txt"
    $deadline = (Get-Date).AddHours(4)
    do {
        Start-Sleep -Seconds 5
        if (Test-Path -LiteralPath $remoteReport) {
            $tail = Get-Content -LiteralPath $remoteReport -Tail 3 -ErrorAction SilentlyContinue
            if ($tail) {
                Write-Host ($tail -join [Environment]::NewLine)
            }
            if (Select-String -LiteralPath $remoteReport -SimpleMatch 'DONE' -Quiet) {
                break
            }
        }
        $remoteProcess = Get-CimInstance -CimSession $cim -ClassName Win32_Process -Filter "ProcessId=$($created.ProcessId)" -ErrorAction SilentlyContinue
        if (-not $remoteProcess -and -not (Test-Path -LiteralPath $remoteReport)) {
            throw 'Remote import process exited before producing a report'
        }
    } while ((Get-Date) -lt $deadline)

    if ((Get-Date) -ge $deadline) {
        throw 'Timed out after four hours waiting for the remote import'
    }

    $reportText = Get-Content -LiteralPath $remoteReport -Raw
    $reportText | Set-Content -LiteralPath (Join-Path $workspaceRoot 'outputs\PX4_WSL_IMPORT_REPORT.server.txt') -Encoding UTF8
    if ($reportText -notmatch 'OVERALL=PASS') {
        throw 'Remote import report did not pass; inspect outputs\PX4_WSL_IMPORT_REPORT.server.txt'
    }

    Write-Log 'OVERALL=PASS'
    Write-Host ''
    Write-Host 'PX4 offline WSL migration PASS.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Log ('ERROR=' + $_.Exception.Message)
    Write-Log 'OVERALL=FAIL'
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Log: $localLog" -ForegroundColor Yellow
    exit 1
}
finally {
    if ($cim) {
        Remove-CimSession -CimSession $cim -ErrorAction SilentlyContinue
    }
    if ($remoteDrive) {
        Remove-PSDrive -Name $remoteDrive.Name -Force -ErrorAction SilentlyContinue
    }
}
