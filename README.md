# UCS PX4 + UE5 HIL Platform

本仓库保存光明项目中可版本管理的 UE5、PX4/QGC 联调源码、运行脚本、语义任务计划和实验文档。

## 仓库结构

- `UE/`：Unreal 项目描述文件、配置、C++ 源码、构建脚本和可再生成的源素材。
- `scripts/`：PX4/UE HIL 启动、诊断、飞行测试、网页控制台和语义任务脚本。
- `test-plans/`：语义任务的 JSON 计划。
- `docs/`：部署、数据流、飞行模式、PID、语义场景和复现实验说明。

## 恢复到本地工作区

1. 准备 UE 5.8、WSL/PX4 SITL、QGroundControl 和 Python 环境。
2. 将 `UE/` 中的内容合并到本地 Unreal 项目目录。
3. 在实验工作区建立 `outputs/`，将 `scripts/` 和 `test-plans/` 的内容分别放入 `outputs/` 与 `outputs/test-plans/`。
4. 按 `docs/DEPLOYMENT_MANUAL.md` 配置本机路径和依赖，再先运行 DryRun：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_semantic_city.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\outputs\run_semantic_regression.ps1 -DryRun
python .\outputs\verify_dashboard_test.py
```

完整 UE `Content/` 及大型二进制资源不在本仓库中；它们需要从项目资产包恢复，其中部分场景资源可由 `UE/SourceArt` 和构建命令重新生成。不要把缓存、运行日志、截图、密钥或本机凭据提交到仓库。

## 坐标约定

UE、PX4、路线规划器、任务命令和原始遥测保持内部坐标不变。网页显示层单独使用 `显示 X = 内部 X`、`显示 Y = -内部 Y`、`显示偏航 = -内部偏航`，页面方向调整不会影响飞行控制逻辑。

更详细的语义任务和验收方法见 `docs/SEMANTIC_CITY.md`。
