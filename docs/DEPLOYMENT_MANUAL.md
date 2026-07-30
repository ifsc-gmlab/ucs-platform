# 七楼服务器部署手册

更新时间：2026-07-22。目标是在新的 Windows 10/11 机器上复现本项目的 UE5 + PX4 SITL-HIL + QGroundControl 环境，并按固定顺序完成连接、绕圆、QGC、风场、六旋翼、汽车和城市语义避障验收。

详细故障说明和历史基线见 `SERVER_REPRO.md`。本文是部署当天按顺序执行的主手册。

## 1. 出发前准备

至少带齐以下内容：

- 当前工作区，必须包含完整 `outputs/` 目录和本手册。
- 完整 UE 项目 `D:\SZTUWindFieldPX4Demo\Unreal`，必须包含 `.uproject`、`.vsconfig`、`Config/`、`Content/`、`Source/`、`Scripts/` 和 `SourceArt/`。
- UE 5.8、Visual Studio 2022、QGroundControl 的安装来源；服务器网络不可用时准备离线安装包。
- Windows 管理员权限、服务器 BIOS/系统虚拟化权限和足够磁盘空间。
- 若从私有 GitHub 仓库获取源码，仍需单独携带完整 `Content/`。不要假设源码仓库包含全部 `.uasset/.umap` 和已购买场景资产。
- 已购买工厂场景必须包含 `Content\TrainEnvironment`：当前为 `568` 个文件、约 `1.575 GiB`，原始地图为 `/Game/TrainEnvironment/Map/Demo`。来源和 SHA256 见 `THIRD_PARTY_ASSETS.md`；购买凭证需另行保存，不要超出授权范围分发。

可以不迁移 UE 项目的 `Binaries/`、`Intermediate/`、`Saved/`、`DerivedDataCache/` 和 `.vs/`；目标机重新生成。不能遗漏 `Content/`，关卡和材质都在其中。

出发前和目标机解包后都运行一次部署包检查；任何必需项缺失时不要进入编译：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\check_deployment_bundle.ps1
```

要求 `outputs\deployment_bundle_report.md` 显示 `Overall: PASS`。该检查明确覆盖原有六个项目实验关卡和城市语义关卡（含无风与有风开阔工厂园区）、工厂原始 Demo 及代表性材质/网格、六旋翼/汽车网格、PX4/QGC 脚本、语义控制器与地图、QGC 补丁和汽车 `51001` 自定义机架。

建议保持以下路径，现有 wrapper 可直接使用：

```text
C:\SZTUWindLab\
D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject
D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe
C:\IFSC-Station\IFSC-Station.exe
```

## 2. 固定版本

部署期间不要顺便升级组件。

| 组件 | 固定基线 |
| --- | --- |
| WSL | WSL2，Ubuntu 24.04；实际发行版名称可为 `Ubuntu` 或 `Ubuntu-24.04` |
| PX4 | `a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f` |
| PX4 描述 | `v1.18.0-alpha1-477-ga86306a146`，另应用本项目 QGC 兼容补丁 |
| Unreal Engine | UE 5.8 |
| QGC | 当前验证二进制 SHA256 `1031BDA2D3299547BA8711AB567C58638671AF1C1693AB1672D8341919C3840C`；不要通过 QGC 给本地 SITL 刷固件 |
| Python | Python 3.11.x，命令名为 `python` |

本项目不是 AirSim 工程，也不需要 Gazebo。PX4 simulator 的对端是本项目 UE 插件。

当前本机验收基线：无风悬停 `20260716-155033` PASS/PASS；无风绕圆 `20260716-165454` PASS/PASS；8 m/s 逆风 `20260716-162143` PASS；城市语义避障 `20260722-181051` PASS；QGC 启动链已连接并完成人工起降；六旋翼悬停已 PASS。汽车已验证 UE-PX4 SITL 连接，运动控制仍应作为目标机独立验收项，不写成既有 PASS。

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
Test-Path "C:\IFSC-Station\IFSC-Station.exe"
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

四旋翼、六旋翼和汽车共用同一个 PX4 rootfs。切换机型前必须停止旧会话；若新机型首次启动出现旧参数或执行器配置，清除参数缓存后重新启动该机型：

```powershell
wsl.exe -d $Distro -- bash -lc "rm -f ~/PX4-Autopilot/build/px4_sitl_default/rootfs/eeprom/parameters_*"
```

项目 launcher 会自动清理 `/tmp/px4_lock-0`、`/tmp/px4-sock-0`、`/tmp/px4_sitl.log` 和 `/dev/shm/px4*`。若仍出现 `server already running for instance 0`，先确认没有存活 PX4，再核对目标机使用的是最新 `outputs\start_px4_ue_hil.sh`，不要只手工反复删除锁文件。

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

若只缺少开阔工厂园区，不要运行会改写全部生成关卡的完整 commandlet。无风版使用窄重建参数：

```powershell
& "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
  "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject" `
  -run=SZTUWindFieldBuildMaps -FactoryMapOnly -unattended -nop4 -nosplash -NullRHI -NoSound
```

