#!/usr/bin/env python3
import json
import os
import tempfile

from semantic_explainability import (
    ExplainabilityStore,
    canonical_sha256,
    file_sha256,
    public_summary,
)


ok = True


def check(name, condition):
    global ok
    print(("PASS" if condition else "FAIL"), name)
    if not condition:
        ok = False


with tempfile.TemporaryDirectory() as temporary:
    map_path = os.path.join(temporary, "semantic_map.json")
    map_data = {
        "map_id": "ifsc_test_bridge_v1",
        "display_name": "IFSC Explainability Test Bridge",
        "frame": "PX4_LOCAL_NED",
        "ue_map": "/Game/Maps/IFSCExplainabilityTest",
        "goals": {
            "start_pad": {
                "display_label": "起点", "position_m": [0, 0],
                "altitude_m": 3,
            },
            "far_pad": {
                "display_label": "对岸", "position_m": [50, 0],
                "altitude_m": 3,
            },
        },
    }
    with open(map_path, "w", encoding="utf-8") as handle:
        json.dump(map_data, handle, ensure_ascii=False, indent=2)

    plan = [
        {
            "action": "fly_to", "goal_id": "far_pad",
            "vision": False, "fly_through": False,
        },
        {"action": "land"},
    ]
    store = ExplainabilityStore(os.path.join(temporary, "records"))
    record = store.create(
        raw_command="飞到对岸并降落",
        requested_vision=False,
        parser_name="rules",
        model=None,
        fallback_reason="未配置模型，使用确定性规则",
        plan=plan,
        map_path=map_path,
        map_data=map_data,
    )
    explanation_id = record["explanation_id"]
    check("server generated explanation id",
          explanation_id.startswith("explain_"))
    check("map hash recorded",
          record["semantic_map"]["sha256"] == file_sha256(map_path))
    check("plan hash deterministic",
          record["plan_sha256"] == canonical_sha256(plan))
    check("goal resolution contains coordinates",
          record["goal_resolution"][0]["position_m"] == [50, 0])
    check("local validator remains control authority",
          record["parser"]["contract"]["control_authority"] ==
          "local_validation_and_route_planner")

    revised_plan = [
        {
            "action": "fly_to", "goal_id": "start_pad",
            "vision": False, "fly_through": False,
        },
        {"action": "land"},
    ]
    revised = store.update_plan(
        explanation_id, revised_plan, map_data,
        reason="operator_corrected_goal")
    check("plan correction preserves previous candidate",
          revised["plan_revisions"][0]["previous_plan"] == plan)
    check("plan correction records explicit reason",
          revised["plan_revisions"][0]["reason"] ==
          "operator_corrected_goal")
    check("replacement plan becomes authoritative",
          revised["plan_sha256"] == canonical_sha256(revised_plan))
    plan = revised_plan

    review = {
        "risk": "中", "blocked": False, "block_reason": None,
        "route_distance_m": 50.0, "planned_clearance_m": 1.25,
        "warnings": ["测试记录"],
    }
    store.attach_review(explanation_id, review)
    trajectory = {
        "plan_fingerprint": json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "route_point_count": 2,
        "legs": [{
            "from": "start_pad", "to": "far_pad",
            "decision": "direct_path_clear",
            "direct_path_obstacles": [],
            "route_points_m": [[0, 0], [50, 0]],
        }],
    }
    store.attach_trajectory(explanation_id, trajectory)
    store.attach_execution(
        explanation_id, "task_0001", "queued", outcome=None)
    final = store.attach_execution(
        explanation_id, "task_0001", "done", outcome="PASS",
        mission_result={"pass": True, "final_position_error_m": 0.02})

    reloaded = store.read(explanation_id)
    summary = public_summary(reloaded)
    check("record reaches completed status", final["status"] == "completed")
    check("execution outcome closes the loop",
          reloaded["execution"]["outcome"] == "PASS")
    check("mission result is replayable",
          reloaded["execution"]["mission_result"]["pass"] is True)
    check("public summary exposes hashes and outcome",
          summary["plan_sha256"] == canonical_sha256(plan)
          and summary["outcome"] == "PASS")
    check("trajectory explains routing decision",
          reloaded["trajectory"]["legs"][0]["decision"] ==
          "direct_path_clear")

    try:
        store.read("../escape")
        traversal_rejected = False
    except ValueError:
        traversal_rejected = True
    check("record id rejects path traversal", traversal_rejected)

print("ALL_OK" if ok else "SOME_FAILED")
raise SystemExit(0 if ok else 1)
