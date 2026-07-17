# UE5 无风悬停与绕圆

该流程使用独立关卡 `/Game/Maps/SZTUFlightPatternsScene`。关卡只保留无人机、地面、光照和固定观察相机，不生成风墙、扇叶阵列、旗子或风采样。

## 环境

本机按 PX4 官方 [Windows WSL2 开发环境文档](https://docs.px4.io/main/en/dev_setup/dev_env_windows_wsl)核查：PX4 源码位于 WSL 的 `~/PX4-Autopilot`，不从 `/mnt/c` 构建；Ubuntu 24.04、递归子模块、`px4_sitl` 和 `pymavlink` 均由检查脚本验证。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\check_px4_wsl_env.ps1
```

检查结果写入 `outputs\px4_wsl_env_report.md`。

## 运行

只悬停并软着陆：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_flight_patterns.ps1 -Pattern hover
```

先悬停，再绕圆一周并软着陆（推荐的一键入口）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_circle_flight.ps1
```

默认轨迹为高度 1 m、半径 1.5 m、14 秒一周。轨迹在 local NED 平面按 `x=r*cos(theta)`、`y=r*sin(theta)`、`z=-alt` 生成，并以 10 Hz 发送位置 setpoint。参数化运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_circle_flight.ps1 `
  -Alt 2 -Radius 2 -Laps 2 -Duration 24
```

底层实现仍在 `outputs\takeoff_offboard.py::fly_circle`；一键入口复用通用启动、日志分析、触地确认和清理流程，不维护第二套飞控代码。

当前模型的实飞回归 `20260716-165454` 为 Flight/Pattern `PASS/PASS`：默认一周轨迹完成，绕圆段高度标准差 `0.023 m`、5–95% 范围 `0.078 m`，最终落地、解锁且无 failsafe。

## QGroundControl

可以在运行前打开 Windows 版 QGC。WSL 中的 `mavlink_relay.py` 会把 PX4 GCS 遥测转发到 Windows UDP `14550`，QGC 通常会自动发现。QGC 是观察与验收界面，OFFBOARD 轨迹仍由 `takeoff_offboard.py` 控制。

如果要让 QGC 直接控制，而不是只观察，不要运行悬停/绕圆 OFFBOARD 脚本。改用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene no-wind -RestartQGC
```

完整的 Takeoff、Go To、Orbit、Plan 任务和摇杆操作见 `QGC_CONTROL.md`。

首次连接 QGC 或需要重新下载完整参数时，应使用 `-KeepRunning`，让 UE、PX4 和 MAVLink 转发器在飞行结束后继续在线：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_flight_patterns.ps1 `
  -Pattern hover -KeepRunning
```

等终端出现 `KeepRunning set` 后再打开或重启 QGC。若 QGC 提示“无法检索完整的参数集”，通常是上一轮自动清理时 PX4 在参数下载完成前断开；确认当前 PX4 仍在线，关闭提示并让 QGC 重新连接即可。Windows 端可用下面的命令确认 QGC 正在监听 `14550`：

```powershell
Get-NetUDPEndpoint -LocalPort 14550
```

结束后清理：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

## 验收

每次运行都会生成 `outputs\verification-runs\<run-id>`。重点查看：

- `summary.md`：UE/PX4/OFFBOARD/起飞/着陆状态。
- `flight_analysis.md`：通用飞行安全指标。
- `pattern_analysis.md`：零风、悬停漂移或绕圆跨度、路径长度和落地判定。

## 垂直悬停参数

当前 AirSim 风格动力学模型的验证参数为 `MPC_Z_P=1.5`、`MPC_Z_VEL_P_ACC=3.0`、`MPC_Z_VEL_I_ACC=1.5`、`MPC_Z_VEL_D_ACC=0`、`MPC_THR_HOVER=0.62`。所有标准入口默认等待估计器在地面收敛 `35 s`；不要为了缩短演示时间跳过该等待。A/B 指标、失败方案和 ULog 分析方法见 `VERTICAL_PID_TUNING.md`。

可见的低频上下运动首先暴露了状态估计问题，而不是继续增大 PID 就能解决。UE 约 50 Hz 发送 local-NED `ODOMETRY`，PX4 使用 `EKF2_EV_CTRL=6`、`EKF2_HGT_REF=3`：融合视觉垂直位置和三维速度，但不融合视觉水平位置。移植 RPM 动态、二次方推力和线性阻力后，最新无风独立复测 `20260716-110443` 为 Flight/Pattern `PASS`：3 m 悬停高度标准差 `0.032 m`、5-95% 范围 `0.107 m`、最大水平漂移 `0.002 m`、最终着陆并解锁、无 failsafe。

着陆末段采用两级控制：先用位置 setpoint 软降到地面附近，再保持 XY 位置并把 NED Z 向下速度在 12 秒内从 `0.12 m/s` 平滑增加到最多 `0.25 m/s`。只有 UE HIL 连续确认 `abs(posz) < 0.025 m` 后才允许在 PX4 landed detector 滞后时 force-disarm；超时且未触地必须判定失败。

PX4、UE5 与 QGC 的双向控制/状态信息流见 `PX4_UE_QGC_DATA_FLOW.md`。
