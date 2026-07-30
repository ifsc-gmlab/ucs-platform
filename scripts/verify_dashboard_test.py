import copy, importlib.util, os, tempfile, time, sys
from semantic_mission_verdict import evaluate_mission_result
from semantic_route_planner import (
    DEFAULT_CLEARANCE_M, plan_safe_route, segment_is_clear)

SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drone_dashboard.py")
spec = importlib.util.spec_from_file_location("dd", SPEC)
dd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dd)

ok = True
def check(name, cond):
    global ok
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        ok = False

# 1) _decode_bytes: PowerShell 5.1 UTF-16LE pipe vs UTF-8 vs GBK
utf16 = "启动 UE 中文测试".encode("utf-16-le")
check("_decode_bytes utf-16le", dd._decode_bytes(utf16) == "启动 UE 中文测试")
utf8 = "启动 UE 中文测试".encode("utf-8")
check("_decode_bytes utf-8", dd._decode_bytes(utf8) == "启动 UE 中文测试")
check("_decode_bytes empty", dd._decode_bytes(b"") == "")

# 2) _stage_for_line
check("stage startup", dd._stage_for_line("[semantic-city] run dir: x") == 0)
check("stage flight", dd._stage_for_line("takeoff ARM OFFBOARD nav 12.3") == 1)
check("stage land", dd._stage_for_line("DISARM landed") == 2)
check("stage cleanup", dd._stage_for_line("verdict=PASS summary.md") == 3)
check("plan JSON is not landing stage",
      dd._stage_for_line('[sequence] accepted [{"action":"land"}]') != 2)
check("startup summary is not cleanup",
      dd._stage_for_line("summary written: verification-runs/x/summary.md") == -1)
check("KeepRunning is not cleanup",
      dd._stage_for_line("KeepRunning set; UE/PX4 were left running") != 3)
check("stage none", dd._stage_for_line("random noise line") == -1)

# 3) _render_report_html
task = {"task_id": "task_0001", "goal_id": "delivery_zone", "vision": True, "outcome": "PASS"}
html = dd.DashboardHandler._render_report_html(None, task, "# PASS\nsemantic <b>goal</b> reached")
check("report has outcome", "PASS" in html)
check("report escapes html", "<b>" not in html)  # escaped, so literal <b> must not appear raw
check("report title", "task_0001" in html)

# 4) history + stop via fake worker (no real UE run)
tmp = tempfile.mkdtemp()
dd.HISTORY_PATH = os.path.join(tmp, "task_history.json")
dd.CURRENT_TASK_PATH = os.path.join(tmp, "current_task.json")

def make_fake(stop_mode):
    def fake_run(tid, goal, vision, plan=None):
        task = dd.tasks.get(tid)
        if task is None:
            return
        dd.mission_lock.acquire(blocking=False)
        try:
            task["status"] = "running"
            task["started_wall"] = time.time()
            dd._write_current(tid)
            for ln in ["[semantic-city] run dir: X", "takeoff ARM OFFBOARD nav", "DISARM landed", "verdict=PASS"]:
                dd._append_task_log(task, ln)
            if stop_mode:
                while not task.get("stop_requested"):
                    time.sleep(0.05)
                task["status"] = "stopped"
                task["outcome"] = "STOPPED"
                task["error"] = "用户已停止任务"
            else:
                d = tempfile.mkdtemp()
                p = os.path.join(d, "summary.md")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("# PASS\nsemantic goal reached")
                task["report_path"] = p
                task["result_json"] = {"pass": True}
                task["outcome"] = "PASS"
                task["status"] = "done"
        finally:
            dd._record_task_history(task)
            dd._clear_current()
            try:
                dd.mission_lock.release()
            except Exception:
                pass
    return fake_run

# Scenario A: PASS (no stop)
dd.run_controller_task = make_fake(stop_mode=False)
res = dd.execute_command("fly_to", "delivery_zone", False)
check("execute returns task_id", "task_id" in res and res.get("task_id"))
tid_a = res["task_id"]
time.sleep(0.4)
ta = dd.get_task_status(tid_a)
check("PASS status", ta and ta["status"] == "done")
check("PASS outcome", ta and ta["outcome"] == "PASS")
check("PASS stage_index reached cleanup", ta and ta.get("stage_index") == 3)
check("PASS report_path set", ta and ta.get("report_path") and os.path.isfile(ta["report_path"]))
check("PASS history written", len(dd._load_history()) == 1)
check("PASS current cleared", dd._read_current() is None)

