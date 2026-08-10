[CmdletBinding()]
param(
    [string]$ReportPath = 'D:\Down\PX4_WSL_IMPORT_DIAGNOSTIC.txt'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Add-Line {
    param([string]$Text)
    $Text | Tee-Object -FilePath $ReportPath -Append
}

function Add-Section {
    param([string]$Name)
    Add-Line ''
    Add-Line ('==== ' + $Name + ' ====')
}

Set-Content -LiteralPath $ReportPath -Value ('PX4 WSL import diagnostic: {0:O}' -f (Get-Date)) -Encoding UTF8
Add-Line "USER=$env:USERDOMAIN\$env:USERNAME"

Add-Section 'WINDOWS'
try {
    $os = Get-CimInstance Win32_OperatingSystem
    Add-Line "CAPTION=$($os.Caption)"
    Add-Line "VERSION=$($os.Version)"
    Add-Line "BUILD=$($os.BuildNumber)"
    Add-Line "ARCH=$($os.OSArchitecture)"
} catch { Add-Line "WINDOWS_ERROR=$($_.Exception.Message)" }

Add-Section 'D_VOLUME'
try {
    $vol = Get-Volume -DriveLetter D
    Add-Line "FILESYSTEM=$($vol.FileSystem)"
    Add-Line "HEALTH=$($vol.HealthStatus)"
    Add-Line "ALLOCATION_UNIT=$($vol.AllocationUnitSize)"
    Add-Line "SIZE=$($vol.Size)"
    Add-Line "FREE=$($vol.SizeRemaining)"
} catch { Add-Line "VOLUME_ERROR=$($_.Exception.Message)" }

Add-Section 'WSL_COMMANDS'
foreach ($command in @(
    @('--status'),
    @('--list', '--verbose')
)) {
    try {
        Add-Line ('COMMAND=wsl.exe ' + ($command -join ' '))
        $text = (& wsl.exe @command 2>&1 | Out-String) -replace "`0", ''
        Add-Line $text.Trim()
        Add-Line "EXIT=$LASTEXITCODE"
    } catch { Add-Line "WSL_COMMAND_ERROR=$($_.Exception.Message)" }
}

Add-Section 'WSL_HYPERV_SERVICES'
foreach ($name in @('WslService', 'LxssManager', 'vmcompute', 'hns')) {
    try {
        $service = Get-Service -Name $name -ErrorAction Stop
        Add-Line "SERVICE=$name STATUS=$($service.Status) START_TYPE=$($service.StartType)"
    } catch {
        Add-Line "SERVICE=$name ERROR=$($_.Exception.Message)"
    }
}

Add-Section 'VIRTUALIZATION_FEATURES'
foreach ($featureName in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform', 'Microsoft-Hyper-V-All', 'Microsoft-Hyper-V-Hypervisor')) {
    try {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName -ErrorAction Stop
        Add-Line "FEATURE=$featureName STATE=$($feature.State) RESTART_NEEDED=$($feature.RestartNeeded)"
    } catch {
        Add-Line "FEATURE=$featureName ERROR=$($_.Exception.Message)"
    }
}
try {
    $computer = Get-CimInstance Win32_ComputerSystem
    $processor = Get-CimInstance Win32_Processor | Select-Object -First 1
    Add-Line "HYPERVISOR_PRESENT=$($computer.HypervisorPresent)"
    Add-Line "VM_MONITOR_MODE_EXTENSIONS=$($processor.VMMonitorModeExtensions)"
    Add-Line "VIRTUALIZATION_FIRMWARE_ENABLED=$($processor.VirtualizationFirmwareEnabled)"
    Add-Line "SECOND_LEVEL_ADDRESS_TRANSLATION=$($processor.SecondLevelAddressTranslationExtensions)"
} catch {
    Add-Line "VIRTUALIZATION_CIM_ERROR=$($_.Exception.Message)"
}
try {
    $bootConfig = (& bcdedit.exe /enum '{current}' 2>&1 | Out-String).Trim()
    Add-Line ('BCD_CURRENT=' + ($bootConfig -replace "`r?`n", ' | '))
} catch {
    Add-Line "BCD_ERROR=$($_.Exception.Message)"
}

Add-Section 'SOURCE_DISTRO'
try {
    $text = (& wsl.exe -d Ubuntu-24.04 -u root -- bash -lc 'echo UNAME=$(uname -a); df -hT / /mnt/d; echo TAR=$(tar --version | head -n1)' 2>&1 | Out-String).Trim()
    Add-Line $text
    Add-Line "SOURCE_DISTRO_EXIT=$LASTEXITCODE"
} catch { Add-Line "SOURCE_DISTRO_ERROR=$($_.Exception.Message)" }

Add-Section 'ARCHIVE_FILES'
foreach ($path in @(
    'D:\Down\PX4_Ubuntu_20260731.tar.gz',
    'D:\Down\PX4_Ubuntu_20260731.tar',
    'D:\Down\PX4_WSL_IMPORT_REPORT.txt',
    'D:\Down\PX4_WSL_RECOVERY_REPORT.txt'
)) {
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        Add-Line "PATH=$path BYTES=$($item.Length) MODIFIED=$($item.LastWriteTime.ToString('O'))"
    } else {
        Add-Line "MISSING=$path"
    }
}

Add-Section 'FAILED_INSTALL_PATHS'
foreach ($dir in Get-ChildItem -LiteralPath 'D:\WSL' -Directory -Filter 'PX4-Ubuntu*' -ErrorAction SilentlyContinue) {
    Add-Line "DIR=$($dir.FullName) MODIFIED=$($dir.LastWriteTime.ToString('O'))"
    foreach ($child in Get-ChildItem -LiteralPath $dir.FullName -Force -ErrorAction SilentlyContinue) {
        $length = if ($child.PSIsContainer) { '<DIR>' } else { [string]$child.Length }
        Add-Line "  CHILD=$($child.Name) BYTES=$length MODIFIED=$($child.LastWriteTime.ToString('O'))"
    }
}

Add-Section 'RELEVANT_EVENT_LOGS'
$logs = @(
    'Microsoft-Windows-Lxss/Operational',
    'Microsoft-Windows-Hyper-V-Compute-Admin',
    'Microsoft-Windows-Hyper-V-Worker-Admin',
    'Microsoft-Windows-VHDMP-Operational'
)
$since = (Get-Date).AddHours(-2)
foreach ($log in $logs) {
    Add-Line "LOG=$log"
    try {
        $events = Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $since } -MaxEvents 30 -ErrorAction Stop
        if (-not $events) { Add-Line '  NO_RECENT_EVENTS' }
        foreach ($event in $events) {
            $message = ($event.Message -replace "`r?`n", ' | ').Trim()
            Add-Line "  $($event.TimeCreated.ToString('O')) ID=$($event.Id) LEVEL=$($event.LevelDisplayName) MESSAGE=$message"
        }
    } catch {
        Add-Line "  EVENT_LOG_ERROR=$($_.Exception.Message)"
    }
}

Add-Section 'DONE'
Add-Line 'DIAGNOSTIC_COMPLETE=YES'
Write-Host "Diagnostic report: $ReportPath"
exit 0
