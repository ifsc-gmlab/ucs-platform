#!/usr/bin/env python3
"""DAE (COLLADA) -> OBJ converter for UE5 propeller meshes"""
import sys, os, xml.etree.ElementTree as ET

NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}

def convert_dae_to_obj(dae_path, obj_path):
    tree = ET.parse(dae_path)
    root = tree.getroot()

    float_arrays = {}
    for fa in root.findall(".//c:float_array", NS):
        fid = fa.get("id")
        vals = list(map(float, fa.text.strip().split()))
        float_arrays[fid] = vals

    p_data = None
    inputs = []
    tri_elem = root.find(".//c:triangles", NS)
    if tri_elem is not None:
        p_elem = tri_elem.find("c:p", NS)
        if p_elem is not None:
            p_data = list(map(int, p_elem.text.strip().split()))
        for inp in tri_elem.findall("c:input", NS):
            inputs.append({"offset": int(inp.get("offset",0)), "semantic": inp.get("semantic"), "source": inp.get("source")})
    else:
        poly_elem = root.find(".//c:polylist", NS)
        if poly_elem is not None:
            p_elem = poly_elem.find("c:p", NS)
            if p_elem is not None:
                p_data = list(map(int, p_elem.text.strip().split()))
            for inp in poly_elem.findall("c:input", NS):
                inputs.append({"offset": int(inp.get("offset",0)), "semantic": inp.get("semantic"), "source": inp.get("source")})
    if p_data is None:
        return False

    stride = max(inp["offset"] for inp in inputs) + 1 if inputs else 2
    faces = []
    for i in range(0, len(p_data), stride * 3):
        face = []
        for j in range(3):
            base = i + j * stride
            face.append((p_data[base], p_data[base + 1] if stride > 1 else 0))
        faces.append(face)

    for fa_id, vals in float_arrays.items():
        if "position" in fa_id.lower() or "Position" in fa_id:
            positions = vals
        if "normal" in fa_id.lower() or "Normal" in fa_id:
            normals = vals

    with open(obj_path, "w", encoding="utf-8") as f:
        f.write("# Converted from " + os.path.basename(dae_path) + "\n")
        f.write("o Propeller\n")
        for i in range(0, len(positions), 3):
            f.write("v " + str(positions[i]) + " " + str(positions[i+1]) + " " + str(positions[i+2]) + "\n")
        for i in range(0, len(normals), 3):
            f.write("vn " + str(normals[i]) + " " + str(normals[i+1]) + " " + str(normals[i+2]) + "\n")
        for face in faces:
            f.write("f")
            for v_idx, n_idx in face:
                f.write(" " + str(v_idx + 1) + "//" + str(n_idx + 1))
            f.write("\n")
    return True

if __name__ == "__main__":
    dae_path, obj_path = sys.argv[1], sys.argv[2]
    if convert_dae_to_obj(dae_path, obj_path):
        print("OK: " + os.path.basename(dae_path) + " -> " + os.path.basename(obj_path))
    else:
        print("FAIL: " + os.path.basename(dae_path))
        sys.exit(1)
