# 七楼服务器复现说明：UE5 + PX4 + QGC 风场逆风测试

本文用于把当前本机已通过的 UE5 风场抗风测试迁移到七楼服务器复现。目标不是接入 AirSim，而是复现当前项目自己的 UE5 + PX4 SITL-HIL + QGC 旁路遥测链路。

从零安装和部署当天的逐步命令以 `DEPLOYMENT_MANUAL.md` 为主；本文保留当前基线、演示方式和故障解释。

## 当前本机基线

- 无风悬停 run：`20260716-155033`，Flight/Pattern 为 `PASS/PASS`。
- 无风绕圆 run：`20260716-165454`，Flight/Pattern 为 `PASS/PASS`；半径 `1.5 m`、一周 `14 s`，绕圆段高度标准差 `0.023 m`、5–95% 范围 `0.078 m`，最终落地解锁。
- 8 m/s 逆风 run：`20260716-162143`，Flight 为 `PASS`，`headwind_motion_confirmed=True`，平均风速 `8.024 m/s`，净 X 位移 `-2.09 m`。
- 风速分级报告：`outputs\WIND_SPEED_SWEEP_20260716-160010.md`，3/5/8 m/s PASS，10 m/s 因电机裕量仅 `0.016` 而 CHECK。
- QGC 启动链 run：`20260716-163801`，UE/PX4 ready 且 QGC UDP `14550` 已验证；随后人工完成 QGC 起飞和降落。
- 关键标志：
  - `Offboard=True`
  - `TakeoffDetected=True`
  - `Disarmed=True`
  - `Failsafe=False`
  - `TelemetryPass=True`
  - `headwind_motion_confirmed=True`
- 当前 Demo 行为：
  - UE 里展示固定第三视角；
  - 风场 debug 箭头关闭；
  - 扇叶阵列/风墙保留；
  - 旗子表示风向；
  - PX4 进入 OFFBOARD 后逆风飞行，再软着陆并 disarm。

## QGC 是否必须

自动验证不依赖 QGC。

- 自动控制链路：`takeoff_offboard.py` 在 WSL 内直接连 PX4 UDP `127.0.0.1:18570`。
- QGC 旁路遥测链路：WSL `mavlink_relay.py` 将 PX4 GCS MAVLink 流转到 Windows UDP `14550`，QGC 自动接收。

所以：

- 跑自动日志验证：可以不开 QGC。
- 给导师或在服务器上演示复现：建议打开 QGC。它能证明 PX4 遥测真实存在，而不是单纯 UE 视觉动画。

## 服务器环境清单

Windows 侧：

- Unreal Engine 5.8，默认路径假设：`D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe`
- UE 项目，默认路径假设：`D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject`
- PowerShell 5.1+
- Python，可运行 `outputs\listen_telem_long.py` 和 `outputs\analyze_flight_run.py`
- QGroundControl，建议路径：`C:\QGroundControl\bin\QGroundControl.exe`
- 防火墙允许：
  - TCP `4560`：UE simulator port，PX4 连接 UE；
  - UDP `14550`：QGC 接收 PX4 遥测。

WSL Ubuntu 侧：

- PX4-Autopilot 已构建，默认路径：`~/PX4-Autopilot`
- PX4 binary 存在：`~/PX4-Autopilot/build/px4_sitl_default/bin/px4`
- `pymavlink` 可用
- `~/szefitl` 目录包含：
  - `start_px4_ue_hil.sh`
  - `takeoff_offboard.py`
  - `mavlink_relay.py`
  - `px4_force_disarm.py`

本机对应同步来源：

```powershell
Copy-Item -LiteralPath .\outputs\start_px4_ue_hil.sh -Destination \\wsl.localhost\Ubuntu\home\reisei0824\szefitl\start_px4_ue_hil.sh -Force
Copy-Item -LiteralPath .\outputs\takeoff_offboard.py -Destination \\wsl.localhost\Ubuntu\home\reisei0824\szefitl\takeoff_offboard.py -Force
Copy-Item -LiteralPath .\outputs\px4_force_disarm.py -Destination \\wsl.localhost\Ubuntu\home\reisei0824\szefitl\px4_force_disarm.py -Force
Copy-Item -LiteralPath .\outputs\mavlink_relay.py -Destination \\wsl.localhost\Ubuntu\home\reisei0824\szefitl\mavlink_relay.py -Force
```

如果服务器的 WSL 用户不是 `reisei0824`，把路径里的用户名改成服务器实际用户名。

## 推荐复现流程

先在项目根目录打开 PowerShell：

```powershell
cd "C:\Users\49337\OneDrive\Desktop\Desktop\实习\光明"
```

服务器路径不同的话，进入服务器上的实际项目根目录即可。

### 1. 预检查

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 -DryRun
```

如果 UE 或项目路径不同，显式传入：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 `
  -UnrealEditor "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" `
  -Project "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject" `
  -DryRun
```

### 2. 连接 smoke test，不起飞

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\verify_px4_ue_hil.ps1 -SkipFlight
```

成功判据：

