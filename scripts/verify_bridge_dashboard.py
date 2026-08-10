import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(ROOT, "drone_dashboard.py")
MAP_PATH = os.path.join(ROOT, "bridge_semantic_map.json")
LARGE_MAP_PATH = os.path.join(ROOT, "large_bridge_semantic_map.json")

spec = importlib.util.spec_from_file_location("bridge_dashboard", MODULE_PATH)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

with open(MAP_PATH, "r", encoding="utf-8") as handle:
    bridge_map = json.load(handle)
with open(LARGE_MAP_PATH, "r", encoding="utf-8") as handle:
    large_bridge_map = json.load(handle)


def has_mixed_overlap(map_data):
    zones = map_data["inspection_zones"]
    for index, left in enumerate(zones):
        for right in zones[index + 1:]:
            if left["class"] == right["class"]:
                continue
            dx = left["position_m"][0] - right["position_m"][0]
            dy = left["position_m"][1] - right["position_m"][1]
            if dx * dx + dy * dy < (left["radius_m"] + right["radius_m"]) ** 2:
                return True
    return False

dashboard._refresh_map_runtime_config(bridge_map)
goals, ue_map = dashboard.load_map_dispatch_config(MAP_PATH)

checks = {
    "arch bridge map id": bridge_map["map_id"] == "sztu_arch_bridge_semantic_v2",
    "all seven goals enabled": len(goals) == 7,
    "correct UE map": ue_map == "/Game/Maps/SZTUBridgeSemanticScene",
    "complete crossing plan": len(bridge_map["default_plan"]) == 7,
    "frame archive enabled": dashboard.DASHBOARD_CONFIG["archive_frames"] is True,
    "highest point parser": dashboard.parse_command("飞到拱桥最高点")[1] == "bridge_apex",
    "far bank parser": dashboard.parse_command("飞到对岸降落点")[1] == "far_pad",
    "dynamic quick goals": 'id="quick-goals"' in dashboard.DASHBOARD_HTML,
    "default plan button": "loadDefaultPlan()" in dashboard.DASHBOARD_HTML,
    "inspection overlays": "inspection_zones" in dashboard.DASHBOARD_HTML,
    "bridge boundary renderer": "桥面边界" in dashboard.DASHBOARD_HTML,
    "small bridge boundary metadata": bridge_map["bridge"]["deck_length_m"] == 30.0,
    "small bridge mixed observation zones": has_mixed_overlap(bridge_map),
    "large bridge map id": large_bridge_map["map_id"] == "sztu_large_suspension_bridge_semantic_v2",
    "large bridge length": large_bridge_map["bridge"]["deck_length_m"] == 50.0,
    "large bridge suspension type": large_bridge_map["bridge"]["type"] == "suspension",
    "large bridge UE map": large_bridge_map["ue_map"] == "/Game/Maps/SZTULargeBridgeSemanticScene",
    "large bridge mixed observation zones": has_mixed_overlap(large_bridge_map),
    "semantic mission profiles": len(large_bridge_map.get("mission_profiles", [])) >= 3,
    "semantic command selector": 'id="semantic-command-select"' in dashboard.DASHBOARD_HTML,
    "UE trajectory generation control": "generateTrajectory()" in dashboard.DASHBOARD_HTML,
    "trajectory-before-flight guard": "requires_trajectory_preview" in open(MODULE_PATH, encoding="utf-8").read(),
    "explainability card": 'id="explainability-card"' in dashboard.DASHBOARD_HTML,
    "explainability record endpoint": "/api/explainability/" in open(MODULE_PATH, encoding="utf-8").read(),
    "explanation id follows task": "explanation_id" in open(MODULE_PATH, encoding="utf-8").read(),
    "north-up NED labels": "北（X+）↑" in dashboard.DASHBOARD_HTML and "东（Y+）→" in dashboard.DASHBOARD_HTML,
    "dynamic title": "__DASHBOARD_TITLE__" in dashboard.DASHBOARD_HTML,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("ALL_OK")