有风版单独重建，不覆盖无风基线：

```powershell
& "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
  "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject" `
  -run=SZTUWindFieldBuildMaps -FactoryWindMapOnly -unattended -nop4 -nosplash -NullRHI -NoSound
```

要求分别输出 `Factory map only: OK` 与 `Factory wind map only: OK`，并生成 `Content\Maps\SZTUFactoryFlightScene.umap`、`Content\Maps\SZTUFactoryWindScene.umap`。原始购买地图 `/Game/TrainEnvironment/Map/Demo` 不会被改写。

若城市语义关卡缺失或修改了城市布局生成源码，使用窄重建参数：

```powershell
& "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
  "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject" `
  -run=SZTUWindFieldBuildMaps -UrbanMapOnly -unattended -nop4 -nosplash -NullRHI -NoSound
```

要求输出 `Urban map only: OK`，并生成 `Content\Maps\SZTUUrbanSemanticScene.umap`。

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

## 8. 部署当天七级验收

每一级通过后再继续，失败时保留最新 `outputs\verification-runs\<run-id>`。

每次打开新的 PowerShell 都先设置实际发行版名，例如 `$Distro = "Ubuntu-24.04"`。

### Level 1：环境和 dry-run

```powershell
Set-Location "C:\SZTUWindLab"
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\check_deployment_bundle.ps1
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

开阔工厂园区使用相同 PX4 四旋翼链路，可单独跑 2 m 悬停回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 `
  -Map /Game/Maps/SZTUFactoryFlightScene -Vehicle quad -FlightPattern hover `
  -Alt 2 -Hold 6 -Climb 3 -Descend 8 -NoSound
```

QGC 手动控制无风版时使用 `run_qgc_control.ps1 -Scene factory -Distro $Distro -RestartQGC`。

有风工厂版使用同一 PX4 四旋翼链路，可跑 8 m/s 逆风闭环验收：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 `
  -Map /Game/Maps/SZTUFactoryWindScene -Vehicle quad -HeadwindTest -WindMps 8 `
  -Alt 2 -Hold 6 -Climb 3 -Descend 8 -EstimatorSettleSec 35 -NoSound
```

要求 `flight_analysis.md` 为 `PASS`、`headwind_motion_confirmed=True`、`Disarmed=True`、`Failsafe=False`。QGC 手动控制使用 `run_qgc_control.ps1 -Scene factory-wind -WindUI -WindMps 8 -Distro $Distro -RestartQGC`；滑块范围仍为 `0-15 m/s`。

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

风场 QGC 演示使用显式 UI 参数，不恢复调试箭头：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene wind -WindMps 8 -WindUI -Distro $Distro -RestartQGC
```

项目根目录还提供双击入口 `START_WIND_QGC_DEMO.cmd`。它固定复用上述受验证流程，并自动完成 `8 m/s` 初始风速、风速 UI、QGC 重启和 UE/QGC 左右分屏。等价 PowerShell 入口为：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\start_wind_qgc_demo.ps1
```

UI 范围为 `0-15 m/s`、步进 `0.5 m/s`。演示建议依次使用 `3/5/8 m/s`；`10 m/s` 已接近电机饱和边界。GameMode 会在关卡启动时自动隐藏旧关卡的世界空间 `TextRenderComponent`，不再需要执行控制台命令，且不会隐藏风速 UMG。

#### 工厂有风场景（SZTUFactoryWindScene）演示入口

工厂有风场景是当前主推演示图：80×40 扇阵风墙 + 软体布旗 + 黄色四旋翼 + 风速 UI（0-15 m/s，0.5 m/s 步进），中央 30×24 m 测试坪，外围集装箱/围栏/小屋作为参照。旗子物理参数已优化（`Mass=0.4`，风阻 `WindDragCoeff=8.0`），8 m/s 下旗面明显飘起；无人机颜色按场景区分（factory-wind=黄 `#FFE600`，其他=橙）。

固定观察相机为微距侧斜俯视 `(3, -3, 2.5) m` 看向 `(2.5, 0, 1.5) m`，FOV 60°，能看清无人机俯仰对风的响应。

**双击入口**（推荐）：项目根目录 `START_FACTORY_WIND_QGC.cmd`，自动以 8 m/s 风、风速 UI、QGC 重启启动。