# Scenario B: STOPPED
dd.run_controller_task = make_fake(stop_mode=True)
res2 = dd.execute_command("fly_to", "medical_station", True)
tid_b = res2["task_id"]
time.sleep(0.2)
ok_stop, msg = dd.stop_task(tid_b)
check("stop returns ok", ok_stop is True)
time.sleep(0.6)
tb = dd.get_task_status(tid_b)
check("STOPPED status", tb and tb["status"] == "stopped")
check("STOPPED outcome", tb and tb["outcome"] == "STOPPED")
hist = dd._load_history()
check("history has 2 records", len(hist) == 2)
check("history STOPPED recorded", any(h.get("outcome") == "STOPPED" for h in hist))

# 5) duplicate execution guard
dd.run_controller_task = make_fake(stop_mode=False)
r3 = dd.execute_command("fly_to", "inspection_gate", False)
tid_c = r3["task_id"]
# mission_lock now held by running worker; a second fly_to must be refused
r4 = dd.execute_command("fly_to", "start_pad", False)
check("duplicate refused", "error" in r4)
time.sleep(0.4)
dd.stop_task(tid_c)

# 6) return-home in the cold-session architecture is an immediate no-op PASS
home = dd.execute_command("fly_to", "start_pad", True)
check("home returns task_id", bool(home.get("task_id")))
check("home immediate done", home.get("status") == "done")
check("home response message", "已在 A 起点" in home.get("message", ""))
home_task = dd.get_task_status(home["task_id"])
check("home task PASS", home_task and home_task.get("outcome") == "PASS")
check("home no flight", home_task and home_task.get("result_json", {}).get("flight_started") is False)
check("home log explains no-op", home_task and any("无需返航" in line for line in home_task.get("log", [])))
check("home stage completed", home_task and home_task.get("stage_index") == 3)
check("home history recorded", any(
    h.get("task_id") == home["task_id"] and h.get("outcome") == "PASS"
    for h in dd._load_history()))

# 7) multi-step plan parsing, validation and dispatch
parsed_plan = dd.parse_plan_local("视觉确认飞到巡检门，完成后返回起点并降落")
check("composite parser step count", parsed_plan and len(parsed_plan) == 3)
check("composite parser outbound vision", parsed_plan and parsed_plan[0] == {
    "action": "fly_to", "goal_id": "inspection_gate", "vision": True})
check("composite parser return", parsed_plan and parsed_plan[1].get("goal_id") == "start_pad")
check("composite parser land", parsed_plan and parsed_plan[-1].get("action") == "land")
normalized = dd.validate_plan([
    {"action": "fly_to", "goal_id": "inspection_gate", "vision": True},
    {"action": "hover", "seconds": 2},
    {"action": "fly_to", "goal_id": "start_pad", "vision": False},
])
check("plan auto appends land", normalized[-1].get("action") == "land")
try:
    dd.validate_plan([
        {"action": "fly_to", "goal_id": "inspection_gate"},
        {"action": "land"},
        {"action": "hover", "seconds": 2},
    ])
    invalid_after_land = False
except ValueError:
    invalid_after_land = True
check("plan rejects step after land", invalid_after_land)
dd.run_controller_task = make_fake(stop_mode=False)
plan_res = dd.execute_plan(normalized)
check("plan execute returns task", bool(plan_res.get("task_id")))
time.sleep(0.4)
plan_task = dd.get_task_status(plan_res["task_id"])
check("plan task PASS", plan_task and plan_task.get("outcome") == "PASS")
check("plan preserved", plan_task and plan_task.get("plan") == normalized)

# 8) a complete natural-language plan replaces stale UI steps instead of
# being appended after an old landing step.
check("AI plan replaces existing queue",
      "planSteps=d.plan.map(step=>({...step}));" in dd.DASHBOARD_HTML)
check("AI replacement requires reconfirmation",
      "invalidatePlan();renderPlan();" in dd.DASHBOARD_HTML)
check("task log has expand control", "openLogModal()" in dd.DASHBOARD_HTML)
check("task log modal exists", 'id="log-modal-content"' in dd.DASHBOARD_HTML)
check("task log keeps full history",
      "latestTaskLog=(d.log||[]).join" in dd.DASHBOARD_HTML)
check("map explicitly labels positive Y upward",
      "Y+ 向上" in dd.DASHBOARD_HTML and "Y+ ↑" in dd.DASHBOARD_HTML)
check("map uses conventional positive-Y-up transform",
      "function w2s(x,y){return [ox+x*scale,oy+y*scale];}" in dd.DASHBOARD_HTML and
      "function d2s(x,y){return [ox+x*scale,oy-y*scale];}" in dd.DASHBOARD_HTML and
      "return [x||0,-(y||0)];" in dd.DASHBOARD_HTML and
      "return mapLocalToDisplayXY(localX,localY);" in dd.DASHBOARD_HTML)
