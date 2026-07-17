# 七楼服务器部署手册

更新时间：2026-07-16。目标是在新的 Windows 10/11 机器上复现本项目的 UE5 + PX4 SITL-HIL + QGroundControl 环境，并按固定顺序完成连接、绕圆、QGC 和风场验收。

详细故障说明和历史基线见 `SERVER_REPRO.md`。本文是部署当天按顺序执行的主手册。

## 1. 出发前准备

至少带齐以下内容：

- 当前工作区，必须包含完整 `outputs/` 目录和本手册。
- 完整 UE 项目 `D:\SZTUWindFieldPX4Demo\Unreal`，必须包含 `.uproject`、`.vsconfig`、`Config/`、`Content/`、`Source/`、`Scripts/` 和 `SourceArt/`。
- UE 5.8、Visual Studio 2022、QGroundControl 的安装来源；服务器网络不可用时准备离线安装包。
- Windows 管理员权限、服务器 BIOS/系统虚拟化权限和足够磁盘空间。

可以不迁移 UE 项目的 `Binaries/`、`Intermediate/`、`Saved/`、`DerivedDataCache/` 和 `.vs/`；目标机重新生成。不能遗漏 `Content/`，关卡和材质都在其中。

建议保持以下路径，现有 wrapper 可直接使用：

```text
C:\SZTUWindLab\
D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject
D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe
C:\QGroundControl\bin\QGroundControl.exe
```

## 2. 固定版本

部署期间不要顺便升级组件。

| 组件 | 固定基线 |
| --- | --- |
| WSL | WSL2，Ubuntu 24.04；实际发行版名称可为 `Ubuntu` 或 `Ubuntu-24.04` |
| PX4 | `a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f` |
| PX4 描述 | `v1.18.0-alpha1-477-ga86306a146`，另应用本项目 QGC 兼容补丁 |
| Unreal Engine | UE 5.8 |
| QGC | 当前 Windows 版；不要通过 QGC 给本地 SITL 刷固件 |
| Python | Python 3，命令名为 `python` |

本项目不是 AirSim 工程，也不需要 Gazebo。PX4 simulator 的对端是本项目 UE 插件。

当前本机验收基线：无风悬停 `20260716-155033` PASS/PASS；无风绕圆 `20260716-165454` PASS/PASS；8 m/s 逆风 `20260716-162143` PASS；QGC 启动链 `20260716-163801` 已连接并人工完成起降。

## 3. 安装 Windows 侧

1. 安装 UE 5.8。
2. 安装 Visual Studio 2022，并导入 UE 项目根目录的 `.vsconfig`。
3. 安装 Python 3 和 QGroundControl。
4. 将工作区和 UE 项目放到第 1 节路径。

检查：

```powershell
python --version
Test-Path "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
Test-Path "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject"
Test-Path "C:\QGroundControl\bin\QGroundControl.exe"
```

需要 ULog 分析时安装：

```powershell
python -m pip install numpy pyulog
```

## 4. 安装 WSL2 和 PX4

管理员 PowerShell：

```powershell
wsl --install -d Ubuntu-24.04
wsl -l -v
wsl --set-version Ubuntu-24.04 2
wsl --set-default Ubuntu-24.04
```

如果 `wsl -l -v` 中名称不同，后两条使用实际名称。以下用变量保存：

```powershell
$Distro = "Ubuntu-24.04"
```

进入 WSL 后，把 PX4 放在 Linux 文件系统，不要放到 `/mnt/c` 或 `/mnt/d`：

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates
cd ~
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
cd ~/PX4-Autopilot
git checkout a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f
git submodule sync --recursive
git submodule update --init --recursive
bash Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools
exit
```

然后重启 WSL：

```powershell
wsl --shutdown
wsl -d $Distro -- bash -lc "python3 -c 'import pymavlink; print(pymavlink.__file__)'"
```

PX4 官方文档同样要求安装和构建操作在 WSL 文件系统内完成，并支持 Ubuntu 24.04 与 `--no-nuttx --no-sim-tools` 选项。

## 5. 应用 QGC 起飞兼容补丁

从工作区根目录执行：

```powershell
Set-Location "C:\SZTUWindLab"
$Patch = (Resolve-Path ".\outputs\px4-qgc-takeoff-p1-compat.patch").Path
$Drive = $Patch.Substring(0,1).ToLowerInvariant()
$PatchWsl = "/mnt/$Drive/" + ($Patch.Substring(3) -replace "\\", "/")

& wsl.exe -d $Distro -- bash -lc "cd ~/PX4-Autopilot && git apply --check '$PatchWsl'"
if ($LASTEXITCODE -eq 0) {
  & wsl.exe -d $Distro -- bash -lc "cd ~/PX4-Autopilot && git apply '$PatchWsl'"
} else {
  & wsl.exe -d $Distro -- bash -lc "cd ~/PX4-Autopilot && git apply --reverse --check '$PatchWsl'"
  if ($LASTEXITCODE -ne 0) { throw "QGC patch is neither applicable nor already applied" }
  Write-Host "QGC compatibility patch is already applied"
}

