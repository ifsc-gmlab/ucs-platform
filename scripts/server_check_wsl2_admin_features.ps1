[CmdletBinding()]
param([string]$ReportPath = 'D:\Down\WSL2_ADMIN_FEATURE_CHECK.txt')
# Deployment copy: use D:\Installers\server_check_wsl2_admin_features.ps1 on the IFSC server.
& 'D:\Installers\server_check_wsl2_admin_features.ps1' -ReportPath $ReportPath
exit $LASTEXITCODE
