#!/usr/bin/env python3
"""给没有 UV 的 OBJ 文件添加简单的平面 UV 坐标"""
import sys

def add_uvs_to_obj(input_path, output_path):
    with open(input_path, 'r') as f:
        lines = f.readlines()

    vertices = []
    faces = []
    other = []

    for line in lines:
        if line.startswith('v ') and not line.startswith('vt') and not line.startswith('vn'):
            parts = line.strip().split()
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif line.startswith('f '):
            faces.append(line)
        else:
            other.append(line)

    # 生成简单的平面 UV（从 XYZ 映射到 UV）
    # 找到 X 和 Y 的范围
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    range_x = max_x - min_x if max_x != min_x else 1.0
    range_y = max_y - min_y if max_y != min_y else 1.0

    # 生成 UV，每个顶点一个 UV
    uvs = []
    for v in vertices:
        u = (v[0] - min_x) / range_x
        v_uv = (v[1] - min_y) / range_y
        uvs.append((u, v_uv))

    # 写入新文件，f 格式改为 f v/vt/vn
    with open(output_path, 'w') as f:
        for line in other:
            f.write(line)
        # 写 UV
        for u, v_uv in uvs:
            f.write(f"vt {u:.6f} {v_uv:.6f}\n")
        # 写面（添加 UV 索引 = 顶点索引）
        for line in faces:
            parts = line.strip().split()
            out = "f"
            for p in parts[1:]:
                if '//' in p:
                    # f v//vn 格式
                    v_idx = p.split('//')[0]
                    out += f" {v_idx}/{v_idx}/"
                elif '/' in p:
                    # f v/vt/vn 格式
                    sub = p.split('/')
                    out += f" {sub[0]}/{sub[0]}/{sub[2]}"
                else:
                    # f v 格式（无 UV 无 N）
                    out += f" {p}/{p}"
            f.write(out + "\n")

    print(f"Added {len(uvs)} UVs to {len(faces)} faces")

if __name__ == "__main__":
    add_uvs_to_obj(sys.argv[1], sys.argv[2])
