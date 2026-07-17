# UE5 场景购买与导入清单

目标是把新场景作为美术环境导入当前 UE 5.8 项目，同时保留现有 PX4、无人机动力学、相机、日志和风场链路。

## 购买前确认

- 必须拿到可编辑的 Unreal 项目或 `.uasset` 源资产，只有打包后的 `.exe` 不能导入。
- 优先选择 UE 5.8 或更早版本的资产；较早版本通常可由 UE 5.8 升级副本，较新版本资产不能可靠降级。
- 让卖家明确列出依赖插件、资产来源、贴图是否齐全、是否使用 World Partition、Nanite、Lumen 和付费第三方插件。
- 确认授权允许当前实习项目使用和演示，并保留交易与授权记录。来源或转售授权不清楚的资产不要并入最终交付项目。
- 要求提供一张 Content Browser 目录图和一段编辑器内运行视频，避免买到只有渲染图或缺依赖的工程。

## 导入原则

1. 先备份 `D:\SZTUWindFieldPX4Demo\Unreal`。
2. 用 UE 5.8 单独打开购买的项目并让它完成升级/编译，确认原项目场景能正常打开。
3. 在来源项目 Content Browser 中选择场景根目录或目标地图，执行 `Asset Actions > Migrate`。
4. 查看 Asset Report，保留地图依赖的材质、纹理、网格和蓝图。
5. 目标必须选择 `D:\SZTUWindFieldPX4Demo\Unreal\Content`。
6. 若提示覆盖当前项目已有资产，默认选 `No`；不要覆盖 `Maps/SZTUFlightPatternsScene`、`Maps/SZTUWindFieldDemoScene` 或现有 SZTU 资产。

Epic 的 Migrate 工具会连同引用依赖一起复制；直接复制零散 `.uasset` 更容易丢材质和纹理。

## 接入本项目

- 不要把购买项目的 `Config/`、`Source/`、`.uproject` 直接覆盖到当前项目。
- 保留全局 GameMode `/Script/SZTUWindFieldPX4Demo.SZTUWindFieldGameMode` 和默认 Pawn `SZTUDronePawn`。
- 第一轮只把新环境加入无风测试，不添加风场 Actor，不改 PX4/TCP 4560 组件。
- 从导入地图复制环境 Actor 到一个新的项目地图，例如 `/Game/Maps/SZTUImportedScene01`；清理来源项目自带 Pawn、PlayerStart、GameMode、相机和飞行蓝图，避免出现第二架飞机或抢占控制权。
- 为无人机留出至少覆盖完整绕圆轨迹的无碰撞空间；默认圆半径 1.5 m，另加进入、返航和降落区域。
- 地面必须有碰撞，起飞点地面高度作为 UE Z=0 基准；复杂地形先放一个不可见或低干扰的平整碰撞平台做首轮联调。
- 固定观察相机对新地图可能需要重新取景，但不要通过修改动力学或坐标系来适配画面。

## 导入后验收

1. 编辑器打开新地图，检查 Missing Asset、编译错误、黑材质和碰撞。
2. 先仅运行 UE，确认地图可加载且没有第二个 Pawn。
3. 给 wrapper 临时传新地图做 `-SkipFlight` 连接测试。
4. 先跑 hover，再跑 `run_circle_flight.ps1` 对应的圆轨迹参数。
5. 检查 `summary.md`、`flight_analysis.md` 和 `pattern_analysis.md`，不能只看画面。

正式导入时先把卖家提供的项目路径和 UE 版本发给 Codex，再做迁移；不要直接双击升级当前主项目。

官方迁移说明：<https://dev.epicgames.com/documentation/en-us/unreal-engine/migrating-assets-in-unreal-engine>
