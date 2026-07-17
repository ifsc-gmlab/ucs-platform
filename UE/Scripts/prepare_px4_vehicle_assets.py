#!/usr/bin/env python3
"""Convert pinned PX4 Gazebo vehicle meshes into UE-oriented OBJ assets.

Development-only dependencies:
    python -m pip install trimesh pycollada
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import trimesh


def load_geometry(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="scene", process=False)
    if isinstance(loaded, trimesh.Scene):
        mesh = loaded.to_geometry()
    else:
        mesh = loaded.copy()
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"unsupported geometry in {path}: {type(mesh)!r}")
    return mesh


def pose_matrix(
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
) -> np.ndarray:
    matrix = trimesh.transformations.euler_matrix(roll, pitch, yaw, axes="sxyz")
    matrix[:3, 3] = (x, y, z)
    return matrix


def scale_matrix(scale: float) -> np.ndarray:
    matrix = np.eye(4)
    matrix[0, 0] = scale
    matrix[1, 1] = scale
    matrix[2, 2] = scale
    return matrix


def gazebo_meters_to_ue_centimeters(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    # Gazebo is X-forward/Y-left/Z-up in metres. UE is X-forward/Y-right/Z-up in cm.
    transform = np.diag([100.0, -100.0, 100.0, 1.0])
    mesh.apply_transform(transform)
    # Reflection changes winding; invert it so imported normals still face outward.
    mesh.invert()
    return mesh


def transformed(mesh: trimesh.Trimesh, matrix: np.ndarray) -> trimesh.Trimesh:
    result = mesh.copy()
    result.apply_transform(matrix)
    return result


def export_obj(mesh: trimesh.Trimesh, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path, file_type="obj", include_color=False)
    extents = ", ".join(f"{value:.2f}" for value in mesh.extents)
    print(f"[vehicle-assets] {path.name}: extents_cm=({extents}) faces={len(mesh.faces)}")


def build_typhoon(source_root: Path, output_root: Path) -> None:
    model_root = source_root / "typhoon_h480" / "meshes"
    body_raw = load_geometry(model_root / "main_body_remeshed_v3.stl")
    cw_raw = load_geometry(model_root / "prop_cw_assembly_remeshed_v3.stl")
    ccw_raw = load_geometry(model_root / "prop_ccw_assembly_remeshed_v3.stl")

    stl_mm_to_m = scale_matrix(0.001)
    body = transformed(body_raw, pose_matrix(yaw=math.pi) @ stl_mm_to_m)

    # Link and visual poses are copied from the pinned Typhoon H480 SDF. The
    # source prop assemblies contain one complete arm/prop at CAD coordinates.
    rotor_instances = [
        (ccw_raw, (0.211396, 0.119762, 0.082219, 0.0), (-0.211396, -0.119762, -0.082219)),
        (ccw_raw, (-0.209396, 0.122762, 0.082219, 2.09439510239), (-0.211396, -0.119762, -0.082219)),
        (cw_raw, (-0.00187896, 0.242705, 0.0822169, 0.0), (0.00187896, -0.242705, -0.0822169)),
        (cw_raw, (0.211396, -0.119762, 0.082219, -2.09439510239), (0.00187896, -0.242705, -0.0822169)),
        (ccw_raw, (-0.00187896, -0.242705, 0.0822169, -2.09439510239), (-0.211396, -0.119762, -0.082219)),
        (cw_raw, (-0.209396, -0.122762, 0.082219, 2.09439510239), (0.00187896, -0.242705, -0.0822169)),
    ]

    parts = [body]
    for mesh, link, visual in rotor_instances:
        link_matrix = pose_matrix(link[0], link[1], link[2], yaw=link[3])
        visual_matrix = pose_matrix(visual[0], visual[1], visual[2])
        parts.append(transformed(mesh, link_matrix @ visual_matrix @ stl_mm_to_m))

    body_ue = gazebo_meters_to_ue_centimeters(body.copy())
    complete_ue = gazebo_meters_to_ue_centimeters(trimesh.util.concatenate(parts))
    export_obj(body_ue, output_root / "typhoon_h480_body.obj")
    export_obj(complete_ue, output_root / "typhoon_h480_complete.obj")


def build_rover(source_root: Path, output_root: Path) -> None:
    model_root = source_root / "rover_ackermann" / "meshes"
    base = load_geometry(model_root / "rover_ackermann_base.dae")
    wheel = load_geometry(model_root / "rover_ackermann_wheel.dae")

    # The pinned Gazebo Ackermann model uses two rear wheels and one steerable
    # front wheel. Keep the components separate for later steering animation.
    wheel_poses = [
        pose_matrix(-0.25, 0.30, 0.0, roll=-1.5707),
        pose_matrix(-0.25, -0.30, 0.0, roll=-1.5707),
        pose_matrix(0.25, 0.0, 0.0, roll=-1.5707),
    ]
    complete_parts = [base.copy()]
    complete_parts.extend(transformed(wheel, pose) for pose in wheel_poses)

    base_ue = gazebo_meters_to_ue_centimeters(base.copy())
    wheel_ue = gazebo_meters_to_ue_centimeters(wheel.copy())
    complete_ue = gazebo_meters_to_ue_centimeters(trimesh.util.concatenate(complete_parts))
    export_obj(base_ue, output_root / "rover_ackermann_base.obj")
    export_obj(wheel_ue, output_root / "rover_ackermann_wheel.obj")
    export_obj(complete_ue, output_root / "rover_ackermann_complete.obj")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=project_root / "SourceArt" / "Vehicles" / "PX4",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=project_root / "SourceArt" / "Vehicles" / "PX4" / "generated",
    )
    args = parser.parse_args()

    build_typhoon(args.source_root, args.output_root)
    build_rover(args.source_root, args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