**等价命令行**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene factory-wind -WindUI -WindMps 8 -Distro $Distro -RestartQGC
```

**演示流程**：
1. 等待 QGC 显示 `Ready to Fly`（UE+PX4 起来约 90s，EKF 收敛 35s）。
2. QGC `Fly > Takeoff` 起飞到 2 m。
3. 顶风悬停时观察 UE 画面：无人机俯仰角典型 15-26°（PX4 位置控制器抗风姿态）；旗面在风中飘扬。
4. 在 UE 左上角风速 UI 滑块切换 `3/5/8 m/s`，观察俯仰角和旗面飘动幅度的变化。
5. 降落并等待 `Disarmed`。
6. 停止仿真：`outputs\px4_emergency_stop.ps1 -KillSim`。

**已验证基线**（2026-07-20）：8 m/s 自动 OFFBOARD 闭环 `flight_analysis.md` PASS，平均风速 7.992 m/s，逆风净位移 2.274 m，最大俯仰 26.18°，最终 `Disarmed=True`、`Failsafe=False`。人工 QGC 手飞确认基本稳定。

### Level 6：六旋翼与汽车扩展验收

六旋翼使用 PX4 `6011` Typhoon H480 Hexa-X 和蓝色 UE 模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene hex -Distro $Distro -RestartQGC
```

要求 QGC 连接、六个执行器有输出、完成起飞/短悬停/降落，最终 `Disarmed`。汽车使用项目自带的外部仿真机架 `outputs\px4_airframes\51001_none_rover_ackermann` 和绿色 UE 模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene rover -Distro $Distro -RestartQGC
```

汽车没有“Ready for takeoff”语义；验收以 PX4-UE `connected=1`、QGC 解锁、虚拟摇杆直行/转向、回中停止和最终上锁为准。不要用多旋翼起飞判据判断汽车成功。

### Level 7：城市多传感器避障与语义驱动

先做无飞行 dry-run，再执行默认的 `start_pad` 到 `delivery_zone` 任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_semantic_city.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_semantic_city.ps1 `
  -Goal delivery_zone -ShowSensorRays
```

也可双击项目根目录的 `START_SEMANTIC_CITY.cmd`。要求最新 `outputs\semantic-runs\<run-id>\summary.md` 为 `PASS`，并同时满足：语义目标已到达、至少 1 次传感器触发避障、障碍碰撞为 0、感知样本不少于 20、最终已落地并解锁。当前基线 `20260722-181051` 的到达误差为 0.75 m，避障 1 次、碰撞 0 次、感知样本 9871。

本级当前实现是“语义名称到预置坐标 + 前/左/右/下测距 + 可解释反应式绕障 + PX4 OFFBOARD”的工程基线。机载相机已接入 UE，但尚未接入目标检测、语义分割或学习型策略；不能把该基线描述成基于相机的完整视觉语义导航。详细边界与扩展接口见 `SEMANTIC_CITY.md`。

## 9. AI 独立部署边界

AI 可以按本文执行文件检查、命令安装、补丁状态判断、编译、端口诊断、自动测试和报告归档，但不能替代以下人工步骤：

- 提供管理员权限，处理 BIOS 虚拟化、Windows 重启和组织网络策略。
- 登录 Epic/GitHub/网盘，获取 UE 5.8、私有仓库、完整 `Content/` 与已购买场景授权文件。
- 确认服务器 GPU/显示驱动和 QGC 图形界面实际可见，并在首次 QGC 手动飞行时承担安全操作。

因此当前手册已经适合让新机上的 AI 主导部署，但在七楼机器完成一次从空目录开始的 Level 1-7 演练前，不能宣称“无需人工即可独立完成”。AI 必须在部署包检查、环境检查、编译或任一级验收失败时停止，保留报告并诊断，不能跳级或用视觉现象代替 PASS 证据。

## 10. 正常停止与记录

完成飞行并确认已降落后：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

记录服务器 Windows/Ubuntu/UE/QGC 版本、PX4 commit、实际路径、每级 run ID、PASS/CHECK 和未解决警告。

## 11. 现场优先排错

| 现象 | 先检查 |
| --- | --- |
| UE 4560 超时 | UE/项目路径、旧 UE 进程、防火墙 |
| PX4 连不上 UE | 默认 WSL 发行版、PX4 binary、Windows 网关 |
| `pymavlink` 缺失 | WSL 中重新运行 PX4 `ubuntu.sh` |
| QGC 参数不完整 | 用 `run_qgc_control.ps1` 保持 UE/PX4/relay 在线 |
| QGC 起飞被拒绝 | 兼容补丁是否应用、PX4 是否重新构建 |
| UE D3D12 崩溃 | 使用 wrapper 的 DX11 960x540，增大页面文件 |
| `server already running` | 旧 PX4 进程和最新 launcher 的 `/tmp/px4_lock-0`/socket 清理 |
| 切换六旋翼后仍是四旋翼参数 | 停止旧会话，清 rootfs `eeprom/parameters_*`，再以 `-Scene hex` 启动 |
| 汽车连接但不出现 Ready to Fly | 不使用起飞判据；检查 `51001`、执行器 101/201、QGC 虚拟摇杆和 UE 位移 |

官方参考：

- <https://docs.px4.io/main/en/dev_setup/dev_env_windows_wsl>
- <https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html>
