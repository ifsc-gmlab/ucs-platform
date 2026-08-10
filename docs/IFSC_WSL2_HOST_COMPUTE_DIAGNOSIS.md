# IFSC 服务器 WSL2 主机计算诊断

## 已确认

- 服务器为物理机：Giga Computing `MZ72-HB2-00`。
- CPU：AMD EPYC 7742 64-Core Processor。
- Windows Server 2022 Standard，Build 20348。
- `Microsoft-Windows-Subsystem-Linux`：Enabled。
- `VirtualMachinePlatform`：Enabled。
- Hyper-V Server Role：Installed。
- `hypervisorlaunchtype`：Auto。
- `WslService`、`LxssManager`、`vmcompute`、`hns`：Running。
- WSL：2.6.1.0，内核 6.6.87.2-1。

因此先前“宿主机未开放嵌套虚拟化”的推断不成立。管理员诊断表明该机器自身就是物理 Hyper-V 主机，WSL2 所需 Windows 功能和启动项均已启用。

## 当前故障

- `djy` 用户的原有 `Ubuntu-24.04` 启动失败：`CreateInstance/CreateVm/HCS_E_CONNECTION_TIMEOUT`。
- 新 PX4 发行版导入失败：`RegisterDistro/CreateVm/HCS_E_CONNECTION_TIMEOUT`。
- 故障范围已收敛到 Windows 主机计算栈、HNS/WSL 状态或残留计算系统，而不是 PX4 镜像、D 盘或 Windows 可选功能。

## 后续安全顺序

1. 只读枚举本机 Hyper-V 虚拟机、虚拟交换机、HNS 网络和 HCS 计算系统，确认是否有业务虚拟机运行。
2. 在不影响其他虚拟机的前提下，关闭 `djy` 的 WSL 会话并清理残留 WSL 计算实例。
3. 优先只重启 WSL 相关服务；只有确认无业务虚拟机后才考虑重启 `vmcompute` 或 HNS。
4. 先验证原有 `Ubuntu-24.04` 能启动，再重试 PX4 TAR 导入。

完整管理员报告：`D:\Installers\server-results\WSL2_ADMIN_FEATURE_CHECK.txt`。