check("display transforms are centralized and display-only",
      "function mapLocalToDisplayXY(x,y)" in dd.DASHBOARD_HTML and
      "function mapDisplayVectorXY(x,y)" in dd.DASHBOARD_HTML and
      "function mapDisplayYawDeg(yaw)" in dd.DASHBOARD_HTML and
      "Never feed these values back to UE, PX4" in dd.DASHBOARD_HTML and
      "mapDisplayVectorXY(t.velocity_x_mps,t.velocity_y_mps)" in dd.DASHBOARD_HTML and
      "mapDisplayYawDeg(t.yaw_deg)" in dd.DASHBOARD_HTML)
check("map preserves UE heading after display-coordinate conversion",
      "ctx.rotate(yaw)" in dd.DASHBOARD_HTML)

# A clear direct leg must not be failed merely because no artificial avoidance
# maneuver occurred. Obstacle-crossing legs still require evidence.
verdict_base = {
    "reached": True,
    "collision_count": 0,
    "sensor_samples": 100,
    "landed": True,
    "disarmed": True,
    "final_position_error_m": 0.03,
    "vision_confirmed": False,
}
clear_verdict = copy.deepcopy(verdict_base)
clear_verdict["legs"] = [{
    "avoidance_required": False,
    "avoidance_demonstrated": True,
}]
check("clear direct leg does not require artificial avoidance",
      evaluate_mission_result(clear_verdict, 0.5, False) is True)
blocked_verdict = copy.deepcopy(verdict_base)
blocked_verdict["legs"] = [{
    "avoidance_required": True,
    "avoidance_demonstrated": False,
}]
check("obstacle-crossing leg still requires avoidance evidence",
      evaluate_mission_result(blocked_verdict, 0.5, False) is False and
      blocked_verdict["failure"] == "required obstacle avoidance not demonstrated")
detour_verdict = copy.deepcopy(verdict_base)
detour_verdict["legs"] = [{
    "avoidance_required": True,
    "avoidance_demonstrated": True,
}]
check("planned detour satisfies required avoidance evidence",
      evaluate_mission_result(detour_verdict, 0.5, False) is True)

# 9) deterministic pre-flight facts remain authoritative even when AI is down.
review = dd.build_plan_review([
    {"action": "fly_to", "goal_id": "delivery_zone", "vision": True},
    {"action": "hover", "seconds": 3},
    {"action": "fly_to", "goal_id": "medical_station", "vision": False},
    {"action": "fly_to", "goal_id": "start_pad", "vision": False},
    {"action": "land"},
])
check("review route distance includes detours", review["route_distance_m"] > 40.4)
check("review counts legs", review["fly_leg_count"] == 3)
check("review counts vision", review["vision_leg_count"] == 1)
check("review detects return", review["returns_home"] is True)
check("review detects landing", review["lands_last"] is True)
check("review flags long route",
      any("总航程较长" in warning for warning in review["warnings"]))
check("review plans obstacle detours", review["blocked"] is False)
check("review exposes rerouted legs", review["rerouted_leg_count"] >= 1)
check("review API is wired", "/api/review-plan" in dd.DASHBOARD_HTML)
check("review blocks execution until complete",
      "正在进行飞前安全审查" in dd.DASHBOARD_HTML)
check("blocked review keeps execute disabled",
      "飞前审查未通过，请调整任务队列" in dd.DASHBOARD_HTML)

with open(dd.DEFAULT_MAP, "r", encoding="utf-8") as map_handle:
    map_data = __import__("json").load(map_handle)
safe_route = plan_safe_route(
    (6.0, -4.0), (18.0, 4.0), map_data["obstacles"])
check("planner finds inspection to medical route", safe_route is not None)
check("planner adds intermediate waypoints",
      safe_route and safe_route["detour_waypoints"] >= 1)
check("every planned segment clears expanded obstacle",
      safe_route and all(
          segment_is_clear(a, b, map_data["obstacles"], DEFAULT_CLEARANCE_M)
          for a, b in zip(safe_route["points"], safe_route["points"][1:])))
hard_route_review = dd.build_plan_review([
    {"action": "fly_to", "goal_id": "inspection_gate"},
    {"action": "fly_to", "goal_id": "medical_station"},
    {"action": "fly_to", "goal_id": "start_pad"},
    {"action": "land"},
])
check("formerly blocked route is now executable",
      hard_route_review["blocked"] is False)
check("formerly blocked route reroutes both crossing legs",
      hard_route_review["rerouted_leg_count"] == 2)

print("ALL_OK" if ok else "SOME_FAILED")
sys.exit(0 if ok else 1)
