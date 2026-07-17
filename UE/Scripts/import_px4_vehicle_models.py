"""Import generated PX4 vehicle OBJ meshes into the UE project."""

from __future__ import annotations

import os

import unreal


def log(message: str) -> None:
    unreal.log(f"[PX4 Vehicle Import] {message}")


project_root = os.path.abspath(unreal.Paths.project_dir())
source_root = os.path.join(project_root, "SourceArt", "Vehicles", "PX4", "generated")

imports = [
    ("typhoon_h480_body.obj", "/Game/Vehicles/Hexacopter/Meshes"),
    ("typhoon_h480_complete.obj", "/Game/Vehicles/Hexacopter/Meshes"),
    ("rover_ackermann_base.obj", "/Game/Vehicles/Rover/Meshes"),
    ("rover_ackermann_wheel.obj", "/Game/Vehicles/Rover/Meshes"),
    ("rover_ackermann_complete.obj", "/Game/Vehicles/Rover/Meshes"),
]

tasks = []
for filename, destination in imports:
    source = os.path.join(source_root, filename)
    if not os.path.isfile(source):
        raise RuntimeError(f"generated source mesh is missing: {source}")
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", source)
    task.set_editor_property("destination_path", destination)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("replace_existing_settings", True)
    task.set_editor_property("save", True)
    tasks.append(task)

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

failed = []
for task in tasks:
    imported = list(task.get_editor_property("imported_object_paths"))
    if not imported:
        failed.append(task.get_editor_property("filename"))
        continue
    for object_path in imported:
        asset = unreal.EditorAssetLibrary.load_asset(object_path)
        if not asset or not isinstance(asset, unreal.StaticMesh):
            failed.append(object_path)
            continue
        unreal.EditorAssetLibrary.save_asset(object_path, only_if_is_dirty=False)
        log(f"imported {object_path}")

if failed:
    raise RuntimeError(f"vehicle imports failed: {failed}")

expected_assets = [
    "/Game/Vehicles/Hexacopter/Meshes/typhoon_h480_body.typhoon_h480_body",
    "/Game/Vehicles/Hexacopter/Meshes/typhoon_h480_complete.typhoon_h480_complete",
    "/Game/Vehicles/Rover/Meshes/rover_ackermann_base.rover_ackermann_base",
    "/Game/Vehicles/Rover/Meshes/rover_ackermann_wheel.rover_ackermann_wheel",
    "/Game/Vehicles/Rover/Meshes/rover_ackermann_complete.rover_ackermann_complete",
]
missing = [path for path in expected_assets if not unreal.EditorAssetLibrary.does_asset_exist(path)]
if missing:
    raise RuntimeError(f"expected imported assets are missing: {missing}")

log(f"PASS assets={len(expected_assets)}")
