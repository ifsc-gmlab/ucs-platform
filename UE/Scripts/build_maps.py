# -*- coding: utf-8 -*-
"""
无头生成 SZTU 无人机的独立关卡（不打开编辑器，避免风场 editor-tick 卡死）。
用法（在 UnrealEditor-Cmd.exe 下通过 -ExecutePythonScript 调用）：
  - 生成 /Game/Maps/DroneTestMap ：仅无人机 + 光照 + 地面 + PlayerStart
  - 生成 /Game/Maps/FlagTestMap  ：仅旗子 + 光照 + PlayerStart
无人机关掉 bAutoSpawnWindField，确保纯无人机场景干净、不会触发风场卡死。
"""
import unreal


def log(msg):
    unreal.log("[BuildMaps] " + str(msg))


def get_cls(name, path):
    """获取 C++ 类：1) 反射属性 → 2) find_object（直接抓内存中的 UClass，不需 Python 绑定）→ 3) load_object。"""
    if hasattr(unreal, name):
        return getattr(unreal, name)
    cls = unreal.find_object(None, path)
    if cls is not None:
        log("get_cls: find_object 命中 " + path)
        return cls
    cls = unreal.load_object(None, path)
    if cls is not None:
        log("get_cls: load_object 命中 " + path)
        return cls
    raise RuntimeError("无法加载类: " + name + " (" + path + ")")


def spawn(world_lib, cls, location, rotation=None, label=None):
    actor = world_lib.spawn_actor_from_class(cls, location, rotation if rotation else unreal.Rotator(0.0, 0.0, 0.0))
    if actor and label:
        actor.set_actor_label(label)
    return actor


def build_drone_map():
    map_path = "/Game/Maps/DroneTestMap"
    eal = unreal.EditorAssetLibrary
    if eal.does_asset_exist(map_path):
        eal.delete_asset(map_path)
        log("已删除旧的 " + map_path)

    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    les.new_level(map_path)
    world_lib = unreal.EditorLevelLibrary
    if world_lib.get_editor_world() is None:
        raise RuntimeError("新建 DroneTestMap 后拿不到 world")

    # 无人机（关键：关闭自动生成风场，纯无人机场景才干净）
    drone_cls = get_cls("ASZTUDronePawn", "/Script/SZTUWindFieldPX4Demo.ASZTUDronePawn")
    drone = spawn(world_lib, drone_cls, unreal.Vector(0.0, 0.0, 120.0), label="SZTU_Drone")
    if drone:
        drone.set_editor_property("b_auto_spawn_wind_field", False)
        drone.set_editor_property("auto_possess_player", unreal.AutoReceiveInput.PLAYER0)
        log("已生成无人机 (bAutoSpawnWindField=False)")
    else:
        raise RuntimeError("无人机生成失败")

    # PlayerStart
    ps_cls = get_cls("PlayerStart", "/Script/Engine.PlayerStart")
    spawn(world_lib, ps_cls, unreal.Vector(0.0, 0.0, 0.0), label="PlayerStart")

    # 光照：方向光 + 天空光（保证无人机可见）
    dl_cls = get_cls("DirectionalLight", "/Script/Engine.DirectionalLight")
    spawn(world_lib, dl_cls, unreal.Vector(0.0, 0.0, 500.0), unreal.Rotator(-50.0, 0.0, 0.0), label="DirectionalLight")
    sl_cls = get_cls("SkyLight", "/Script/Engine.SkyLight")
    spawn(world_lib, sl_cls, unreal.Vector(0.0, 0.0, 300.0), label="SkyLight")

    # 地面（引擎内置 Plane，缩放成大方块做参照）
    sm_cls = get_cls("StaticMeshActor", "/Script/Engine.StaticMeshActor")
    ground = spawn(world_lib, sm_cls, unreal.Vector(0.0, 0.0, 0.0), label="Ground")
    if ground:
        plane = unreal.load_object(None, "/Engine/BasicShapes/Plane.Plane")
        if plane:
            comp = ground.get_editor_property("static_mesh_component")
            comp.set_editor_property("static_mesh", plane)
        ground.set_actor_scale3d(unreal.Vector(60.0, 60.0, 1.0))

    les.save_current_level()
    log("已保存 " + map_path)


def build_flag_map():
    map_path = "/Game/Maps/FlagTestMap"
    eal = unreal.EditorAssetLibrary
    if eal.does_asset_exist(map_path):
        eal.delete_asset(map_path)
        log("已删除旧的 " + map_path)

    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    les.new_level(map_path)
    world_lib = unreal.EditorLevelLibrary

    # 旗子（独立资产；此场景不放置风场，旗子静止，仅证明可独立摆放）
    flag_cls = get_cls("ASZTUWindFlag", "/Script/SZTUWindFieldPX4Demo.ASZTUWindFlag")
    flag = spawn(world_lib, flag_cls, unreal.Vector(0.0, 0.0, 0.0), label="SZTU_WindFlag")
    if flag is None:
        raise RuntimeError("旗子生成失败")
    log("已生成旗子（独立场景，无风场）")

    ps_cls = get_cls("PlayerStart", "/Script/Engine.PlayerStart")
    spawn(world_lib, ps_cls, unreal.Vector(-300.0, 0.0, 0.0), label="PlayerStart")

    dl_cls = get_cls("DirectionalLight", "/Script/Engine.DirectionalLight")
    spawn(world_lib, dl_cls, unreal.Vector(0.0, 0.0, 500.0), unreal.Rotator(-50.0, 0.0, 0.0), label="DirectionalLight")
    sl_cls = get_cls("SkyLight", "/Script/Engine.SkyLight")
    spawn(world_lib, sl_cls, unreal.Vector(0.0, 0.0, 300.0), label="SkyLight")

    les.save_current_level()
    log("已保存 " + map_path)


def main():
    log("开始无头生成独立关卡 ...")
    build_drone_map()
    build_flag_map()
    log("全部完成。")


if __name__ == "__main__":
    main()
