# 使用 QGroundControl 控制 UE5 中的无人机

## 启动 QGC 接管模式

在项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene no-wind -RestartQGC
```

调 PID 时每个 UE/PX4 会话只做一次起飞。降落并解锁后，不要在同一会话里改参数再起飞；先运行 `outputs\px4_emergency_stop.ps1 -KillSim`，再重新执行上面的启动命令。这样可以避免上一轮着陆留下的 EKF 高度/垂直速度残差污染下一组参数。

该入口会启动无风关卡、PX4、MAVLink 转发器和长时遥测，但不会启动 `takeoff_offboard.py`。因此不会有 OFFBOARD 脚本与 QGC 抢控制权。默认保留 15 分钟；可用 `-SessionSeconds 1800` 改成 30 分钟。

QGC 接管模式默认使用 UE DX11、`960x540` 和无音频运行，以降低 D3D12/系统页面文件不足导致的长时间运行崩溃风险；这不改变 PX4/UE 的飞行动力学链路。

启动器现在会在 PX4 报告 ready 后继续等待 `35 s`，然后才打开 QGC。看到终端输出 `ready`，并且 QGC 顶部稳定显示 `Ready to Fly` 后再操作；不要在这段地面收敛期内手动 Arm/Takeoff。

## 方法一：Fly 页面直接控制

这是第一次测试最推荐的方式：

1. 打开 QGC 的 **Fly** 页面。
2. 点击 **Takeoff**，高度先设为 `2 m`，确认执行。
3. 起飞后不下其他指令时，PX4 会保持当前位置。
4. 点击地图上的目标点，选择 **Go To Location** 并确认，可让飞机移动。
5. 点击地图上的目标点并选择 **Orbit**，可让飞机绕该点飞行；该选项需要有效位置估计。
6. 结束时点击 **Land** 并确认，等待 UE 中飞机贴地且 QGC 显示 Disarmed。

不要在空中使用 Emergency Stop；它等同于直接停转电机。

## 方法二：Plan 页面执行航点任务

1. 进入 **Plan**，新建任务并添加航点和最终 Land 指令。
2. 将任务上传到飞机。
3. 返回 **Fly**，选择 **Start Mission** 并确认。
4. 多旋翼任务启动时 QGC 会处理起飞流程；任务执行中可用 Pause 暂停并悬停。

## 方法三：虚拟摇杆或实体手柄

QGC 接管启动器已经为本轮 PX4 设置 `COM_RC_IN_MODE=1`，允许 MAVLink 摇杆输入。

- 虚拟摇杆：进入 **Application Settings > Fly View > Virtual joystick** 并启用。
- 实体手柄：进入 **Vehicle Setup > Joystick**，校准并在最后启用。
- 飞行前在 Fly 顶部选择适合多旋翼的 Position/Hold 类模式，先低高度小幅测试。

摇杆依赖连续 MAVLink 链路；如果 QGC 卡顿或断连，立即停止摇杆测试并使用正常 Land/Pause 流程。

## 切换到风场

仅在无风控制验证成功后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_qgc_control.ps1 `
  -Scene wind -WindMps 8 -RestartQGC
```

风场关卡会保留扇叶阵列和旗子。未显式传 `-WindMps` 时，QGC 风场模式也默认使用已通过裕量验收的 `8 m/s`；传 `-WindMps -1` 才恢复地图约 `12.86 m/s` 的压力工况。手动演示时先悬停，再逐步下 Go To 或摇杆指令。

## 结束和紧急处理

正常结束顺序：QGC **Land** -> 等待 Disarmed -> 清理仿真。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\px4_emergency_stop.ps1 -KillSim
Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
```

只有仿真失控且 QGC 无法降落时，才直接运行不带 `-KillSim` 的 `px4_emergency_stop.ps1` 强制 disarm；空中执行会导致飞机掉落。

## 常见问题

- QGC 只有遥测、无法操作：确认使用的是 `run_qgc_control.ps1`，而不是正在执行的自动悬停/绕圆脚本。
- 没有 Takeoff：点击顶部 Ready/Not Ready 状态查看 PX4 拒绝原因，等待位置和航向估计稳定。
- 没有 Joystick 页面：确认本轮由 QGC 接管启动器启动，PX4 日志中应包含 `COM_RC_IN_MODE` 设置为 `1`。
- 参数下载不完整：保持 UE/PX4 在线，关闭重复 QGC 实例并重新启动单个 QGC。
- 端口检查：`Get-NetUDPEndpoint -LocalPort 14550` 应显示 QGroundControl 进程。
- Takeoff 显示 `MAV_CMD_NAV_TAKEOFF` 未响应：检查 `/tmp/relay.log` 是否同时增长 `px4_to_qgc` 和 `qgc_to_px4`；QGC 回程必须转发到 PX4 本地监听端口 `18570`，不是 relay 自己的 `14550`。
- Takeoff 显示“命令被拒绝”：先确认 QGC 顶部已经稳定显示 `Ready to Fly`。relay 会记录 `QGC->PX4 COMMAND_LONG/INT` 和 `PX4->QGC COMMAND_ACK`，可直接核对 Arm、Takeoff 的顺序、参数和 ACK；不要只根据飞机画面判断。
- UE 突然退出：查看 `Saved\Crashes`。若为 D3D12 `OutOfMemory`，使用本启动器的 DX11 低内存模式，并避免同时运行其他高内存/GPU 应用。

当前 PX4 main 对 QGC 5.0.x 的 `MAV_CMD_NAV_TAKEOFF param1=-1` 已应用兼容补丁，补丁文件为 `outputs/px4-qgc-takeoff-p1-compat.patch`。不要通过 QGC 给本地 SITL 刷固件；重新构建 PX4 后应用该补丁并先运行 `outputs/probe_qgc_takeoff.py` 验证 ACK。

完整的 PX4 控制到 UE 执行、UE 状态经 PX4 返回 QGC 的链路见 `PX4_UE_QGC_DATA_FLOW.md`。