- `UEStarted=True`
- `UEPortListening=True`
- `PX4Started=True`
- `PX4Connected=True`
- `PX4Ready=True`
- `TelemetryStarted=True`

### 3. 完整自动逆风验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_headwind_test.ps1
```

入口默认使用已验证的 `8 m/s` 风速。单档抗风悬停和完整分级扫描分别使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_wind_hover.ps1 -WindMps 8
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_wind_speed_sweep.ps1 -WindSpeeds "3,5,8,10"
```

本机扫描 `WIND_SPEED_SWEEP_20260716-160010.md` 显示 3、5、8 m/s PASS；10 m/s 因最大电机均值 `0.984`、裕量仅 `0.016` 而 CHECK。服务器演示优先使用 8 m/s，不把约 13 m/s 压力工况当作稳定基线。

成功判据：最新 `outputs\verification-runs\<run-id>\flight_analysis.md` 中：

- `Verdict: PASS`
- `Offboard=True`
- `TakeoffDetected=True`
- `Disarmed=True`
- `Failsafe=False`
- `TelemetryPass=True`
- `headwind_motion_confirmed=True`

### 4. 给导师看的 QGC 演示模式

QGC 直接控制无风起降：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene no-wind -RestartQGC
```

等待 QGC 显示 `Ready to Fly`，执行起飞、悬停/Go To/Orbit、降落并等待 `Disarmed`。此模式不能同时运行 OFFBOARD 自动脚本。

如需让 QGC 只观察自动 8 m/s 逆风测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_headwind_test.ps1 -KeepRunning
```

终端显示 `KeepRunning set` 后打开 QGC。演示时同时看 UE 运动、QGC 遥测和最新 `flight_analysis.md`；QGC 此时不是命令所有者。

演示结束后清理：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

## 常见问题

### UE 没有监听 TCP 4560

表现：`UE listening on TCP 4560` timeout。

检查：

- UE 路径是否正确；
- 项目是否能正常打开；
- 地图是否为 `/Game/Maps/SZTUWindFieldDemoScene`；
- 是否有旧 UnrealEditor 卡住。

可手动清理：

```powershell
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

### PX4 没连上 UE

表现：`PX4 simulator connected` timeout。

检查：

- UE 是否已经监听 TCP `4560`；
- WSL 能否取到 Windows 网关 IP；
- `~/szefitl/start_px4_ue_hil.sh` 是否存在；
- `~/PX4-Autopilot/build/px4_sitl_default/bin/px4` 是否存在。

WSL 内可查：

```bash
test -x ~/PX4-Autopilot/build/px4_sitl_default/bin/px4 && echo OK
cat /tmp/px4_sitl.log | tail -80
```

### QGC 没自动连接

自动验证仍可能成功，因为 QGC 不是控制依赖。

若演示需要 QGC：

```powershell
wsl bash -lc "pgrep -af mavlink_relay.py; cat /tmp/relay.log 2>/dev/null | tail -80"
```

重点检查：

- QGC 是否监听 Windows UDP `14550`；
- Windows 防火墙是否放行 QGC；
- `mavlink_relay.py` 是否正在运行；
- relay 日志是否显示发往 Windows 网关。

### OFFBOARD 失败或没有起飞

看最新 run：

```powershell
Get-ChildItem .\outputs\verification-runs -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

再打开：

- `summary.md`
- `flight_analysis.md`
- `takeoff_offboard.log`
- `px4_sitl.log`

常见原因：

- PX4 EKF/健康状态未稳定，导致 OFFBOARD 被拒；
- 旧 PX4/relay/UE 残留进程污染端口；
- 脚本未同步到 WSL 的 `~/szefitl`。

先清理再跑：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

## AirSim 参考关系

当前项目没有引入 AirSim 作为依赖。AirSim 只作为架构参考：

- UE 仿真端 + PX4 SITL/HIL 的分层方式；
- PX4 通过 TCP `4560` 连 UE simulator；
- QGC/MAVLink 作为旁路遥测和验收视图；
- WSL2 网络、PX4 重启和日志组织经验；
- offboard 前等待状态稳定、记录传感器/姿态/执行器日志。

当前项目对应关系：

- `ASZTUDronePawn` 类似 AirSim 的 multirotor pawn + simulated vehicle bridge；
- UE HIL/MAVLink 组件类似 AirSim 的 PX4 firmware API / adapter；
- `mavlink_relay.py` 类似 AirSim 场景里 WSL 到 Windows QGC 的网络桥；
- `analyze_flight_run.py` 是当前项目自己的飞行日志验收器。

详细对照见：

```text
outputs\PX4_UE_HIL_recap_AirSim_2026-07-13.md
```

## 交付建议

服务器复现时建议按这个顺序向导师展示：

1. 先跑 `-SkipFlight`，证明 UE 和 PX4 能连。
2. 再跑完整逆风验证，证明 OFFBOARD、起飞、逆风移动、软着陆和日志分析通过。
3. 最后跑 `-KeepRunning` 并打开 QGC，证明 PX4 遥测链路也能被外部地面站看到。

如果三步都过，当前阶段可以视为“服务器复现通过”。