wsl.exe -d $Distro -- bash -lc "cd ~/PX4-Autopilot && PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin make px4_sitl"
if ($LASTEXITCODE -ne 0) { throw "PX4 build failed" }
```

检查：

```powershell
wsl.exe -d $Distro -- bash -lc "git -C ~/PX4-Autopilot rev-parse HEAD"
wsl.exe -d $Distro -- bash -lc "git -C ~/PX4-Autopilot status --short"
wsl.exe -d $Distro -- bash -lc "test -x ~/PX4-Autopilot/build/px4_sitl_default/bin/px4 && echo PX4_BINARY_OK"
```

## 6. 编译 UE 项目

普通 PowerShell：

```powershell
& "D:\Epic Games\UE_5.8\Engine\Build\BatchFiles\Build.bat" `
  SZTUWindFieldPX4DemoEditor Win64 Development `
  -Project="D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject" `
  -WaitMutex -NoHotReloadFromIDE -NoUBA -MaxParallelActions=1

if ($LASTEXITCODE -ne 0) { throw "UE build failed" }
```

`-NoUBA -MaxParallelActions=1` 是低内存构建方式。正常迁移完整 `Content/` 时不要运行地图 commandlet；只有修改地图生成源码或缺失生成关卡时才重建地图。

## 7. 防火墙

管理员 PowerShell：

```powershell
if (-not (Get-NetFirewallRule -DisplayName "UE5 SITL TCP 4560" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName "UE5 SITL TCP 4560" -Direction Inbound -Protocol TCP -LocalPort 4560 -Action Allow -Profile Any
}
if (-not (Get-NetFirewallRule -DisplayName "PX4 QGC UDP 14550" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName "PX4 QGC UDP 14550" -Direction Inbound -Protocol UDP -LocalPort 14550 -Action Allow -Profile Any
}
```

端口：TCP `4560` 为 PX4 到 UE；UDP `18570` 为 WSL 内 OFFBOARD；UDP `14550` 为 QGC；UDP `14660` 为 UE 验证遥测。

## 8. 部署当天五级验收

每一级通过后再继续，失败时保留最新 `outputs\verification-runs\<run-id>`。

每次打开新的 PowerShell 都先设置实际发行版名，例如 `$Distro = "Ubuntu-24.04"`。

### Level 1：环境和 dry-run

```powershell
Set-Location "C:\SZTUWindLab"
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\check_px4_wsl_env.ps1 -Distro $Distro
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 -DryRun
```

要求环境报告 `Overall: PASS`，dry-run 退出 0。

### Level 2：UE-PX4 连接，不飞

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 -SkipFlight
```

要求 `UEStarted/PX4Started/PX4Connected/PX4Ready=True`。

### Level 3：无风绕圆自动验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_circle_flight.ps1
```

要求 `flight_analysis.md` 与 `pattern_analysis.md` 都为 `PASS`，并且最终 `Disarmed=True`、`Failsafe=False`。

### Level 4：QGC 手动起降

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 -Scene no-wind -Distro $Distro -RestartQGC
```

等待 `Ready to Fly`，再执行 2 m 起飞、短暂悬停、降落并等待 `Disarmed`。QGC 直接控制时不得同时运行自动 OFFBOARD 脚本。

### Level 5：8 m/s 逆风验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_headwind_test.ps1
```

要求两个分析报告 `PASS`，且 `headwind_motion_confirmed=True`。8 m/s 是演示基线；约 13 m/s 是压力工况。

## 9. 正常停止与记录

完成飞行并确认已降落后：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

记录服务器 Windows/Ubuntu/UE/QGC 版本、PX4 commit、实际路径、每级 run ID、PASS/CHECK 和未解决警告。

## 10. 现场优先排错

| 现象 | 先检查 |
| --- | --- |
| UE 4560 超时 | UE/项目路径、旧 UE 进程、防火墙 |
| PX4 连不上 UE | 默认 WSL 发行版、PX4 binary、Windows 网关 |
| `pymavlink` 缺失 | WSL 中重新运行 PX4 `ubuntu.sh` |
| QGC 参数不完整 | 用 `run_qgc_control.ps1` 保持 UE/PX4/relay 在线 |
| QGC 起飞被拒绝 | 兼容补丁是否应用、PX4 是否重新构建 |
| UE D3D12 崩溃 | 使用 wrapper 的 DX11 960x540，增大页面文件 |

官方参考：

- <https://docs.px4.io/main/en/dev_setup/dev_env_windows_wsl>
- <https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html>
