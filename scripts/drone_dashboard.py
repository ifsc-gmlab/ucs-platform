#!/usr/bin/env python3
"""
IFSC Bridge Inspection Platform — Three-Window Web Dashboard
=============================================================
Panels:
  1. Drone Status  — real-time telemetry (position, velocity, attitude, sensors, wind)
  2. Semantic Map  — 2D top-down: drone icon + landmarks + obstacles + trail + radar arcs
  3. Command Input — rule-based command parser + background task execution

Data source: UDP 14660 telemetry stream from UE5 (JSON datagrams).
Map source: urban_semantic_map.json

Execution architecture (rewritten 2026-07-23):
  - ThreadingHTTPServer: the HTTP handler never blocks on a long controller run.
  - /api/execute spawns a BACKGROUND worker thread and returns a task_id at once.
  - /api/task/<id> reports live status (queued/running/done/error) + captured output.
  - Goal names are a STRICT whitelist; the controller is launched with an argument
    ARRAY and shell=False (no command-string injection).
  - Default mode is the verified NON-vision path; vision is enabled only when the
    user explicitly requests it (checkbox or "vision/视觉/识别" keyword).
  - A pre-flight readiness check refuses to launch if live telemetry is stale.
  - Server binds 127.0.0.1 only (no LAN exposure).

Usage:
  python drone_dashboard.py [--port 8080] [--telemetry-port 14660] [--map urban_semantic_map.json]

Open http://localhost:8080 in a browser.
"""

import json
import socket
import math
import os
import re
import socket
import sys
import threading
import time
import subprocess
import urllib.error
import urllib.request
import winreg
from semantic_route_planner import (
    DEFAULT_CLEARANCE_M,
    intersected_obstacle_ids,
    plan_safe_route,
)
from semantic_runtime_config import load_runtime_config
from semantic_explainability import (
    ExplainabilityStore, canonical_sha256, file_sha256, public_summary)
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs


def _decode_bytes(raw):
    """Robustly decode subprocess stdout bytes.

    PowerShell 5.1 emits UTF-16LE when stdout is a pipe, so a naive utf-8
    decode produces mojibake. Heuristic: if the first bytes contain NULs
    (ASCII text in UTF-16LE has a NUL every other byte), treat as UTF-16LE;
    otherwise try utf-8, then gbk, then latin-1.
    """
    if not raw:
        return ""
    if b"\x00" in raw[:60]:
        try:
            return raw.decode("utf-16-le", "replace")
        except Exception:
            pass
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


# ─── Configuration ──────────────────────────────────────────────────────────
DEFAULT_PORT = 8080
SERVER_PORT = DEFAULT_PORT
DEFAULT_TELEMETRY_PORT = 14660
DEFAULT_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "urban_semantic_map.json")
DEFAULT_UE_MAP = ""
MAP_DATA = {}
DASHBOARD_CONFIG = {}
RUNTIME_CONFIG = load_runtime_config()
CAMERA_PREVIEW_POLL_MS = int(
    RUNTIME_CONFIG["camera"]["preview_poll_ms"])
CAMERA_STALE_AFTER_SEC = float(
    RUNTIME_CONFIG["camera"]["stale_after_s"])

def load_map_dispatch_config(path):
    """Load a fail-closed goal whitelist and matching UE map from map data."""
    with open(path, "r", encoding="utf-8-sig") as map_file:
        data = json.load(map_file)
    goals = data.get("goals")
    ue_map = str(data.get("ue_map", "")).strip()
    if not isinstance(goals, dict) or not goals:
        raise ValueError("semantic map has no dispatchable goals")
    if not re.fullmatch(r"/Game/[A-Za-z0-9_./-]+", ue_map) or ".." in ue_map:
        raise ValueError("semantic map has an invalid ue_map asset path")
    return set(str(goal_id) for goal_id in goals), ue_map


# Strict whitelist of goal ids the dashboard is allowed to dispatch.
GOAL_WHITELIST, DEFAULT_UE_MAP = load_map_dispatch_config(DEFAULT_MAP)
with open(DEFAULT_MAP, "r", encoding="utf-8-sig") as _default_map_file:
    MAP_DATA = json.load(_default_map_file)
DASHBOARD_CONFIG = MAP_DATA.get("dashboard", {})
# Runtime locations are configurable for server migration.  The legacy project
# folder name remains the default because UE asset/module identifiers are still
# compatibility-critical; the public product name is IFSC.
WSL_DISTRO = "Ubuntu"
WSL_CONTROLLER_DIR = "~/szefitl"
UE_PROJECT_ROOT = os.environ.get(
    "IFSC_UE_PROJECT_ROOT", r"D:\SZTUWindFieldPX4Demo\Unreal")
CAMERA_FRAMES_WINDOWS_DIR = os.path.join(
    UE_PROJECT_ROOT, "Saved", "CameraFrames")
_ue_drive, _ue_tail = os.path.splitdrive(UE_PROJECT_ROOT)
WSL_CAMERA_DIR = "/mnt/%s/%s/Saved/CameraFrames" % (
    _ue_drive[:1].lower(), _ue_tail.lstrip("\\/").replace("\\", "/"))
# Windows-side source files that we mirror into WSL so the run command stays
# pure-ASCII (avoids GBK/UTF-8 path-encoding issues on the Windows side).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_THIS_DIR)
CONTROLLER_WINDOWS_PATH = os.path.join(_THIS_DIR, "semantic_obstacle_mission.py")
VISION_WINDOWS_PATH = os.path.join(_THIS_DIR, "vision_detector.py")
TAKEOFF_WINDOWS_PATH = os.path.join(_THIS_DIR, "takeoff_offboard.py")
SEMANTIC_WRAPPER_PATH = os.path.join(_THIS_DIR, "run_semantic_city.ps1")
ENV_CHECK_PATH = os.path.join(_THIS_DIR, "check_px4_wsl_env.ps1")
SEMANTIC_RUNS_DIR = os.path.join(_THIS_DIR, "semantic-runs")
POWERSHELL_EXE = os.path.join(
    os.environ.get("WINDIR", r"C:\Windows"),
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
)

# DeepSeek is used only to translate natural language into the small, validated
# command schema below. It never receives direct access to PX4 or actuator APIs.
def read_user_environment(name, default=""):
    process_value = os.environ.get(name)
    if process_value:
        return process_value
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env_key:
            return winreg.QueryValueEx(env_key, name)[0]
    except OSError:
        return default


DEEPSEEK_API_KEY = read_user_environment("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = read_user_environment(
    "DEEPSEEK_MODEL", "deepseek-v4-flash"
).strip()
DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"
).strip()
DEEPSEEK_TIMEOUT_SEC = 30


def persist_deepseek_key(api_key):
    """Persist the key in HKCU\\Environment without writing it to project files."""
    global DEEPSEEK_API_KEY
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as env_key:
        winreg.SetValueEx(env_key, "DEEPSEEK_API_KEY", 0, winreg.REG_SZ, api_key)
        winreg.SetValueEx(env_key, "DEEPSEEK_MODEL", 0, winreg.REG_SZ, DEEPSEEK_MODEL)
    os.environ["DEEPSEEK_API_KEY"] = api_key
    os.environ["DEEPSEEK_MODEL"] = DEEPSEEK_MODEL
    DEEPSEEK_API_KEY = api_key


def validate_and_persist_deepseek_key(api_key):
    """Validate against the API before replacing the active/persisted key."""
    global DEEPSEEK_API_KEY
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,}", api_key or ""):
        return False, "密钥格式不正确"
    previous_key = DEEPSEEK_API_KEY
    DEEPSEEK_API_KEY = api_key
    parsed, error = parse_command_with_deepseek("查看状态")
    if parsed is None:
        DEEPSEEK_API_KEY = previous_key
        return False, error or "DeepSeek 密钥验证失败"
    persist_deepseek_key(api_key)
    return True, None


def remove_deepseek_key():
    """Remove the persisted DeepSeek key (registry + in-memory)."""
    global DEEPSEEK_API_KEY
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as env_key:
            try:
                winreg.DeleteValue(env_key, "DEEPSEEK_API_KEY")
            except OSError:
                pass
    except OSError:
        pass
    os.environ.pop("DEEPSEEK_API_KEY", None)
    DEEPSEEK_API_KEY = ""
    return True


def windows_to_wsl(windows_path):
    """Convert a Windows path (drive letter, backslashes) to a /mnt/c/... WSL path."""
    full = os.path.abspath(windows_path)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", full)
    if not m:
        return full
    drive = m.group(1).lower()
    tail = m.group(2).replace("\\", "/")
    return "/mnt/%s/%s" % (drive, tail)


# ─── Mission stages (for UI stepper) ────────────────────────────────────────
STAGE_NAMES = ["启动", "飞行", "降落", "清理"]
_STAGE_PATTERNS = [
    (0, re.compile(r"检查|环境|restarting WSL|run dir|copied|starting semantic|UE |PX4|TCP 4560|connected|ready|启动|开始|wsl", re.I)),
    (1, re.compile(r"takeoff|ARM|OFFBOARD|nav |飞行|起飞|上升|approach|接近|standoff|vision|climb|爬升|hold|cruise", re.I)),
    (2, re.compile(
        r"^\[sequence\] step \d+/\d+ action=land|^\[land-capture|"
        r"confirming landed|landed stable|DISARM|touchdown|RTL|降落", re.I)),
    (3, re.compile(r"verdict|清理|cleanup|Kill|Stop-Process|teardown|退出", re.I)),
]


def _stage_for_line(line):
    best = -1
    for idx, pat in _STAGE_PATTERNS:
        if pat.search(line):
            best = max(best, idx)
    return best


# ─── Global state (shared between UDP listener + HTTP handler + task threads) ─
latest_telemetry = {}
telemetry_lock = threading.Lock()
flight_trail = []  # list of (x, y) tuples, max 200 points
TRAIL_MAX = 200
last_telemetry_wall = 0.0  # time.monotonic() of most recent UDP packet

# Background task registry: task_id -> dict
tasks = {}
tasks_lock = threading.Lock()
mission_lock = threading.Lock()
_task_counter = 0

# ─── UDP Telemetry Listener ─────────────────────────────────────────────────
def telemetry_listener(port):
    """Listen for UDP telemetry datagrams and update global state."""
    global latest_telemetry, flight_trail, last_telemetry_wall
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            telem = json.loads(data.decode("utf-8"))
            with telemetry_lock:
                latest_telemetry = telem
                last_telemetry_wall = time.monotonic()
                x = telem.get("position_x_m", 0)
                y = telem.get("position_y_m", 0)
                flight_trail.append((x, y))
                if len(flight_trail) > TRAIL_MAX:
                    flight_trail.pop(0)
        except socket.timeout:
            continue
        except Exception:
            continue


def telemetry_ready(max_stale_sec=3.0):
    """True if we have recent, non-empty telemetry (UE + proximity running)."""
    with telemetry_lock:
        if not latest_telemetry:
            return False, "尚未收到实时遥测，请先启动 UE/PX4"
        if (time.monotonic() - last_telemetry_wall) > max_stale_sec:
            return False, "遥测已过期（超过 %.0f 秒未更新）" % max_stale_sec
        if not latest_telemetry.get("proximity_enabled"):
            return False, "测距传感器未启用（UE 城市场景尚未就绪）"
    return True, "已就绪"


# ─── Command Parser (Rule-Based) ────────────────────────────────────────────
COMMAND_RULES = [
    # (regex_pattern, action, goal_id)
    (r"(?:飞到|去|前往|到|fly.*to|go.*to|navigate.*to).*?(投递|delivery|dropoff|b)", "fly_to", "delivery_zone"),
    (r"(?:飞到|去|前往|到|fly.*to|go.*to).*?(检查门|检查|巡检|inspection|gate|checkpoint)", "fly_to", "inspection_gate"),
    (r"(?:飞到|去|前往|到|fly.*to|go.*to).*?(医疗站|医疗|medical|hospital|aid)", "fly_to", "medical_station"),
    (r"(?:飞到|去|前往|到|fly.*to|go.*to).*?(起点|起飞点|start|home|a)", "fly_to", "start_pad"),
    # Bare target names (no verb). Deliberately EXCLUDE single-letter 'a'/'b'
    # shorthands so English words like "camera" don't false-trigger a goal.
    (r"(?:投递区|delivery[ _]?zone|dropoff)", "fly_to", "delivery_zone"),
    (r"(?:巡检门|检查门|巡检|inspection[ _]?gate|checkpoint)", "fly_to", "inspection_gate"),
    (r"(?:医疗站|medical[ _]?station)", "fly_to", "medical_station"),
    (r"(?:起点|起飞点|start[ _]?point)", "fly_to", "start_pad"),
    (r"(?:返航|回家|返回|return|rtl|go.*home)", "fly_to", "start_pad"),
    (r"(?:悬停|hold|hover|停留)", "hover", None),
    (r"(?:起飞|takeoff|升空)", "takeoff", None),
    (r"(?:降落|land|下降|descend)", "land", None),
    (r"(?:状态|报告|status|report|怎么样)", "status", None),
]

VISION_KEYWORDS = [r"vision", r"视觉", r"识别", r"看", r"see", r"camera", r"摄像头"]

GOAL_LABELS = {
    "start_pad": "A 起点",
    "inspection_gate": "巡检门",
    "delivery_zone": "B 投递区",
    "medical_station": "医疗站",
}

BRIDGE_GOAL_KEYWORDS = {
    "start_pad": ["起点", "起飞点", "近岸", "近端", "返航", "回家"],
    "bridge_entry": ["桥入口", "入口", "进桥"],
    "bridge_ascent": ["上升点", "上坡", "拱桥上升"],
    "bridge_apex": ["最高点", "拱顶", "桥顶"],
    "bridge_descent": ["下降点", "下坡", "拱桥下降"],
    "bridge_exit": ["桥出口", "出口", "桥尾"],
    "far_pad": ["对岸", "远岸", "终点", "远端", "降落点"],
}


def _refresh_map_runtime_config(map_data):
    """Refresh labels and UI settings after --map selects a new mission map."""
    global MAP_DATA, DASHBOARD_CONFIG, GOAL_LABELS
    MAP_DATA = map_data
    DASHBOARD_CONFIG = map_data.get("dashboard", {})
    GOAL_LABELS = {
        str(goal_id): str(goal.get("display_label") or goal.get("label") or goal_id)
        for goal_id, goal in map_data.get("goals", {}).items()
    }


def _dynamic_goal_from_text(text):
    """Match configured goal aliases, including bridge-specific Chinese terms."""
    if "bridge" not in str(MAP_DATA.get("map_id", "")).lower():
        return None
    lowered = text.lower()
    for goal_id, goal in MAP_DATA.get("goals", {}).items():
        tokens = [goal_id, goal.get("label", ""), goal.get("display_label", "")]
        tokens.extend(goal.get("aliases", []))
        tokens.extend(BRIDGE_GOAL_KEYWORDS.get(goal_id, []))
        for token in tokens:
            token = str(token).strip().lower()
            if token and token in lowered:
                return goal_id
    return None

def detect_vision_request(text):
    t = text.lower()
    return any(re.search(kw, t) for kw in VISION_KEYWORDS)

def parse_command(text):
    """Parse natural-language command text. Returns (action, goal_id, vision, reply)."""
    text_lower = text.lower().strip()
    vision = detect_vision_request(text_lower)
    dynamic_goal = _dynamic_goal_from_text(text_lower)
    if dynamic_goal:
        label = GOAL_LABELS.get(dynamic_goal, dynamic_goal)
        vnote = "（视觉确认模式）" if vision else ""
        return "fly_to", dynamic_goal, vision, f"收到，目标设为：{label}{vnote}"
    for pattern, action, goal_id in COMMAND_RULES:
        if re.search(pattern, text_lower):
            if action == "fly_to":
                label = GOAL_LABELS.get(goal_id, goal_id)
                vnote = "（视觉确认模式）" if vision else ""
                return action, goal_id, vision, f"收到，目标设为：{label}{vnote}"
            elif action == "hover":
                return action, None, vision, "收到，保持当前位置悬停"
            elif action == "takeoff":
                return action, None, vision, "收到，准备起飞"
            elif action == "land":
                return action, None, vision, "收到，准备降落"
            elif action == "status":
                return action, None, vision, "收到，正在读取无人机状态"
    return "unknown", None, vision, f"无法识别指令“{text}”。可以试试：飞到投递区、返回起点、悬停、降落"


def parse_plan_local(text):
    """Parse explicit multi-step Chinese/English sequencing into queue steps."""
    if not re.search(r"然后|完成后|接着|随后|再|，|,|并(?:降落|返回|悬停|前往|飞到)", text, re.I):
        return None
    fragments = [
        part.strip()
        for part in re.split(
            r"(?:然后|完成后|接着|随后|，|,|并(?=降落|返回|悬停|前往|飞到))",
            text, flags=re.I)
        if part.strip()
    ]
    steps = []
    for fragment in fragments:
        action, goal_id, vision, _ = parse_command(fragment)
        if action == "fly_to" and goal_id:
            steps.append({
                "action": "fly_to",
                "goal_id": goal_id,
                "vision": bool(vision),
            })
        elif action == "hover":
            match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec)", fragment, re.I)
            seconds = float(match.group(1)) if match else 3.0
            steps.append({"action": "hover", "seconds": seconds})
        elif action == "land":
            steps.append({"action": "land"})
    return steps if len(steps) >= 2 else None


def _reply_for_command(action, goal_id, vision):
    """Build the user-facing reply locally; never trust model prose as control data."""
    if action == "fly_to":
        label = GOAL_LABELS.get(goal_id, goal_id)
        vnote = "（视觉确认模式）" if vision else ""
        return f"收到，目标设为：{label}{vnote}"
    if action == "hover":
        return "收到，保持当前位置悬停"
    if action == "takeoff":
        return "收到，准备起飞"
    if action == "land":
        return "收到，准备降落"
    if action == "status":
        return "收到，正在读取无人机状态"
    return "无法识别该指令，请换一种说法"


def parse_plan_with_deepseek(text):
    if not DEEPSEEK_API_KEY:
        return None, "未配置 DeepSeek API 密钥"
    system_prompt = """你是无人机多步骤任务规划解析器，只负责把用户原话转换成待确认队列，不执行飞行。
只输出 JSON 对象：
{"steps":[
 {"action":"fly_to","goal_id":"start_pad|inspection_gate|delivery_zone|medical_station","vision":true或false},
 {"action":"hover","seconds":0.5到30之间的数字},
 {"action":"land"}
]}
规则：
1. 严格保持用户描述的先后顺序，不得遗漏后半句。
2. 返航、返回起点、回家都转换为 fly_to start_pad。
3. 视觉、识别、相机确认只作用于用户明确要求视觉确认的飞行步骤。
4. 如果用户要求最终降落，最后一步必须是 land。
5. 不得创造新地点、动作、参数，不输出解释或 Markdown。"""
    system_prompt = system_prompt.replace(
        "start_pad|inspection_gate|delivery_zone|medical_station",
        "|".join(sorted(GOAL_WHITELIST)))
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text[:1000]},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 320,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + DEEPSEEK_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
                request, timeout=DEEPSEEK_TIMEOUT_SEC) as response:
            api_result = json.loads(response.read().decode("utf-8"))
        content = api_result["choices"][0]["message"]["content"].strip()
        parsed = json.loads(content)
        steps = validate_plan(parsed.get("steps"))
    except urllib.error.HTTPError as exc:
        return None, "DeepSeek API 返回 HTTP %s" % exc.code
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            return None, "DeepSeek API 请求超时（30 秒）"
        return None, "无法连接 DeepSeek API：%s" % reason[:120]
    except TimeoutError:
        return None, "DeepSeek API 请求超时"
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return None, "DeepSeek 多步骤格式无效：%s" % exc
    return steps, None


def parse_command_with_deepseek(text):
    """Use DeepSeek for intent parsing, then enforce the local command whitelist.

    Returns ((action, goal_id, vision, reply), error). A failure returns
    (None, short_error) so the caller can fall back to the deterministic parser.
    """
    if not DEEPSEEK_API_KEY:
        return None, "未配置 DeepSeek API 密钥"

    system_prompt = """你是无人机任务指令解析器，只负责理解意图，不执行飞行。
只输出 JSON 对象，字段必须为：
{"action":"fly_to|hover|takeoff|land|status|unknown",
 "goal_id":"start_pad|inspection_gate|delivery_zone|medical_station|null",
 "vision":true或false}
规则：
1. 投递区=delivery_zone，巡检门/检查门=inspection_gate，医疗站=medical_station，起点/返航/home=start_pad。
2. 只有 action=fly_to 时 goal_id 才能是地点，否则必须为 null。
3. 用户要求看、视觉、识别、相机确认时 vision=true。
4. 不得创造其他动作或地点，不得输出解释、Markdown 或控制参数。"""
    system_prompt = system_prompt.replace(
        "start_pad|inspection_gate|delivery_zone|medical_station",
        "|".join(sorted(GOAL_WHITELIST)))
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text[:1000]},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 160,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + DEEPSEEK_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEEPSEEK_TIMEOUT_SEC) as response:
            api_result = json.loads(response.read().decode("utf-8"))
        content = api_result["choices"][0]["message"]["content"].strip()
        parsed = json.loads(content)
    except urllib.error.HTTPError as exc:
        return None, "DeepSeek API 返回 HTTP %s" % exc.code
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            return None, "DeepSeek API 请求超时（30 秒）"
        return None, "无法连接 DeepSeek API：%s" % reason[:120]
    except TimeoutError:
        return None, "DeepSeek API 请求超时"
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None, "DeepSeek 返回格式无效"

    action = parsed.get("action")
    goal_id = parsed.get("goal_id")
    vision = parsed.get("vision") is True
    if action not in {"fly_to", "hover", "takeoff", "land", "status", "unknown"}:
        return None, "DeepSeek 返回了未允许的动作"
    if action == "fly_to":
        if goal_id not in GOAL_WHITELIST:
            return None, "DeepSeek 返回了未允许的目标"
    else:
        goal_id = None
    return (
        action,
        goal_id,
        vision,
        _reply_for_command(action, goal_id, vision),
    ), None


# ─── Task history / current-state persistence ───────────────────────────────
HISTORY_PATH = os.path.join(_THIS_DIR, "task_history.json")
CURRENT_TASK_PATH = os.path.join(_THIS_DIR, "current_task.json")
PLAN_DIR = os.path.join(_THIS_DIR, "task-plans")
EXPLAINABILITY_DIR = os.path.join(_THIS_DIR, "semantic-explainability")
explainability_store = ExplainabilityStore(EXPLAINABILITY_DIR)
UNREAL_ROUTE_PREVIEW_PATH = os.path.join(
    UE_PROJECT_ROOT, "Saved",
    "semantic_route_preview.json")
trajectory_preview_lock = threading.Lock()
trajectory_preview_state = {
    "fingerprint": None,
    "generated_wall": None,
    "path": UNREAL_ROUTE_PREVIEW_PATH,
    "plan": None,
    "route_point_count": 0,
}


def _plan_fingerprint(plan):
    return json.dumps(plan, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _create_explanation(raw_command, requested_vision, parser_name,
                        fallback_reason, plan, source="command"):
    return explainability_store.create(
        raw_command=raw_command,
        requested_vision=requested_vision,
        parser_name=parser_name,
        model=DEEPSEEK_MODEL if parser_name == "deepseek" else None,
        fallback_reason=fallback_reason,
        plan=plan,
        map_path=DEFAULT_MAP,
        map_data=MAP_DATA,
        source=source,
    )


def _read_explanation_summary(explanation_id):
    if not explanation_id:
        return None
    try:
        return public_summary(explainability_store.read(explanation_id))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def generate_trajectory_preview(raw_steps, explanation_id=None):
    """Validate a semantic plan and publish the exact UE5 route preview."""
    plan = validate_plan(raw_steps)
    review = build_plan_review(plan)
    if review["blocked"]:
        raise ValueError("轨迹规划失败：" + str(review["block_reason"]))

    goals = MAP_DATA.get("goals", {})
    obstacles = MAP_DATA.get("obstacles", [])
    points = [{
        "x_m": 0.0, "y_m": 0.0, "z_m": 0.25,
        "label": "起飞点", "kind": "start",
    }]
    trajectory_legs = []
    current_xy = (0.0, 0.0)
    current_z = 0.25
    previous_goal = "start_pad"
    for step_index, step in enumerate(plan, start=1):
        if step["action"] != "fly_to":
            continue
        goal_id = step["goal_id"]
        goal = goals.get(goal_id, {})
        position = goal.get("position_m", [0.0, 0.0])
        target_xy = (float(position[0]), float(position[1]))
        target_z = float(goal.get("altitude_m", 5.0))
        planned = plan_safe_route(
            current_xy, target_xy, obstacles, DEFAULT_CLEARANCE_M)
        if planned is None:
            raise ValueError("无法为 %s 生成安全轨迹" % goal_id)
        conflicts = intersected_obstacle_ids(
            current_xy, target_xy, obstacles, DEFAULT_CLEARANCE_M)
        trajectory_legs.append({
            "step": step_index,
            "from": previous_goal,
            "to": goal_id,
            "direct_path_obstacles": conflicts,
            "route_points_m": planned["points"],
            "distance_m": planned["distance_m"],
            "detour_waypoints": planned["detour_waypoints"],
            "clearance_m": planned["clearance_m"],
            "decision": (
                "detour_around_map_obstacle"
                if planned["detour_waypoints"] else "direct_path_clear"),
        })
        route_xy = planned["points"][1:]
        leg_count = max(1, len(route_xy))
        for point_index, point_xy in enumerate(route_xy, start=1):
            alpha = point_index / float(leg_count)
            is_target = point_index == leg_count
            points.append({
                "x_m": round(float(point_xy[0]), 3),
                "y_m": round(float(point_xy[1]), 3),
                "z_m": round(current_z + (target_z - current_z) * alpha, 3),
                "label": (
                    goal.get("display_label") or goal.get("label") or goal_id
                    if is_target else "安全绕行点"),
                "kind": "goal" if is_target else "detour",
                "goal_id": goal_id if is_target else None,
            })
        current_xy = target_xy
        current_z = target_z
        previous_goal = goal_id

    payload = {
        "schema": "sztu.semantic_route_preview.v1",
        "map_id": MAP_DATA.get("map_id"),
        "frame": "PX4_LOCAL_NED",
        "origin_world_m": MAP_DATA.get("origin_world_m", [0.0, 0.0, 0.0]),
        "generated_wall": time.time(),
        "plan_fingerprint": _plan_fingerprint(plan),
        "route_points": points,
        "trajectory_legs": trajectory_legs,
        "plan": plan,
    }
    os.makedirs(os.path.dirname(UNREAL_ROUTE_PREVIEW_PATH), exist_ok=True)
    temporary_path = UNREAL_ROUTE_PREVIEW_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as route_file:
        json.dump(payload, route_file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, UNREAL_ROUTE_PREVIEW_PATH)
    with trajectory_preview_lock:
        trajectory_preview_state.update({
            "fingerprint": payload["plan_fingerprint"],
            "generated_wall": payload["generated_wall"],
            "path": UNREAL_ROUTE_PREVIEW_PATH,
            "plan": plan,
            "route_point_count": len(points),
        })
    if explanation_id:
        explainability_store.update_plan(explanation_id, plan, MAP_DATA)
    else:
        record = _create_explanation(
            "控制台任务队列", False, "operator", None, plan,
            source="operator_plan")
        explanation_id = record["explanation_id"]
    explainability_store.attach_review(explanation_id, review)
    trajectory_record = {
        "schema": payload["schema"],
        "frame": payload["frame"],
        "generated_wall": payload["generated_wall"],
        "preview_path": UNREAL_ROUTE_PREVIEW_PATH,
        "preview_sha256": file_sha256(UNREAL_ROUTE_PREVIEW_PATH),
        "plan_fingerprint": payload["plan_fingerprint"],
        "route_point_count": len(points),
        "route_points": points,
        "legs": trajectory_legs,
    }
    record = explainability_store.attach_trajectory(
        explanation_id, trajectory_record)
    return {
        "ok": True,
        "plan": plan,
        "review": review,
        "route_point_count": len(points),
        "trajectory_path": UNREAL_ROUTE_PREVIEW_PATH,
        "generated_wall": payload["generated_wall"],
        "explanation_id": explanation_id,
        "explainability": public_summary(record),
    }


def _load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _append_history(record):
    hist = _load_history()
    hist.append(record)
    hist = hist[-50:]
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _write_current(task_id):
    try:
        with open(CURRENT_TASK_PATH, "w", encoding="utf-8") as f:
            json.dump({"task_id": task_id}, f)
    except Exception:
        pass


def _clear_current():
    try:
        if os.path.exists(CURRENT_TASK_PATH):
            os.remove(CURRENT_TASK_PATH)
    except Exception:
        pass


def _read_current():
    try:
        with open(CURRENT_TASK_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("task_id")
    except Exception:
        return None


# ─── Background task execution ──────────────────────────────────────────────
def _new_task_id():
    global _task_counter
    with tasks_lock:
        _task_counter += 1
        return "task_%04d" % _task_counter


def _wsl_sync(src_windows, dst_leaf):
    """Mirror a Windows file into ~/szefitl on WSL. The Windows source path is
    converted to a /mnt/c/... WSL path (drive letter + forward slashes) and
    passed as a single quoted shell argument so Windows encodes it as UTF-16 for
    CreateProcess (wsl receives it correctly)."""
    src_wsl = windows_to_wsl(src_windows)
    safe = src_wsl.replace("'", "'\\''")
    inner = "mkdir -p ~/szefitl && cp '%s' ~/szefitl/%s" % (safe, dst_leaf)
    cmd = ["wsl.exe", "-d", WSL_DISTRO, "--", "bash", "-lc", inner]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception:
        pass


def _append_task_log(task, line):
    clean = line.replace("\x00", "").rstrip("\r\n")
    if clean:
        task["log"].append(clean)
        if len(task["log"]) > 2000:
            del task["log"][:500]
        s = _stage_for_line(clean)
        if s > task.get("stage_index", -1):
            task["stage_index"] = s
            task["stage"] = STAGE_NAMES[s]


def _newest_semantic_run(started_wall):
    if not os.path.isdir(SEMANTIC_RUNS_DIR):
        return None
    candidates = []
    for name in os.listdir(SEMANTIC_RUNS_DIR):
        run_dir = os.path.join(SEMANTIC_RUNS_DIR, name)
        summary_path = os.path.join(run_dir, "summary.md")
        if os.path.isfile(summary_path):
            mtime = os.path.getmtime(summary_path)
            if mtime >= started_wall - 5:
                candidates.append((mtime, run_dir))
    return max(candidates, default=(None, None))[1]


def _controller_failure_label(task):
    """Turn common wrapper startup failures into a useful terminal UI label."""
    log_text = "\n".join(task.get("log") or [])
    if "UE did not listen on TCP 4560" in log_text:
        return "启动失败：UE 未在限定时间内完成启动（TCP 4560 未监听）"
    if "UE/PX4 startup failed after 2 attempts" in log_text:
        return "启动失败：UE/PX4 自动重试后仍未连接"
    if "PX4/WSL 环境检查失败" in log_text:
        return "启动失败：PX4/WSL 环境检查未通过"
    if "PX4 did not" in log_text or "PX4Ready: False" in log_text:
        return "启动失败：PX4 未进入可执行任务状态"
    return "CHECK/FAIL"


def run_controller_task(task_id, goal_id, vision, plan=None):
    """Run the validated end-to-end Windows wrapper in a guarded worker thread."""
    task = tasks.get(task_id)
    if task is None:
        return
    acquired = mission_lock.acquire(blocking=False)
    if not acquired:
        task["status"] = "error"
        task["error"] = "已有任务正在运行"
        task["finished_wall"] = time.time()
        return
    try:
        task["status"] = "running"
        task["started_wall"] = time.time()
        _write_current(task_id)
        _append_task_log(task, "[系统] 正在检查 PX4/WSL 环境")
        env_check = subprocess.run(
            [
                POWERSHELL_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", ENV_CHECK_PATH,
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
        for raw in (env_check.stdout or b"").split(b"\n"):
            _append_task_log(task, _decode_bytes(raw))
        if task.get("stop_requested"):
            task["status"] = "stopped"
            task["outcome"] = "STOPPED"
            task["error"] = "用户已停止任务"
            return
        if env_check.returncode != 0:
            raise RuntimeError("PX4/WSL 环境检查失败")

        cmd = [
            POWERSHELL_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", SEMANTIC_WRAPPER_PATH,
            "-Goal", goal_id,
            "-SemanticMapFile", DEFAULT_MAP,
            "-UeMap", DEFAULT_UE_MAP,
            "-ProjectRoot", UE_PROJECT_ROOT,
            "-MissionTimeoutSec", str(int(DASHBOARD_CONFIG.get("mission_timeout_s", 70))),
            "-EstimatorSettleSec", str(int(DASHBOARD_CONFIG.get("estimator_settle_s", 35))),
        ]
        if DASHBOARD_CONFIG.get("archive_frames"):
            cmd.append("-ArchiveFrames")
        if DASHBOARD_CONFIG.get("camera_capture_hz") is not None:
            cmd.extend(["-CameraCaptureHz", str(DASHBOARD_CONFIG["camera_capture_hz"])])
        if DASHBOARD_CONFIG.get("camera_width") is not None:
            cmd.extend(["-CameraWidth", str(int(DASHBOARD_CONFIG["camera_width"]))])
        if DASHBOARD_CONFIG.get("camera_height") is not None:
            cmd.extend(["-CameraHeight", str(int(DASHBOARD_CONFIG["camera_height"]))])
        if DASHBOARD_CONFIG.get("wind_mps") is not None:
            cmd.extend(["-WindMps", str(float(DASHBOARD_CONFIG["wind_mps"]))])
        if plan:
            os.makedirs(PLAN_DIR, exist_ok=True)
            plan_path = os.path.join(PLAN_DIR, task_id + ".json")
            with open(plan_path, "w", encoding="utf-8") as plan_file:
                json.dump({"steps": plan}, plan_file, ensure_ascii=False, indent=2)
            task["plan_path"] = plan_path
            cmd.extend(["-PlanFile", plan_path])
        if vision:
            cmd.append("-Vision")
        if DASHBOARD_CONFIG.get("follow_drone_camera"):
            cmd.append("-FollowDroneCamera")
        if DASHBOARD_CONFIG.get("dual_camera_view"):
            cmd.append("-DualCameraView")
        task["cmd"] = (
            "run_semantic_city.ps1 %s%s" % (
                ("-PlanFile %s" % task.get("plan_path"))
                if plan else ("-Goal %s" % goal_id),
                " -Vision" if vision else ""))
        _append_task_log(task, "[系统] 正在启动 UE/PX4 完整任务")
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=1,
        )
        task["proc"] = proc
        for raw in iter(proc.stdout.readline, b""):
            _append_task_log(task, _decode_bytes(raw))
            # Break as soon as a stop is requested. Do NOT wait for proc.poll()
            # to become non-None: stop_task kills the wrapper powershell, but a
            # detached child (UE/PX4) may keep the stdout pipe open and block
            # readline for minutes on a weak machine. The terminal status is
            # written by the finally block after proc.wait() returns.
            if task.get("stop_requested"):
                break
        if task.get("stop_requested") and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        # After a stop request, don't block the full 420s waiting on the wrapper;
        # the safety net has already killed UE/PX4, so give it a short grace then force-kill.
        try:
            proc.wait(timeout=30 if task.get("stop_requested") else 420)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        task["returncode"] = proc.returncode

        if task.get("stop_requested"):
            task["status"] = "stopped"
            task["outcome"] = "STOPPED"
            task["error"] = task.get("error") or "用户已停止任务"
        else:
            run_dir = _newest_semantic_run(task["started_wall"])
            summary_text = ""
            result_json = None
            if run_dir:
                summary_path = os.path.join(run_dir, "summary.md")
                result_path = os.path.join(run_dir, "mission_result.json")
                task["report_path"] = summary_path
                try:
                    with open(summary_path, "r", encoding="utf-8-sig") as summary_file:
                        summary_text = summary_file.read()
                except Exception:
                    summary_text = ""
                if os.path.isfile(result_path):
                    try:
                        with open(result_path, "r", encoding="utf-8") as result_file:
                            result_json = json.load(result_file)
                    except Exception:
                        result_json = None
            task["summary"] = summary_text
            task["result_json"] = result_json
            passed = (
                proc.returncode == 0
                and "- Verdict: PASS" in summary_text
                and result_json is not None
                and result_json.get("pass") is True
            )
            task["outcome"] = "PASS" if passed else _controller_failure_label(task)
            task["status"] = "done"
            if not passed and not task["error"]:
                if task["outcome"] != "CHECK/FAIL":
                    task["error"] = task["outcome"]
                else:
                    task["error"] = "完整任务未满足 PASS 条件，请查看报告"
    except subprocess.TimeoutExpired:
        task["status"] = "error"
        task["error"] = "完整任务运行超时（超过 420 秒）"
        task["outcome"] = "TIMEOUT"
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
        task["outcome"] = "ERROR"
    finally:
        # The worker is the SOLE authority on the final (terminal) status.
        # Guarantee a terminal state so the UI can never get stuck on
        # "stopping"/"running" due to a race with stop_task's transient write.
        if task.get("stop_requested") and task.get("status") not in ("done", "stopped", "error"):
            task["status"] = "stopped"
            task["outcome"] = task.get("outcome") or "STOPPED"
            task["error"] = task.get("error") or "用户已停止任务"
        if task.get("status") not in ("done", "stopped", "error"):
            task["status"] = "error"
            task["outcome"] = task.get("outcome") or "ERROR"
            if not task.get("error"):
                task["error"] = "任务异常结束"
        task["finished_wall"] = time.time()
        explanation_id = task.get("explanation_id")
        if explanation_id:
            try:
                explainability_store.attach_execution(
                    explanation_id,
                    task_id,
                    task.get("status"),
                    outcome=task.get("outcome"),
                    error=task.get("error"),
                    returncode=task.get("returncode"),
                    report_path=task.get("report_path"),
                    report_sha256=(
                        file_sha256(task["report_path"])
                        if task.get("report_path")
                        and os.path.isfile(task["report_path"]) else None),
                    mission_result=task.get("result_json"),
                    started_wall=task.get("started_wall"),
                    finished_wall=task.get("finished_wall"),
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                _append_task_log(task, "[解释记录] 最终结果写入失败：%s" % exc)
        _record_task_history(task)
        _clear_current()
        try:
            mission_lock.release()
        except Exception:
            pass


def execute_command(action, goal_id, vision=False):
    """Dispatch a parsed command. fly_to launches the verified full-chain wrapper."""
    if action == "fly_to" and goal_id:
        if goal_id not in GOAL_WHITELIST:
            return {"error": "目标“%s”不在允许列表中" % goal_id}
        tid = _new_task_id()
        with tasks_lock:
            active = any(
                task.get("status") in {"queued", "running"}
                for task in tasks.values()
            )
            if active or mission_lock.locked():
                return {"error": "已有任务正在运行，请等待其完成"}
            tasks[tid] = {
                "task_id": tid,
                "action": action,
                "goal_id": goal_id,
                "vision": vision,
                "status": "queued",
                "log": [],
                "cmd": "",
                "returncode": None,
                "result_json": None,
                "outcome": None,
                "error": None,
                "report_path": None,
                "summary": "",
                "started_wall": None,
                "finished_wall": None,
                "stage": "启动",
                "stage_index": 0,
                "proc": None,
                "stop_requested": False,
                "history_written": False,
            }
        if goal_id == "start_pad":
            # Every one-click mission currently owns a fresh UE/PX4 session and
            # cleans it up when complete. With no active mission there is no
            # airborne vehicle to return: the next session's initial state is
            # already A Start. Do not launch a redundant flight whose downward
            # camera cannot see the marker directly under the spawn point.
            now = time.time()
            task = tasks[tid]
            message = "检测到当前已在 A 起点，无需返航。"
            task.update({
                "status": "done",
                "outcome": "PASS",
                "returncode": 0,
                "started_wall": now,
                "finished_wall": now,
                "stage": "清理",
                "stage_index": 3,
                "cmd": "noop: already at start_pad",
                "summary": (
                    "# 返回起点状态\n\n"
                    "- Verdict: PASS\n"
                    "- 状态：检测到当前已在 A 起点\n"
                    "- 动作：无需启动 UE/PX4 返航任务\n"
                    "- 原因：当前一键任务采用冷启动并在结束后清理会话\n"
                ),
                "result_json": {
                    "pass": True,
                    "goal_id": "start_pad",
                    "already_at_start": True,
                    "flight_started": False,
                    "vision_requested": bool(vision),
                },
            })
            _append_task_log(task, "[系统] " + message)
            _append_task_log(task, "[系统] 未启动 UE/PX4；未执行飞行、视觉确认或避障")
            _record_task_history(task)
            _clear_current()
            return {
                "task_id": tid,
                "status": "done",
                "vision": vision,
                "message": message,
                "already_at_start": True,
            }
        t = threading.Thread(
            target=run_controller_task,
            args=(tid, goal_id, vision),
            daemon=True,
        )
        t.start()
        return {"task_id": tid, "status": "queued", "vision": vision}
    elif action == "status":
        with telemetry_lock:
            t = latest_telemetry
        if not t:
            return {"result": "尚未收到实时遥测"}
        return {"result": (
            f"位置=({t.get('position_x_m',0):.1f}, {-t.get('position_y_m',0):.1f}, {t.get('position_z_m',0):.1f}) 米  "
            f"速度=({t.get('velocity_x_mps',0):.1f}, {-t.get('velocity_y_mps',0):.1f}, {t.get('velocity_z_mps',0):.1f}) 米/秒  "
            f"姿态=({t.get('roll_deg',0):.1f}, {t.get('pitch_deg',0):.1f}, {-t.get('yaw_deg',0):.1f}) 度  "
            f"区域={t.get('semantic_zone','?')}  "
            f"风速={t.get('wind_speed_mps',0):.1f} 米/秒")}
    elif action in ("hover", "takeoff", "land"):
        return {"warning": f"已接收 {action} 指令；需要 PX4 OFFBOARD 连接，本页面不会自动启动"}
    return {"error": "未执行任何操作"}


def validate_plan(raw_steps):
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("任务队列不能为空")
    if len(raw_steps) > 12:
        raise ValueError("任务步骤不能超过 12 个")
    normalized = []
    land_seen = False
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError("第 %d 步格式无效" % (index + 1))
        action = str(raw.get("action", "")).strip().lower()
        if land_seen:
            raise ValueError("降落必须是最后一步")
        if action == "fly_to":
            goal_id = str(raw.get("goal_id", "")).strip()
            if goal_id not in GOAL_WHITELIST:
                raise ValueError("第 %d 步目标无效" % (index + 1))
            fly_through = bool(raw.get("fly_through", False))
            if fly_through and bool(raw.get("vision", False)):
                raise ValueError("连续通过航点不能同时要求视觉悬停确认")
            normalized.append({
                "action": "fly_to",
                "goal_id": goal_id,
                "vision": bool(raw.get("vision", False)),
                "fly_through": fly_through,
            })
        elif action == "hover":
            try:
                seconds = float(raw.get("seconds", 3))
            except (TypeError, ValueError):
                raise ValueError("第 %d 步悬停时间无效" % (index + 1))
            if not 0.5 <= seconds <= 30:
                raise ValueError("悬停时间必须在 0.5 到 30 秒之间")
            normalized.append({"action": "hover", "seconds": seconds})
        elif action == "land":
            normalized.append({"action": "land"})
            land_seen = True
        else:
            raise ValueError("第 %d 步动作不支持" % (index + 1))
    if not any(step["action"] == "fly_to" for step in normalized):
        raise ValueError("任务队列至少需要一个飞行目标")
    if not land_seen:
        normalized.append({"action": "land"})
    return normalized


def build_plan_review(raw_steps):
    """Build the authoritative local pre-flight facts for a normalized plan."""
    plan = validate_plan(raw_steps)
    try:
        with open(DEFAULT_MAP, "r", encoding="utf-8") as map_file:
            map_data = json.load(map_file)
            goals = map_data.get("goals", {})
            obstacles = map_data.get("obstacles", [])
    except Exception:
        goals = {}
        obstacles = []

    current = (0.0, 0.0)
    route_distance = 0.0
    fly_steps = []
    hover_seconds = 0.0
    warnings = []
    obstacle_conflicts = []
    rerouted_legs = []
    unplannable_legs = []
    previous_goal = None
    for index, step in enumerate(plan):
        if step["action"] == "fly_to":
            goal_id = step["goal_id"]
            position = goals.get(goal_id, {}).get("position_m", [0.0, 0.0])
            target = (float(position[0]), float(position[1]))
            conflicts = intersected_obstacle_ids(
                current, target, obstacles, DEFAULT_CLEARANCE_M)
            planned = plan_safe_route(
                current, target, obstacles, DEFAULT_CLEARANCE_M)
            if planned is None:
                unplannable_legs.append({
                    "step": index + 1,
                    "from": previous_goal or "start_pad",
                    "to": goal_id,
                    "obstacle_ids": conflicts,
                })
                route_distance += math.hypot(
                    target[0] - current[0], target[1] - current[1])
            else:
                route_distance += planned["distance_m"]
                if planned["detour_waypoints"]:
                    rerouted_legs.append({
                        "step": index + 1,
                        "from": previous_goal or "start_pad",
                        "to": goal_id,
                        "obstacle_ids": conflicts,
                        "waypoints_m": planned["points"][1:-1],
                        "distance_m": planned["distance_m"],
                        "clearance_m": planned["clearance_m"],
                    })
            current = target
            fly_steps.append({
                "index": index + 1,
                "goal_id": goal_id,
                "label": goals.get(goal_id, {}).get("label", goal_id),
                "vision": bool(step.get("vision")),
            })
            if previous_goal == goal_id:
                warnings.append("存在连续重复目标：%s" % goal_id)
            previous_goal = goal_id
        elif step["action"] == "hover":
            hover_seconds += float(step["seconds"])

    vision_count = sum(1 for step in fly_steps if step["vision"])
    returns_home = bool(
        fly_steps and fly_steps[-1]["goal_id"] == "start_pad")
    if not returns_home:
        warnings.append("最后一个飞行目标不是 A 起点；任务会在其他目标附近降落")
    if route_distance >= 30.0:
        warnings.append("总航程较长，建议重点关注返航和降落日志")
    if len(fly_steps) >= 4:
        warnings.append("飞行航段较多，任务复杂度较高")
    if vision_count == 0:
        warnings.append("没有视觉确认步骤；任务只依赖位置与本地规则")
    for reroute in rerouted_legs:
        warnings.append(
            "第%d航段 %s→%s 将自动绕行 %s（%d 个安全航点）" % (
                reroute["step"], reroute["from"], reroute["to"],
                "、".join(reroute["obstacle_ids"]) or "地图障碍物",
                len(reroute["waypoints_m"])))
    for blocked in unplannable_legs:
        warnings.append(
            "第%d航段 %s→%s 无法生成安全航路" % (
                blocked["step"], blocked["from"], blocked["to"]))

    risk_score = 0
    if route_distance >= 30.0:
        risk_score += 1
    if len(fly_steps) >= 3:
        risk_score += 1
    if not returns_home:
        risk_score += 1
    if any("重复目标" in warning for warning in warnings):
        risk_score += 1
    if unplannable_legs:
        risk_score = max(risk_score, 3)
    risk = "高" if risk_score >= 3 else ("中" if risk_score >= 1 else "低")
    estimated_seconds = int(round(
        45.0 + route_distance / 1.25 + hover_seconds + len(fly_steps) * 5.0))
    return {
        "plan": plan,
        "risk": risk,
        "route_distance_m": round(route_distance, 1),
        "estimated_seconds": estimated_seconds,
        "fly_leg_count": len(fly_steps),
        "vision_leg_count": vision_count,
        "returns_home": returns_home,
        "lands_last": plan[-1]["action"] == "land",
        "blocked": bool(unplannable_legs),
        "block_reason": (
            "至少一个航段无法生成满足安全余量的地图航路"
            if unplannable_legs else None),
        "obstacle_conflicts": obstacle_conflicts,
        "rerouted_legs": rerouted_legs,
        "rerouted_leg_count": len(rerouted_legs),
        "planned_clearance_m": DEFAULT_CLEARANCE_M,
        "unplannable_legs": unplannable_legs,
        "warnings": warnings,
        "route": fly_steps,
    }


def review_plan_with_deepseek(local_review):
    """Ask DeepSeek to explain local facts; it cannot alter or approve the plan."""
    if not DEEPSEEK_API_KEY:
        return None, "未配置 DeepSeek API 密钥"
    system_prompt = """你是无人机飞前审查助手，只解释本地安全审查已经计算出的事实。
只输出 JSON：
{"summary":"一句话概括任务","warnings":["最多三条简短提示"],"recommendation":"一句话操作建议"}
不得修改任务步骤，不得生成飞控参数，不得声称任务绝对安全。"""
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(
                local_review, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 320,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + DEEPSEEK_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
                request, timeout=DEEPSEEK_TIMEOUT_SEC) as response:
            api_result = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(
            api_result["choices"][0]["message"]["content"].strip())
        summary = str(parsed.get("summary", "")).strip()
        recommendation = str(parsed.get("recommendation", "")).strip()
        warnings = [
            str(value).strip() for value in parsed.get("warnings", [])
            if str(value).strip()
        ][:3]
        if not summary or not recommendation:
            raise ValueError("missing review fields")
        return {
            "summary": summary[:300],
            "warnings": warnings,
            "recommendation": recommendation[:300],
        }, None
    except urllib.error.HTTPError as exc:
        return None, "DeepSeek API 返回 HTTP %s" % exc.code
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        return None, "DeepSeek 飞前审查连接失败：%s" % reason[:120]
    except TimeoutError:
        return None, "DeepSeek 飞前审查请求超时"
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None, "DeepSeek 飞前审查返回格式无效"


def execute_plan(raw_steps, explanation_id=None):
    try:
        plan = validate_plan(raw_steps)
        review = build_plan_review(plan)
    except ValueError as exc:
        return {"error": str(exc)}
    if review["blocked"]:
        return {
            "error": "飞前审查阻止执行：" + review["block_reason"],
            "review": review,
        }
    fingerprint = _plan_fingerprint(plan)
    with trajectory_preview_lock:
        generated_fingerprint = trajectory_preview_state.get("fingerprint")
    if generated_fingerprint != fingerprint:
        return {
            "error": "请先点击“生成 UE5 轨迹”，确认场景中出现航线后再执行",
            "requires_trajectory_preview": True,
        }
    if not explanation_id:
        record = _create_explanation(
            "控制台任务队列", False, "operator", None, plan,
            source="operator_plan")
        explanation_id = record["explanation_id"]
        explainability_store.attach_review(explanation_id, review)
        explainability_store.attach_trajectory(explanation_id, {
            "plan_fingerprint": fingerprint,
            "preview_path": UNREAL_ROUTE_PREVIEW_PATH,
            "preview_sha256": (
                file_sha256(UNREAL_ROUTE_PREVIEW_PATH)
                if os.path.isfile(UNREAL_ROUTE_PREVIEW_PATH) else None),
            "route_point_count": trajectory_preview_state.get(
                "route_point_count", 0),
        })
    try:
        explanation_record = explainability_store.read(explanation_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"error": "解释记录不存在，请重新生成 UE5 轨迹"}
    if (
        explanation_record.get("plan_sha256") != canonical_sha256(plan)
        or (explanation_record.get("trajectory") or {}).get(
            "plan_fingerprint") != fingerprint
    ):
        return {"error": "解释记录与当前计划不一致，请重新生成 UE5 轨迹"}
    with tasks_lock:
        active = any(
            task.get("status") in {"queued", "running"}
            for task in tasks.values())
        if active or mission_lock.locked():
            return {"error": "已有任务正在运行，请等待其完成"}
    tid = _new_task_id()
    fly_steps = [step for step in plan if step["action"] == "fly_to"]
    final_goal = fly_steps[-1]["goal_id"]
    vision = any(step.get("vision", False) for step in fly_steps)
    with tasks_lock:
        tasks[tid] = {
            "task_id": tid,
            "action": "sequence",
            "goal_id": final_goal,
            "vision": vision,
            "plan": plan,
            "status": "queued",
            "log": [],
            "cmd": "",
            "returncode": None,
            "result_json": None,
            "outcome": None,
            "error": None,
            "report_path": None,
            "summary": "",
            "started_wall": None,
            "finished_wall": None,
            "stage": "启动",
            "stage_index": 0,
            "proc": None,
            "stop_requested": False,
            "history_written": False,
            "explanation_id": explanation_id,
        }
    explainability_store.attach_execution(
        explanation_id, tid, "queued", outcome=None, error=None)
    worker = threading.Thread(
        target=run_controller_task,
        args=(tid, final_goal, vision, plan),
        daemon=True)
    worker.start()
    return {
        "task_id": tid,
        "status": "queued",
        "vision": vision,
        "plan": plan,
        "explanation_id": explanation_id,
        "explainability": _read_explanation_summary(explanation_id),
    }


def get_task_status(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return None
        # Return a copy without the (non-serializable) process handle.
        d = dict(task)
        d.pop("proc", None)
        return d


def _record_task_history(task):
    """Append a finished task to the persistent history file (once)."""
    if task.get("history_written"):
        return
    task["history_written"] = True
    _append_history({
        "task_id": task.get("task_id"),
        "action": task.get("action"),
        "goal_id": task.get("goal_id"),
        "vision": task.get("vision"),
        "outcome": task.get("outcome"),
        "status": task.get("status"),
        "error": task.get("error"),
        "report_path": task.get("report_path"),
        "started": task.get("started_wall"),
        "finished": task.get("finished_wall"),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.get("finished_wall") or time.time())),
        "cmd": task.get("cmd"),
        "plan": task.get("plan"),
        "explanation_id": task.get("explanation_id"),
    })


def stop_task(task_id):
    """Terminate a running task's process tree (UE/PX4 included)."""
    with tasks_lock:
        task = tasks.get(task_id)
    if task is None:
        return False, "任务不存在"
    # Never clobber an already-terminal task. This avoids a race with the
    # worker's finally block (which writes the terminal status last) that would
    # otherwise leave the live task stuck on "stopping".
    if task.get("status") in ("done", "stopped", "error"):
        return True, "任务已结束"
    task["stop_requested"] = True
    proc = task.get("proc")
    killed = False
    if proc is not None and proc.poll() is None:
        pid = proc.pid
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15)
            killed = True
        except Exception:
            pass
        # Safety net: kill dedicated simulation processes by name so the wrapper's
        # powershell (which may wait on them) exits promptly instead of hanging.
        for name in ("UnrealEditor", "px4", "mavlink_relay", "IFSC-Station", "QGroundControl"):
            try:
                subprocess.run(["taskkill", "/F", "/IM", name + ".exe"],
                               capture_output=True, timeout=15)
            except Exception:
                pass
    # NOTE: This function must NOT write a non-terminal status (e.g. "stopping")
    # into the shared task dict. The worker thread is the SOLE authority on the
    # final status and writes it as its last act in the finally block. If
    # stop_task also wrote "stopping", a late write from this HTTP thread could
    # clobber the worker's "stopped" and leave the UI stuck on a non-terminal
    # state. The UI derives the "正在停止" hint from the stop_requested flag.
    return True, "已发送停止信号" + ("（进程树已终止）" if killed else "（正在等待进程退出）")


# ─── HTTP Server ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__DASHBOARD_TITLE__</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',system-ui,sans-serif}
body{background:#f0f2f5;color:#333;overflow:hidden;height:100vh}
.dashboard{display:grid;grid-template-columns:280px 1fr 340px;gap:8px;padding:8px;height:100vh}
.panel{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1);display:flex;flex-direction:column;overflow:hidden}
.panel-header{padding:10px 14px;font-size:14px;font-weight:600;border-bottom:1px solid #e8e8e8;display:flex;align-items:center;gap:6px}
.panel-body{flex:1;overflow-y:auto;padding:12px}
#map-panel .panel-body{padding:0;position:relative}
#map-canvas{display:block;width:100%;height:100%}
.status-item{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #f0f0f0;font-size:13px}
.status-label{color:#888}
.status-value{font-weight:600;font-family:'Consolas',monospace}
.status-value.warn{color:#e8533f}
.status-value.ok{color:#52c41a}
.indicator{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.indicator.on{background:#52c41a}
.indicator.off{background:#d9d9d9}
.chat-log{flex:1;min-height:80px;overflow-y:auto;padding:10px;font-size:13px}
.chat-msg{margin-bottom:8px;padding:8px 12px;border-radius:8px;max-width:92%}
.chat-msg.user{background:#1890ff;color:#fff;margin-left:auto;text-align:right}
.chat-msg.ai{background:#f0f0f0;color:#333}
.chat-msg.system{background:#fff7e6;color:#d48806;font-size:12px;white-space:pre-wrap;word-break:break-word}
.chat-input{display:flex;gap:6px;padding:10px;border-top:1px solid #e8e8e8}
.chat-input input{flex:1;padding:8px 12px;border:1px solid #d9d9d9;border-radius:4px;font-size:13px}
.chat-input button{padding:8px 16px;background:#1890ff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px}
.chat-input button:hover{background:#40a9ff}
.chat-input button:disabled{background:#d9d9d9;cursor:not-allowed}
.quick-cmds{display:flex;flex-wrap:wrap;gap:4px;padding:6px 10px}
.quick-cmd{padding:4px 10px;background:#f0f0f0;border-radius:12px;font-size:12px;cursor:pointer;border:1px solid #e8e8e8}
.quick-cmd:hover{background:#e6f7ff;border-color:#1890ff}
.vision-toggle{display:flex;align-items:center;gap:6px;padding:6px 10px;font-size:12px;color:#666;border-top:1px solid #e8e8e8}
.plan-builder{margin:0 10px 8px;padding:8px;border:1px solid #d9e8f5;border-radius:7px;background:#f8fbff;font-size:12px}
.semantic-command{margin:8px 10px;padding:9px;border:1px solid #91caff;border-radius:7px;background:#e6f4ff}
.semantic-command-title{font-weight:600;color:#0958d9;margin-bottom:6px}
.semantic-command-row{display:grid;grid-template-columns:1fr auto;gap:6px}
.semantic-command select{min-width:0;padding:7px 8px;border:1px solid #91caff;border-radius:4px;background:#fff;font-size:12px}
.semantic-command button{padding:7px 11px;border:0;border-radius:4px;background:#722ed1;color:#fff;cursor:pointer;font-size:12px}
.semantic-command button:disabled{background:#bfbfbf;cursor:not-allowed}
.trajectory-state{margin-top:6px;color:#531dab;line-height:1.4}
.plan-title{display:flex;justify-content:space-between;align-items:center;font-weight:600;margin-bottom:6px}
.plan-list{max-height:112px;overflow-y:auto}
.plan-empty{color:#999;padding:8px;text-align:center}
.plan-step{display:grid;grid-template-columns:22px 1fr auto;gap:5px;align-items:center;background:#fff;border:1px solid #e8e8e8;border-radius:5px;padding:5px;margin-bottom:4px}
.plan-step .plan-actions{display:flex;gap:3px}
.plan-step button{border:1px solid #d9d9d9;background:#fff;border-radius:3px;cursor:pointer;padding:1px 5px}
.plan-step button:hover{border-color:#1890ff;color:#1890ff}
.plan-controls{display:flex;gap:5px;margin-top:6px}
.plan-controls button{flex:1;padding:6px;border-radius:4px;border:1px solid #1890ff;background:#fff;color:#1677ff;cursor:pointer}
.plan-controls button.primary{background:#1890ff;color:#fff}
.plan-controls button:disabled{border-color:#d9d9d9;background:#f5f5f5;color:#aaa;cursor:not-allowed}
.plan-confirmed{margin-top:6px;color:#389e0d;white-space:pre-wrap;max-height:44px;overflow-y:auto}
.plan-review{display:none;margin-top:6px;padding:7px;border-radius:5px;background:#f6ffed;border:1px solid #b7eb8f;color:#37651f;white-space:pre-wrap;max-height:110px;overflow-y:auto;line-height:1.45}
.plan-review.risk-medium{background:#fffbe6;border-color:#ffe58f;color:#8a5a00}
.plan-review.risk-high{background:#fff2f0;border-color:#ffccc7;color:#a61d24}
.explainability-card{display:none;margin-top:6px;padding:7px;border-radius:5px;background:#f0f5ff;border:1px solid #adc6ff;color:#10239e;font-size:11px;line-height:1.45;white-space:pre-wrap}
.explainability-card a{color:#1677ff;text-decoration:underline;cursor:pointer}
.task-status{flex:0 0 270px;margin:4px 10px 8px;padding:8px 10px;background:#fafafa;border:1px solid #d9e8f5;border-radius:6px;font-size:12px;font-family:'Consolas',monospace;white-space:pre-wrap;word-break:break-word;max-height:270px;overflow:hidden}
.task-log-head{display:flex;align-items:center;justify-content:space-between;margin-top:5px;font-family:'Segoe UI',system-ui,sans-serif;font-weight:600}
.task-log-head button{padding:2px 8px;border:1px solid #91caff;background:#e6f4ff;color:#1677ff;border-radius:4px;cursor:pointer;font-size:11px}
#task-log{height:145px;overflow-y:auto;margin-top:4px;padding:6px;background:#111827;color:#d1fae5;border-radius:4px;white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;font-size:11px}
.log-modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:70}
.log-modal-box{width:min(960px,92vw);height:min(720px,86vh);background:#fff;border-radius:10px;padding:12px;display:flex;flex-direction:column}
.log-modal-head{display:flex;align-items:center;justify-content:space-between;font-weight:600;margin-bottom:8px}
.log-modal-head button{padding:5px 12px;border:1px solid #d9d9d9;background:#fff;border-radius:4px;cursor:pointer}
#log-modal-content{flex:1;overflow:auto;background:#111827;color:#d1fae5;padding:12px;border-radius:6px;white-space:pre-wrap;word-break:break-word;font:12px/1.5 Consolas,monospace}
.ai-badge{margin-left:auto;padding:2px 8px;border-radius:10px;background:#f5f5f5;color:#999;font-size:11px;font-weight:400}
.ai-badge.on{background:#e6f7ff;color:#1677ff}
.ai-config{display:flex;gap:6px;align-items:center;padding:8px 10px;background:#fafafa;border-bottom:1px solid #e8e8e8}
.ai-config input{flex:1;min-width:0;padding:6px 9px;border:1px solid #d9d9d9;border-radius:4px;font-size:12px}
.ai-config button{padding:6px 10px;background:#1677ff;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:12px}
.ai-config button:disabled{background:#bbb;cursor:not-allowed}
.ai-config-status{font-size:11px;color:#666;white-space:nowrap}
.stepper{display:flex;gap:4px;margin:8px 0}
.stepper .step{flex:1;text-align:center;font-size:11px;padding:6px 2px;border-radius:6px;background:#f0f0f0;color:#999;border:1px solid #e8e8e8;transition:.2s}
.stepper .step.active{background:#1890ff;color:#fff;border-color:#1890ff}
.stepper .step.done{background:#e6f7ff;color:#1677ff;border-color:#91d5ff}
.btn-stop{width:100%;margin-top:6px;padding:8px;background:#ff4d4f;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:13px}
.btn-stop:disabled{background:#d9d9d9;cursor:not-allowed}
.btn-stop:hover:not(:disabled){background:#ff7875}
.task-status-label{display:inline-block;margin:6px 0 2px;padding:3px 10px;border-radius:12px;background:#e6f4ff;color:#1677ff;font-size:12px;font-family:'Microsoft YaHei','PingFang SC',sans-serif}
.task-status-label:empty{display:none}
.report-link{display:inline-block;margin-top:6px;color:#1677ff;cursor:pointer;text-decoration:underline;font-size:12px}
.history-modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center;z-index:50}
.history-box{background:#fff;width:560px;max-width:92vw;max-height:80vh;overflow:auto;border-radius:10px;padding:16px}
.history-item{padding:10px;border-bottom:1px solid #f0f0f0;font-size:13px}
.history-item .h-id{font-weight:600}
.badge-pass{background:#52c41a;color:#fff;padding:1px 8px;border-radius:8px;font-size:11px}
.badge-check{background:#e8533f;color:#fff;padding:1px 8px;border-radius:8px;font-size:11px}
.badge-stop{background:#faad14;color:#fff;padding:1px 8px;border-radius:8px;font-size:11px}
.conn-banner{display:none;background:#ff4d4f;color:#fff;text-align:center;padding:6px;font-size:13px;position:fixed;top:0;left:0;right:0;z-index:60}
.conn-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#52c41a;margin-left:6px}
.conn-dot.off{background:#d9d9d9}
.onboard-preview{position:relative;flex:0 0 auto;width:calc(100% - 24px);min-width:0;aspect-ratio:16/9;margin:10px 12px 12px;background:#111827;border:1px solid #d9e8f5;border-radius:7px;box-shadow:0 1px 4px rgba(0,0,0,.16);overflow:hidden}
.onboard-preview img{width:100%;height:100%;object-fit:cover;display:none}
.onboard-preview-label{position:absolute;left:7px;top:6px;padding:2px 7px;border-radius:9px;background:rgba(0,0,0,.62);color:#fff;font-size:11px;z-index:2}
.onboard-preview-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:12px}
@media (max-width:900px), (orientation:portrait){
  body{height:auto;min-height:100vh;overflow:auto}
  .dashboard{grid-template-columns:1fr;height:auto;min-height:100vh}
  .panel{min-height:480px}
  #status-panel{min-height:auto}
  #status-panel .panel-body{max-height:520px}
  .onboard-preview{width:min(calc(100% - 24px),420px);align-self:center}
}
body.recording-layout{height:100vh;overflow:hidden;background:#111827}
body.recording-layout .dashboard{grid-template-columns:235px 1fr!important;grid-template-rows:170px 1fr!important;height:100vh!important;min-height:0!important;padding:6px;gap:6px}
body.recording-layout .panel{min-height:0!important;height:auto}
body.recording-layout #ai-panel{display:block;grid-column:1/-1;grid-row:1;overflow:hidden}
body.recording-layout #status-panel{grid-column:1;grid-row:2}
body.recording-layout #map-panel{grid-column:2;grid-row:2}
body.recording-layout #ai-panel .ai-config,
body.recording-layout #ai-panel .quick-cmds,
body.recording-layout #ai-panel .vision-toggle,
body.recording-layout #ai-panel .plan-builder,
body.recording-layout #ai-panel .chat-log,
body.recording-layout #ai-panel .task-status,
body.recording-layout #ai-panel .chat-input{display:none!important}
body.recording-layout #ai-panel .semantic-command{display:block;margin:7px 10px;padding:8px}
body.recording-layout #ai-panel .semantic-command-row{grid-template-columns:minmax(260px,1fr) auto}
body.recording-layout #ai-panel .trajectory-state{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
body.recording-layout #status-panel .panel-body{max-height:none;overflow:hidden;padding:8px}
body.recording-layout .status-item{padding:4px 0;font-size:12px}
body.recording-layout .onboard-preview{width:calc(100% - 16px);margin:6px 8px 8px}
</style>
</head>
<body>
<div class="conn-banner" id="conn-banner">⚠ 与服务器连接中断，请检查本机服务是否运行</div>
<div class="dashboard">
  <!-- Panel 1: Drone Status -->
  <div class="panel" id="status-panel">
    <div class="panel-header">无人机状态</div>
    <div class="panel-body" id="status-body">
      <div class="status-item"><span class="status-label">连接状态</span><span class="status-value" id="s-conn"><span class="indicator off"></span>离线</span></div>
      <div class="status-item"><span class="status-label">仿真时间</span><span class="status-value" id="s-time">--</span></div>
      <div class="status-item"><span class="status-label">位置（米）</span><span class="status-value" id="s-pos">--</span></div>
      <div class="status-item"><span class="status-label">速度（米/秒）</span><span class="status-value" id="s-vel">--</span></div>
      <div class="status-item"><span class="status-label">横滚角（度）</span><span class="status-value" id="s-roll">--</span></div>
      <div class="status-item"><span class="status-label">俯仰角（度）</span><span class="status-value" id="s-pitch">--</span></div>
      <div class="status-item"><span class="status-label">偏航角（度）</span><span class="status-value" id="s-yaw">--</span></div>
      <div class="status-item"><span class="status-label">风速（米/秒）</span><span class="status-value" id="s-wind">--</span></div>
      <div class="status-item"><span class="status-label">当前语义区域</span><span class="status-value" id="s-zone">--</span></div>
      <div class="status-item"><span class="status-label">前方距离（米）</span><span class="status-value" id="s-rf">--</span></div>
      <div class="status-item"><span class="status-label">左侧距离（米）</span><span class="status-value" id="s-rl">--</span></div>
      <div class="status-item"><span class="status-label">右侧距离（米）</span><span class="status-value" id="s-rr">--</span></div>
      <div class="status-item"><span class="status-label">下方距离（米）</span><span class="status-value" id="s-rd">--</span></div>
      <div class="status-item"><span class="status-label">碰撞次数</span><span class="status-value" id="s-col">--</span></div>
      <div class="status-item"><span class="status-label">安全包线</span><span class="status-value" id="s-env">--</span></div>
      <div class="status-item"><span class="status-label">机载相机</span><span class="status-value" id="s-cam">--</span></div>
    </div>
    <div class="onboard-preview">
      <div class="onboard-preview-label">无人机视角</div>
      <img id="onboard-preview-img" alt="无人机机载相机实时画面">
      <div class="onboard-preview-empty" id="onboard-preview-empty">等待 UE5 机载画面</div>
    </div>
  </div>

  <!-- Panel 2: Semantic Map -->
  <div class="panel" id="map-panel">
    <div class="panel-header">语义地图（PX4 局部 NED，北向↑ / 东向→）</div>
    <div class="panel-body">
      <canvas id="map-canvas"></canvas>
    </div>
  </div>

  <!-- Panel 3: Command Input -->
  <div class="panel" id="ai-panel">
    <div class="panel-header">任务指令 <span class="ai-badge" id="ai-badge">AI 检查中</span><span class="conn-dot" id="conn-dot"></span><button id="history-btn" onclick="openHistory()" style="margin-left:auto;padding:3px 10px;font-size:12px;background:#f0f0f0;border:1px solid #d9d9d9;border-radius:4px;cursor:pointer">任务历史</button></div>
    <div class="ai-config">
      <input type="password" id="deepseek-key" placeholder="在此输入 AI API 密钥" autocomplete="new-password">
      <button id="save-key-btn" onclick="saveDeepSeekKey()">连接 AI</button>
      <button id="remove-key-btn" onclick="removeKey()" style="padding:6px 10px;background:#fff;color:#e8533f;border:1px solid #e8533f;border-radius:4px;cursor:pointer;font-size:12px">移除</button>
      <span class="ai-config-status" id="ai-config-status"></span>
    </div>
    <div class="quick-cmds">
      <span class="quick-cmd" id="default-plan-btn" onclick="loadDefaultPlan()" style="display:none">＋完整巡检</span>
      <span id="quick-goals" style="display:contents"></span>
      <span class="quick-cmd" onclick="addPlanStep('hover')">＋悬停</span>
      <span class="quick-cmd" onclick="addPlanStep('land')">＋降落</span>
      <span class="quick-cmd" onclick="sendCmd('状态')">查看状态</span>
    </div>
    <div class="vision-toggle">
      <input type="checkbox" id="vision-mode">
      <label for="vision-mode">启用机载相机视觉确认</label>
    </div>
    <div class="semantic-command" id="semantic-command-panel">
      <div class="semantic-command-title">① 选择语义指令</div>
      <div class="semantic-command-row">
        <select id="semantic-command-select" onchange="loadSelectedSemanticCommand()"></select>
        <button id="generate-trajectory-btn" onclick="generateTrajectory()">② 生成 UE5 轨迹</button>
      </div>
      <div class="trajectory-state" id="trajectory-state">请选择指令并生成轨迹；轨迹出现前无人机不会起飞。</div>
    </div>
    <div class="plan-builder" id="plan-builder">
      <div class="plan-title"><span>任务队列（点击按钮添加，可排序）</span><span id="plan-count">0 步</span></div>
      <div class="plan-list" id="plan-list"><div class="plan-empty">尚未添加任务步骤</div></div>
      <div class="plan-controls">
        <button onclick="clearPlan()">清空</button>
        <button onclick="confirmPlan()">仅做安全审查</button>
        <button class="primary" id="execute-plan-btn" onclick="executePlan()" disabled>③ 沿轨迹执行</button>
      </div>
      <div class="plan-confirmed" id="plan-confirmed"></div>
      <div class="plan-review" id="plan-review"></div>
      <div class="explainability-card" id="explainability-card"></div>
    </div>
    <div class="chat-log" id="chat-log">
      <div class="chat-msg ai">无人机任务仪表盘已就绪。请输入指令，或点击上方快捷操作。</div>
    </div>
    <div class="task-status" id="task-status" style="display:none">
      <div class="stepper" id="stepper">
        <div class="step" data-stage="0">启动</div>
        <div class="step" data-stage="1">飞行</div>
        <div class="step" data-stage="2">降落</div>
        <div class="step" data-stage="3">清理</div>
      </div>
      <button class="btn-stop" id="stop-btn" onclick="stopTask()" disabled>停止任务 / 紧急停止</button>
      <div id="task-status-label" class="task-status-label">等待中</div>
      <div id="task-report" style="display:none"><span class="report-link" id="report-link">查看任务报告</span></div>
      <div class="task-log-head"><span>运行日志</span><button onclick="openLogModal()">放大日志</button></div>
      <div id="task-log"></div>
    </div>
    <div class="chat-input">
      <input type="text" id="chat-input" placeholder="例如：飞到投递区" onkeydown="if(event.key==='Enter')sendCmd()">
      <button id="send-btn" onclick="sendCmd()">发送</button>
    </div>
  </div>
</div>

<div class="log-modal" id="log-modal" onclick="if(event.target===this)closeLogModal()">
  <div class="log-modal-box">
    <div class="log-modal-head"><span>任务运行日志</span><button onclick="closeLogModal()">关闭</button></div>
    <div id="log-modal-content"></div>
  </div>
</div>

<script>
if(new URLSearchParams(window.location.search).get('recording')==='1'||window.location.hash.includes('recording=1')){
  document.body.classList.add('recording-layout');
}
let mapData = null;
let trail = [];
let currentTaskId = null;
let isRunning = false;
let connFails = 0;
let latestTaskLog = '';
let reviewToken = 0;
let planSteps = [];
let planConfirmed = false;
let trajectoryGenerated = false;
let explanationId = null;
let semanticProfiles = [];
let onboardPreviewLive = false;
const reportedIds = new Set();
let PLAN_GOAL_NAMES={start_pad:'A 起点',inspection_gate:'巡检门',delivery_zone:'B 投递区',medical_station:'医疗站'};

function loadDefaultPlan(){
  if(isRunning||!mapData||!Array.isArray(mapData.default_plan))return;
  planSteps=mapData.default_plan.map(step=>({...step}));
  invalidatePlan();renderPlan();
}

function markConn(ok){
  const banner=document.getElementById('conn-banner');
  const dot=document.getElementById('conn-dot');
  if(ok){connFails=0;if(banner)banner.style.display='none';if(dot)dot.className='conn-dot';}
  else{connFails++;if(connFails>=3){if(banner)banner.style.display='block';if(dot)dot.className='conn-dot off';}}
}
function setRunningUI(running){
  isRunning=running;
  const sb=document.getElementById('send-btn');if(sb)sb.disabled=running;
  const ci=document.getElementById('chat-input');if(ci)ci.disabled=running;
  const stopBtn=document.getElementById('stop-btn');if(stopBtn)stopBtn.disabled=!running;
  const qc=document.querySelector('.quick-cmds');if(qc){qc.style.pointerEvents=running?'none':'auto';qc.style.opacity=running?'0.6':'1';}
  const pb=document.getElementById('plan-builder');if(pb){pb.style.pointerEvents=running?'none':'auto';pb.style.opacity=running?'0.6':'1';}
  const gb=document.getElementById('generate-trajectory-btn');if(gb)gb.disabled=running;
}

function planStepLabel(step){
  if(step.action==='fly_to')return `前往 ${PLAN_GOAL_NAMES[step.goal_id]||step.goal_id}${step.vision?'（视觉确认）':''}`;
  if(step.action==='hover')return `悬停 ${step.seconds||3} 秒`;
  if(step.action==='land')return '降落并结束任务';
  return step.action;
}

function invalidatePlan(){
  reviewToken++;
  planConfirmed=false;
  trajectoryGenerated=false;
  explanationId=null;
  document.getElementById('execute-plan-btn').disabled=true;
  document.getElementById('plan-confirmed').textContent='';
  const review=document.getElementById('plan-review');
  review.style.display='none';review.textContent='';
  const state=document.getElementById('trajectory-state');
  if(state)state.textContent='任务队列已改变，请重新生成 UE5 轨迹。';
}

function loadSelectedSemanticCommand(){
  if(isRunning)return;
  const select=document.getElementById('semantic-command-select');
  const profile=semanticProfiles.find(p=>p.id===select.value);
  if(!profile)return;
  planSteps=(profile.plan||[]).map(step=>({...step}));
  invalidatePlan();renderPlan();
  addChat('system',`已选择语义指令：${profile.label}。下一步请生成 UE5 轨迹。`);
}

function generateTrajectory(){
  if(isRunning||!planSteps.some(s=>s.action==='fly_to')){
    addChat('system','请先选择包含飞行目标的语义指令。');return;
  }
  if(!planSteps.some(s=>s.action==='land'))planSteps.push({action:'land'});
  renderPlan();
  const button=document.getElementById('generate-trajectory-btn');
  const state=document.getElementById('trajectory-state');
  button.disabled=true;state.textContent='正在规划并发送轨迹到 UE5……';
  const body='plan='+encodeURIComponent(JSON.stringify({steps:planSteps}))+
    '&explanation_id='+encodeURIComponent(explanationId||'');
  fetch('/api/generate-trajectory',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
    .then(async r=>({ok:r.ok,data:await r.json()})).then(({ok,data:d})=>{
      button.disabled=false;
      if(!ok||d.error){trajectoryGenerated=false;planConfirmed=false;state.textContent='轨迹生成失败：'+(d.error||'未知错误');return;}
      trajectoryGenerated=true;planConfirmed=true;
      explanationId=d.explanation_id||explanationId;
      renderExplainability(d.explainability);
      renderPlanReview(d.review);
      document.getElementById('execute-plan-btn').disabled=false;
      document.getElementById('plan-confirmed').textContent='轨迹已锁定：\n'+planSteps.map((s,i)=>`${i+1}. ${planStepLabel(s)}`).join('\n');
      state.textContent=`UE5 轨迹已生成（${d.route_point_count} 个轨迹点）。请先观察场景中的紫青色航线，再点击“沿轨迹执行”。`;
      addChat('system',`UE5 轨迹已生成，共 ${d.route_point_count} 个轨迹点；无人机仍保持停止。`);
    }).catch(()=>{
      button.disabled=false;trajectoryGenerated=false;planConfirmed=false;
      state.textContent='轨迹生成请求失败，请检查 8082 服务。';markConn(false);
    });
}

function addPlanStep(action,goal){
  if(isRunning)return;
  if(planSteps.some(s=>s.action==='land')){
    addChat('system','降落已是最后一步；请先删除降落步骤再继续添加。');
    return;
  }
  if(action==='fly_to'){
    const vision=document.getElementById('vision-mode').checked;
    planSteps.push({action:'fly_to',goal_id:goal,vision});
  }else if(action==='hover'){
    planSteps.push({action:'hover',seconds:3});
  }else if(action==='land'){
    planSteps.push({action:'land'});
  }
  invalidatePlan();renderPlan();
}

function movePlanStep(index,delta){
  const next=index+delta;
  if(next<0||next>=planSteps.length)return;
  const item=planSteps.splice(index,1)[0];
  planSteps.splice(next,0,item);
  invalidatePlan();renderPlan();
}

function removePlanStep(index){
  planSteps.splice(index,1);
  invalidatePlan();renderPlan();
}

function clearPlan(){
  if(isRunning)return;
  planSteps=[];invalidatePlan();renderPlan();
}

function renderPlan(){
  const list=document.getElementById('plan-list');
  document.getElementById('plan-count').textContent=planSteps.length+' 步';
  if(!planSteps.length){
    list.innerHTML='<div class="plan-empty">尚未添加任务步骤</div>';return;
  }
  list.innerHTML=planSteps.map((step,index)=>`
    <div class="plan-step">
      <span>${index+1}</span>
      <span>${planStepLabel(step)}</span>
      <span class="plan-actions">
        <button onclick="movePlanStep(${index},-1)" ${index===0?'disabled':''}>↑</button>
        <button onclick="movePlanStep(${index},1)" ${index===planSteps.length-1?'disabled':''}>↓</button>
        <button onclick="removePlanStep(${index})">×</button>
      </span>
    </div>`).join('');
}

function renderPlanReview(review){
  const el=document.getElementById('plan-review');
  const riskClass=review.risk==='高'?'risk-high':(review.risk==='中'?'risk-medium':'');
  el.className='plan-review '+riskClass;
  const lines=[
    `飞前审查：${review.risk}风险｜${review.fly_leg_count} 个航段｜约 ${review.route_distance_m} 米｜预计 ${review.estimated_seconds} 秒`,
    `视觉确认 ${review.vision_leg_count} 段｜返航 ${review.returns_home?'是':'否'}｜最终降落 ${review.lands_last?'是':'否'}`,
  ];
  if(review.rerouted_leg_count){
    lines.push(`自动绕行 ${review.rerouted_leg_count} 段｜地图安全余量 ${review.planned_clearance_m} 米`);
    (review.rerouted_legs||[]).forEach(r=>{
      const pts=(r.waypoints_m||[]).map(p=>`(${p[0]}, ${p[1]})`).join(' → ');
      lines.push(`↪ 第${r.step}航段 ${r.from}→${r.to}：${pts}`);
    });
  }
  if(review.blocked)lines.push('⛔ 禁止执行：'+review.block_reason);
  (review.warnings||[]).forEach(w=>lines.push('⚠ '+w));
  if(review.ai){
    lines.push('DeepSeek：'+review.ai.summary);
    (review.ai.warnings||[]).forEach(w=>lines.push('AI 提示：'+w));
    lines.push('建议：'+review.ai.recommendation);
  }else if(review.ai_fallback_reason){
    lines.push('AI 复核不可用，已采用本地安全审查：'+review.ai_fallback_reason);
  }
  el.textContent=lines.join('\n');
  el.style.display='block';
}

function renderExplainability(info){
  if(!info)return;
  explanationId=info.explanation_id||explanationId;
  const el=document.getElementById('explainability-card');
  const short=v=>v?String(v).slice(0,12):'-';
  const lines=[
    `解释链：${info.status||'-'}｜解析 ${info.parser||'-'}${info.model?' / '+info.model:''}`,
    `地图 ${info.map_id||'-'} @ ${short(info.map_sha256)}｜计划 ${short(info.plan_sha256)}`,
    `风险 ${info.risk||'-'}｜航程 ${info.route_distance_m??'-'} m｜轨迹点 ${info.route_point_count??'-'}`,
  ];
  if(info.task_id)lines.push(`任务 ${info.task_id}｜结果 ${info.outcome||'执行中'}`);
  el.textContent=lines.join('\n')+'\n';
  const link=document.createElement('a');
  link.textContent='查看完整解释记录 JSON';
  link.onclick=()=>window.open('/api/explainability/'+encodeURIComponent(explanationId),'_blank');
  el.appendChild(link);el.style.display='block';
}

function refreshExplainability(id){
  if(!id)return;
  fetch('/api/explainability/'+encodeURIComponent(id))
    .then(r=>r.ok?r.json():null).then(record=>{
      if(!record)return;
      renderExplainability({
        explanation_id:record.explanation_id,status:record.status,
        parser:record.parser&&record.parser.name,
        model:record.parser&&record.parser.model,
        map_id:record.semantic_map&&record.semantic_map.map_id,
        map_sha256:record.semantic_map&&record.semantic_map.sha256,
        plan_sha256:record.plan_sha256,
        risk:record.safety_review&&record.safety_review.risk,
        route_distance_m:record.safety_review&&record.safety_review.route_distance_m,
        route_point_count:record.trajectory&&record.trajectory.route_point_count,
        task_id:record.execution&&record.execution.task_id,
        outcome:record.execution&&record.execution.outcome,
      });
    });
}

function confirmPlan(){
  if(!planSteps.some(s=>s.action==='fly_to')){
    addChat('system','任务队列至少需要一个飞行目标。');return;
  }
  const landIndex=planSteps.findIndex(s=>s.action==='land');
  if(landIndex>=0&&landIndex!==planSteps.length-1){
    addChat('system','降落必须是最后一步。');return;
  }
  if(landIndex<0)planSteps.push({action:'land'});
  planConfirmed=false;renderPlan();
  document.getElementById('execute-plan-btn').disabled=true;
  document.getElementById('plan-confirmed').textContent='正在进行飞前安全审查……';
  const token=++reviewToken;
  const body='plan='+encodeURIComponent(JSON.stringify({steps:planSteps}))+
    '&explanation_id='+encodeURIComponent(explanationId||'');
  fetch('/api/review-plan',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
    .then(async r=>({ok:r.ok,data:await r.json()})).then(({ok,data:d})=>{
      if(token!==reviewToken)return;
      if(!ok||d.error){
        document.getElementById('plan-confirmed').textContent='飞前审查失败，请重试：'+(d.error||'未知错误');
        return;
      }
      renderPlanReview(d);
      renderExplainability(d.explainability);
      if(d.blocked){
        planConfirmed=false;
        document.getElementById('execute-plan-btn').disabled=true;
        document.getElementById('plan-confirmed').textContent='飞前审查未通过，请调整任务队列。';
        addChat('system','飞前审查阻止执行：'+d.block_reason);
      }else{
        planConfirmed=true;
        document.getElementById('execute-plan-btn').disabled=!trajectoryGenerated;
        document.getElementById('plan-confirmed').textContent='已确认：\n'+planSteps.map((s,i)=>`${i+1}. ${planStepLabel(s)}`).join('\n');
        addChat('system',`飞前审查完成：${d.risk}风险，约 ${d.route_distance_m} 米，预计 ${d.estimated_seconds} 秒。仍需生成 UE5 轨迹后才能执行。`);
      }
    }).catch(()=>{
      if(token!==reviewToken)return;
      document.getElementById('plan-confirmed').textContent='飞前审查请求失败，请检查连接后重试。';
      markConn(false);
    });
}

function executePlan(){
  if(!planConfirmed||!trajectoryGenerated||isRunning){
    addChat('system','请先生成并确认 UE5 轨迹，再执行飞行。');return;
  }
  const body='plan='+encodeURIComponent(JSON.stringify({steps:planSteps}))+
    '&explanation_id='+encodeURIComponent(explanationId||'');
  addChat('system','正在提交已确认的同会话任务队列……');
  fetch('/api/execute-plan',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
    .then(async r=>({ok:r.ok,data:await r.json()})).then(({ok,data:d})=>{
      if(!ok||d.error){addChat('system',d.error||'任务队列提交失败');return;}
      explanationId=d.explanation_id||explanationId;
      renderExplainability(d.explainability);
      currentTaskId=d.task_id;setRunningUI(true);updateStepper(0,'running');
      document.getElementById('task-status').style.display='block';
      addChat('system',`任务 ${d.task_id} 已加入队列，共 ${d.plan.length} 步；将在同一 UE/PX4 会话中执行。`);
      pollTask(d.task_id);
    }).catch(()=>{addChat('system','请求失败：与服务器连接中断');markConn(false);});
}
function updateStepper(stageIndex,status){
  const steps=document.querySelectorAll('#stepper .step');
  steps.forEach(s=>{
    const idx=parseInt(s.getAttribute('data-stage'),10);
    s.className='step';
    if(status==='done'||status==='stopped'||status==='error'){
      if(idx<=stageIndex)s.classList.add('done');
    }else if(idx<stageIndex){s.classList.add('done');}
    else if(idx===stageIndex){s.classList.add('active');}
  });
}

fetch('/api/map').then(r=>r.json()).then(d=>{
  mapData=d;
  PLAN_GOAL_NAMES={};
  Object.entries(d.goals||{}).forEach(([id,g])=>{
    PLAN_GOAL_NAMES[id]=g.display_label||g.label||id;
  });
  Object.assign(ZONE_NAMES,PLAN_GOAL_NAMES);
  const goalBox=document.getElementById('quick-goals');
  if(goalBox){
    goalBox.replaceChildren();
    Object.entries(d.goals||{}).forEach(([id,g])=>{
      const button=document.createElement('span');
      button.className='quick-cmd';
      button.textContent='＋'+(g.display_label||g.label||id);
      button.onclick=()=>addPlanStep('fly_to',id);
      goalBox.appendChild(button);
    });
  }
  const defaultButton=document.getElementById('default-plan-btn');
  if(defaultButton&&Array.isArray(d.default_plan))defaultButton.style.display='inline-block';
  semanticProfiles=Array.isArray(d.mission_profiles)&&d.mission_profiles.length
    ? d.mission_profiles
    : [{id:'default_inspection',label:'完整语义巡检',description:'按地图默认航线巡检',plan:d.default_plan||[]}];
  const commandSelect=document.getElementById('semantic-command-select');
  commandSelect.replaceChildren();
  semanticProfiles.forEach(profile=>{
    const option=document.createElement('option');
    option.value=profile.id;option.textContent=profile.label;
    commandSelect.appendChild(option);
  });
  if(semanticProfiles.length)loadSelectedSemanticCommand();
  drawMap(null);
});
fetch('/api/ai-status').then(r=>r.json()).then(d=>{
  updateAiStatus(d);
});

function updateAiStatus(d){
  const badge=document.getElementById('ai-badge');
  badge.textContent=d.enabled?'AI 已连接 · '+d.model:'规则模式';
  badge.className='ai-badge'+(d.enabled?' on':'');
  document.getElementById('ai-config-status').textContent=d.enabled?'已安全保存':'尚未连接';
}

function saveDeepSeekKey(){
  const input=document.getElementById('deepseek-key');
  const button=document.getElementById('save-key-btn');
  const status=document.getElementById('ai-config-status');
  const key=input.value.trim();
  if(!key){status.textContent='请先输入密钥';return;}
  button.disabled=true;status.textContent='正在验证……';
  const body='api_key='+encodeURIComponent(key);
  input.value='';
  fetch('/api/config/deepseek',{
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body
  }).then(async r=>({ok:r.ok,data:await r.json()})).then(({ok,data})=>{
    if(!ok){status.textContent=data.error||'连接失败';return;}
    updateAiStatus(data);
    status.textContent='连接成功';
  }).catch(()=>{status.textContent='本机服务请求失败';markConn(false);})
    .finally(()=>{button.disabled=false;});
}

setInterval(()=>{
  fetch('/api/telemetry').then(r=>r.json()).then(t=>{
    markConn(true);
    if(!t||!t.time_s)return;
    updateStatus(t);
    if(t.position_x_m!==undefined){
      trail.push({x:t.position_x_m,y:t.position_y_m});
      if(trail.length>200)trail.shift();
    }
    drawMap(t);
  }).catch(()=>{markConn(false);});
  if(currentTaskId){ pollTask(currentTaskId); }
},200);

function syncTrajectoryPreview(){
  fetch('/api/trajectory-preview').then(r=>r.json()).then(d=>{
    if(!d||!d.generated_wall||!Array.isArray(d.plan))return;
    const samePlan=JSON.stringify(d.plan)===JSON.stringify(planSteps);
    if(!samePlan)return;
    trajectoryGenerated=true;planConfirmed=true;
    const executeButton=document.getElementById('execute-plan-btn');
    if(executeButton&&!isRunning)executeButton.disabled=false;
    const state=document.getElementById('trajectory-state');
    if(state)state.textContent=`UE5 轨迹已生成（${d.route_point_count||0} 个轨迹点）；等待执行或正在沿轨迹飞行。`;
  }).catch(()=>{});
}
setInterval(syncTrajectoryPreview,1000);

function refreshOnboardPreview(){
  if(refreshOnboardPreview.loading)return;
  refreshOnboardPreview.loading=true;
  const img=document.getElementById('onboard-preview-img');
  const empty=document.getElementById('onboard-preview-empty');
  const probe=new Image();
  probe.onload=()=>{
    refreshOnboardPreview.loading=false;
    onboardPreviewLive=true;
    img.src=probe.src;
    img.style.display='block';
    empty.style.display='none';
  };
  probe.onerror=()=>{
    refreshOnboardPreview.loading=false;
    onboardPreviewLive=false;
    img.style.display='none';
    empty.style.display='flex';
  };
  probe.src='/api/camera/latest';
}
setInterval(refreshOnboardPreview,__CAMERA_PREVIEW_POLL_MS__);
refreshOnboardPreview();

function fmt(v,d=1){return v!==undefined?v.toFixed(d):'--';}

function mapLocalXY(x,y){
  const origin=(mapData&&Array.isArray(mapData.origin_world_m))?mapData.origin_world_m:[0,0,0];
  return [(x||0)-(origin[0]||0),(y||0)-(origin[1]||0)];
}

// Display-only coordinate helpers. Never feed these values back to UE, PX4,
// the route planner, mission commands, or recorded telemetry.
function mapLocalToDisplayXY(x,y){
  // PX4 local NED: X is North, Y is East. The dashboard is north-up like
  // QGC, therefore screen/display X is East and display Y is North.
  return [y||0,x||0];
}

function mapDisplayVectorXY(x,y){
  return [y||0,x||0];
}

function mapDisplayYawDeg(yaw){
  return yaw||0;
}

function mapDisplaySensorBearingDeg(yaw,bodyAngle){
  return (yaw||0)+(bodyAngle||0);
}

function mapDisplayAngleToCanvasRad(displayAngle){
  // Canvas vehicle glyph points to +screen-X at zero rotation; QGC/NED yaw
  // zero points North (screen up), so subtract 90 degrees.
  return ((displayAngle||0)-90)*Math.PI/180;
}

function mapDisplayXY(x,y){
  const [localX,localY]=mapLocalXY(x,y);
  return mapLocalToDisplayXY(localX,localY);
}

function updateStatus(t){
  document.getElementById('s-conn').innerHTML='<span class="indicator on"></span>在线';
  document.getElementById('s-time').textContent=fmt(t.time_s,1)+'s';
  const [displayX,displayY]=mapDisplayXY(t.position_x_m,t.position_y_m);
  const [displayVx,displayVy]=mapDisplayVectorXY(t.velocity_x_mps,t.velocity_y_mps);
  document.getElementById('s-pos').textContent=`(${fmt(displayX)},${fmt(displayY)},${fmt(t.position_z_m)})`;
  document.getElementById('s-vel').textContent=`(${fmt(displayVx)},${fmt(displayVy)},${fmt(t.velocity_z_mps)})`;
  document.getElementById('s-roll').textContent=fmt(t.roll_deg);
  document.getElementById('s-pitch').textContent=fmt(t.pitch_deg);
  document.getElementById('s-yaw').textContent=fmt(mapDisplayYawDeg(t.yaw_deg));
  document.getElementById('s-wind').textContent=fmt(t.wind_speed_mps);
  document.getElementById('s-zone').textContent=ZONE_NAMES[t.semantic_zone]||t.semantic_zone||'--';
  document.getElementById('s-rf').textContent=fmt(t.range_front_m);
  document.getElementById('s-rl').textContent=fmt(t.range_left_m);
  document.getElementById('s-rr').textContent=fmt(t.range_right_m);
  document.getElementById('s-rd').textContent=fmt(t.range_down_m);
  const col=t.obstacle_collision_count||0;
  const colEl=document.getElementById('s-col');
  colEl.textContent=col;colEl.className='status-value'+(col>0?' warn':' ok');
  const env=t.envelope_exceeded;
  const envEl=document.getElementById('s-env');
  envEl.textContent=env?'超出':'正常';envEl.className='status-value'+(env?' warn':' ok');
  const cameraLive=Boolean(t.onboard_camera_active||onboardPreviewLive);
  document.getElementById('s-cam').innerHTML='<span class="indicator '+(cameraLive?'on':'off')+'"></span>'+(cameraLive?'已启用':'未启用');
}

const canvas=document.getElementById('map-canvas');
const ctx=canvas.getContext('2d');
function resizeCanvas(){
  const r=canvas.parentElement.getBoundingClientRect();
  canvas.width=r.width;canvas.height=r.height;
}
window.addEventListener('resize',resizeCanvas);resizeCanvas();
const COLORS={start_pad:'#f5c518',inspection_gate:'#3b82f6',delivery_zone:'#22c55e',medical_station:'#ef4444',bridge_entry:'#22c55e',bridge_ascent:'#13c2c2',bridge_apex:'#ef4444',bridge_descent:'#fa8c16',bridge_exit:'#722ed1',far_pad:'#2f54eb'};
const ZONE_NAMES={start_pad:'A 起点',inspection_gate:'巡检门',delivery_zone:'B 投递区',medical_station:'医疗站',transit:'航路区'};

function drawMap(t){
  resizeCanvas();
  const w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);ctx.fillStyle='#fafafa';ctx.fillRect(0,0,w,h);
  const points=[];
  if(mapData&&mapData.goals)Object.values(mapData.goals).forEach(g=>points.push(mapLocalToDisplayXY(g.position_m[0],g.position_m[1])));
  if(mapData&&mapData.inspection_zones)mapData.inspection_zones.forEach(z=>points.push(mapLocalToDisplayXY(z.position_m[0],z.position_m[1])));
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const xMin=xs.length?Math.min(...xs)-4:-5,xMax=xs.length?Math.max(...xs)+4:25;
  const yMin=ys.length?Math.min(...ys)-4:-8,yMax=ys.length?Math.max(...ys)+4:8;
  const scaleX=w/(xMax-xMin),scaleY=h/(yMax-yMin);
  const scale=Math.min(scaleX,scaleY)*0.9;
  const ox=w/2-(xMin+xMax)/2*scale;
  const oy=h/2+(yMin+yMax)/2*scale;
  // UE/PX4 keep internal NED coordinates; w2s rotates them for a QGC-style
  // north-up view without feeding display coordinates back into control.
  function w2s(x,y){const [east,north]=mapLocalToDisplayXY(x,y);return [ox+east*scale,oy-north*scale];}
  function d2s(x,y){return [ox+x*scale,oy-y*scale];}
  ctx.strokeStyle='#e8e8e8';ctx.lineWidth=1;ctx.font='10px monospace';ctx.fillStyle='#bbb';
  for(let gx=Math.ceil(xMin/5)*5;gx<=xMax;gx+=5){let [sx,_]=w2s(gx,0);ctx.beginPath();ctx.moveTo(sx,0);ctx.lineTo(sx,h);ctx.stroke();ctx.fillText(gx+'m',sx+2,h-12);}
  for(let gy=Math.ceil(yMin/5)*5;gy<=yMax;gy+=5){let [_,sy]=d2s(0,gy);ctx.beginPath();ctx.moveTo(0,sy);ctx.lineTo(w,sy);ctx.stroke();ctx.fillText(gy+'m',4,sy-2);}
  if(mapData&&mapData.obstacles){
    mapData.obstacles.forEach(ob=>{
      let [cx,cy]=ob.center_m,[sw,sh]=ob.size_m;
      let [sx,sy]=w2s(cx,cy);
      ctx.fillStyle='rgba(120,120,120,0.3)';ctx.strokeStyle='#999';ctx.lineWidth=1;
      ctx.fillRect(sx-sw*scale/2,sy-sh*scale/2,sw*scale,sh*scale);
      ctx.strokeRect(sx-sw*scale/2,sy-sh*scale/2,sw*scale,sh*scale);
    });
  }
  if(mapData&&mapData.bridge){
    const bridge=mapData.bridge;
    const deckStart=Number(bridge.deck_start_m||0);
    const deckEnd=Number(bridge.deck_end_m||bridge.deck_length_m||0);
    const halfWidth=Number(bridge.deck_width_m||8)/2;
    const bridgeCorners=[w2s(deckStart,-halfWidth),w2s(deckStart,halfWidth),w2s(deckEnd,-halfWidth),w2s(deckEnd,halfWidth)];
    const bridgeXs=bridgeCorners.map(p=>p[0]),bridgeYs=bridgeCorners.map(p=>p[1]);
    const left=Math.min(...bridgeXs),right=Math.max(...bridgeXs),top=Math.min(...bridgeYs),bottom=Math.max(...bridgeYs);
    ctx.save();
    ctx.fillStyle='rgba(71,85,105,0.13)';
    ctx.strokeStyle='#334155';ctx.lineWidth=4;
    ctx.fillRect(left,top,right-left,bottom-top);
    ctx.strokeRect(left,top,right-left,bottom-top);
    ctx.setLineDash([8,5]);ctx.strokeStyle='#94a3b8';ctx.lineWidth=1.5;
    const [,spanTop]=w2s(deckStart+(deckEnd-deckStart-(bridge.structural_span_m||0))/2,0);
    const [,spanBottom]=w2s(deckEnd-(deckEnd-deckStart-(bridge.structural_span_m||0))/2,0);
    ctx.beginPath();ctx.moveTo(left,spanTop);ctx.lineTo(right,spanTop);ctx.moveTo(left,spanBottom);ctx.lineTo(right,spanBottom);ctx.stroke();
    ctx.restore();
    ctx.fillStyle='#334155';ctx.font='600 11px sans-serif';ctx.textAlign='center';
    ctx.fillText(`桥面边界 ${Number(bridge.deck_length_m||deckEnd-deckStart).toFixed(0)}m · N↑ E→`,(left+right)/2,top+14);
  }
  if(mapData&&mapData.inspection_zones){
    const zoneColors={road_crack:'#111827',pest:'#d97706',plant:'#16a34a',bird:'#7c3aed'};
    mapData.inspection_zones.forEach(zone=>{
      const [sx,sy]=w2s(zone.position_m[0],zone.position_m[1]);
      const radius=(zone.radius_m||0.8)*scale;
      ctx.save();ctx.setLineDash([4,3]);ctx.lineWidth=2;
      ctx.strokeStyle=zoneColors[zone.class]||'#64748b';
      ctx.fillStyle=(zoneColors[zone.class]||'#64748b')+'18';
      ctx.beginPath();ctx.arc(sx,sy,radius,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.restore();
      ctx.fillStyle=zoneColors[zone.class]||'#64748b';ctx.font='10px sans-serif';ctx.textAlign='center';
      ctx.fillText(zone.label||zone.class,sx,sy-radius-4);
    });
  }
  if(trail.length>1){
    ctx.strokeStyle='#1890ff';ctx.lineWidth=2;ctx.beginPath();
    trail.forEach((p,i)=>{
      let [localX,localY]=mapLocalXY(p.x,p.y);
      let [sx,sy]=w2s(localX,localY);
      if(i===0)ctx.moveTo(sx,sy);else ctx.lineTo(sx,sy);
    });
    ctx.stroke();
  }
  if(mapData&&mapData.goals){
    Object.entries(mapData.goals).forEach(([id,g])=>{
      let [sx,sy]=w2s(g.position_m[0],g.position_m[1]);
      let color=COLORS[id]||'#888';
      ctx.fillStyle=color;ctx.strokeStyle='#333';ctx.lineWidth=1;
      ctx.beginPath();ctx.arc(sx,sy,10,0,Math.PI*2);ctx.fill();ctx.stroke();
      ctx.fillStyle='#333';ctx.font='11px sans-serif';ctx.textAlign='center';
      ctx.fillText(ZONE_NAMES[id]||g.label,sx,sy-15);
      ctx.font='9px monospace';ctx.fillStyle='#888';
      const [goalDisplayX,goalDisplayY]=mapLocalToDisplayXY(g.position_m[0],g.position_m[1]);
      ctx.fillText(`(${goalDisplayX},${goalDisplayY})`,sx,sy+22);
    });
  }
  if(t&&t.position_x_m!==undefined){
    let [localX,localY]=mapLocalXY(t.position_x_m,t.position_y_m);
    let [dx,dy]=w2s(localX,localY);
    let displayYaw=mapDisplayYawDeg(t.yaw_deg);
    let canvasYaw=mapDisplayAngleToCanvasRad(displayYaw);
    ctx.save();ctx.translate(dx,dy);ctx.rotate(canvasYaw);
    ctx.fillStyle='#1890ff';ctx.strokeStyle='#fff';ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(12,0);ctx.lineTo(-8,-7);ctx.lineTo(-4,0);ctx.lineTo(-8,7);ctx.closePath();ctx.fill();ctx.stroke();
    ctx.restore();
    const ranges=[
      {r:t.range_front_m||0,bodyAngle:0,color:'rgba(33,150,243,0.15)'},
      {r:t.range_left_m||0,bodyAngle:-90,color:'rgba(33,150,243,0.1)'},
      {r:t.range_right_m||0,bodyAngle:90,color:'rgba(33,150,243,0.1)'},
    ];
    ranges.forEach(s=>{
      if(s.r>0&&s.r<15){
        let rad=s.r*scale;
        let displayBearing=mapDisplaySensorBearingDeg(t.yaw_deg,s.bodyAngle);
        let aRad=mapDisplayAngleToCanvasRad(displayBearing);
        ctx.fillStyle=s.color;ctx.beginPath();ctx.moveTo(dx,dy);ctx.arc(dx,dy,rad,aRad-0.4,aRad+0.4);ctx.closePath();ctx.fill();
      }
    });
    ctx.fillStyle='#333';ctx.font='10px monospace';ctx.textAlign='left';
    ctx.fillText(`Z=${fmt(t.position_z_m,1)}m`,dx+14,dy-4);
    ctx.fillText(`V=${fmt(Math.hypot(t.velocity_x_mps||0,t.velocity_y_mps||0,t.velocity_z_mps||0),1)}m/s`,dx+14,dy+8);
  }
  ctx.fillStyle='#8c8c8c';ctx.font='11px sans-serif';
  ctx.textAlign='left';ctx.fillText('北（X+）↑',12,18);
  ctx.textAlign='right';ctx.fillText('东（Y+）→',w-12,h-12);
}

function sendCmd(text){
  const input=document.getElementById('chat-input');
  const cmd=text||input.value;
  if(!cmd.trim())return;
  input.value='';
  addChat('user',cmd);
  addChat('ai','正在解析指令……');
  const vision=document.getElementById('vision-mode').checked;
  const body='text='+encodeURIComponent(cmd)+'&vision='+(vision?'1':'0');
  fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})
    .then(r=>r.json()).then(d=>{
      const log=document.getElementById('chat-log');
      log.removeChild(log.lastChild);
      addChat('ai',d.reply);
      addChat('system',d.parser==='deepseek'?'由 DeepSeek AI 理解':'由本地规则理解');
      explanationId=d.explanation_id||null;
      renderExplainability(d.explainability);
      if(Array.isArray(d.plan)&&d.plan.length){
        const replaced=planSteps.length>0;
        planSteps=d.plan.map(step=>({...step}));
        invalidatePlan();renderPlan();
        addChat('system',replaced
          ? `已用 AI 新计划替换原队列，共 ${d.plan.length} 步，请重新确认顺序后执行。`
          : `已将自然语言解析出的 ${d.plan.length} 个步骤加入任务队列，请确认顺序后执行。`);
      }else if(d.action==='fly_to'&&d.goal_id){
        if(planSteps.some(s=>s.action==='land')){
          addChat('system','当前队列已包含降落，请先删除降落步骤。');
        }else{
          planSteps.push({action:'fly_to',goal_id:d.goal_id,vision:!!d.vision});
          invalidatePlan();renderPlan();
          addChat('system',`已加入任务队列：${planStepLabel(planSteps[planSteps.length-1])}`);
        }
      }else if(d.action==='hover'||d.action==='land'){
        addPlanStep(d.action);
      }else if(d.action==='status'){
        addChat('ai',d.result||'');
      }else if(d.error){
        addChat('system','错误：'+d.error);
      }else if(d.warning){
        addChat('system',d.warning);
      }
    });
}

function doExecute(action,goal,vision){
  addChat('system','正在下发控制器任务……');
  fetch('/api/execute',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`action=${action}&goal=${goal||''}&vision=${vision?'1':'0'}`})
    .then(r=>r.json()).then(d=>{
      if(d.error){addChat('system',d.error);return;}
      currentTaskId=d.task_id;
      setRunningUI(true);
      updateStepper(0,'running');
      document.getElementById('task-status').style.display='block';
      addChat('system',d.message||`任务 ${d.task_id} 已加入队列（视觉确认：${d.vision?'是':'否'}）`);
      pollTask(d.task_id);
    }).catch(()=>{addChat('system','请求失败：与服务器连接中断');markConn(false);});
}

function pollTask(id){
  fetch('/api/task/'+id).then(r=>r.json()).then(d=>{
    markConn(true);
    if(!d){
      setRunningUI(false);
      if(currentTaskId===id){currentTaskId=null;addChat('system','进行中的任务已丢失（服务可能已重启），请重新执行');}
      return;
    }
    document.getElementById('task-status').style.display='block';
    const statusNames={queued:'排队中',running:'运行中',done:'已完成',error:'错误',stopping:'正在停止',stopped:'已停止'};
    // Derive a display label from the worker's terminal status, but show
    // "正在停止" while a stop is in flight (stop_requested flag) since the
    // backend no longer writes a non-terminal "stopping" status into the dict.
    let dispLabel = statusNames[d.status] || d.status || '等待中';
    if (d.stop_requested && d.status !== 'stopped' && d.status !== 'done' && d.status !== 'error') {
      dispLabel = '正在停止';
    }
    const lblEl = document.getElementById('task-status-label');
    if (lblEl) lblEl.textContent = dispLabel;
    updateStepper(d.stage_index||0, d.status);
    const logEl=document.getElementById('task-log');
    const tail=(d.log||[]).slice(-40).join('\n');
    latestTaskLog=(d.log||[]).join('\n');
    logEl.textContent=tail; logEl.scrollTop=logEl.scrollHeight;
    const modalLog=document.getElementById('log-modal-content');
    if(modalLog&&document.getElementById('log-modal').style.display==='flex'){
      modalLog.textContent=latestTaskLog;modalLog.scrollTop=modalLog.scrollHeight;
    }
    const rep=document.getElementById('task-report');
    const repLink=document.getElementById('report-link');
    if((d.report_path||d.summary) && (d.status==='done'||d.status==='stopped'||d.status==='error')){
      rep.style.display='block';
      repLink.onclick=()=>window.open('/api/task/'+id+'/report','_blank');
    } else { rep.style.display='none'; }
    if(d.explanation_id){
      explanationId=d.explanation_id;
      if(d.status==='done'||d.status==='stopped'||d.status==='error'){
        refreshExplainability(d.explanation_id);
      }
    }
    if(d.status==='running'||d.status==='queued'||d.status==='stopping'){
      setRunningUI(true);
    } else {
      setRunningUI(false);
      if(!reportedIds.has(id)){
        reportedIds.add(id);
        const outcome=d.outcome||d.error||statusNames[d.status]||d.status;
        addChat('system',`任务 ${id} 结束：${outcome}`);
        loadHistory();
      }
      if(currentTaskId===id)currentTaskId=null;
    }
  }).catch(()=>{markConn(false);});
}

function openLogModal(){
  const modal=document.getElementById('log-modal');
  const content=document.getElementById('log-modal-content');
  content.textContent=latestTaskLog||'当前还没有任务日志。';
  modal.style.display='flex';
  content.scrollTop=content.scrollHeight;
}

function closeLogModal(){
  document.getElementById('log-modal').style.display='none';
}

function addChat(type,msg){
  const log=document.getElementById('chat-log');
  const div=document.createElement('div');
  div.className='chat-msg '+type;
  div.textContent=msg;
  log.appendChild(div);
  log.scrollTop=log.scrollHeight;
}

function stopTask(){
  if(!currentTaskId)return;
  const id=currentTaskId;
  addChat('system','正在发送停止信号……');
  fetch('/api/task/'+id+'/stop',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error){addChat('system','停止失败：'+d.error);}
    else{addChat('system',d.message||'已发送停止信号，正在清理……');}
  }).catch(()=>{addChat('system','停止请求失败：与服务器连接中断');markConn(false);});
}

function loadHistory(){
  fetch('/api/history').then(r=>r.json()).then(list=>{
    markConn(true);
    const el=document.getElementById('history-list');
    if(!list||!list.length){el.innerHTML='<div class="history-item">暂无历史任务</div>';return;}
    el.innerHTML=list.map(h=>{
      const oc=h.outcome||'-';
      const cls=oc==='PASS'?'badge-pass':(oc==='STOPPED'?'badge-stop':'badge-check');
      const rp=h.report_path?'<span class="report-link" onclick="window.open(\'/api/task/'+h.task_id+'/report\',\'_blank\')">报告</span>':'';
      const xp=h.explanation_id?'<span class="report-link" onclick="window.open(\'/api/explainability/'+h.explanation_id+'\',\'_blank\')">解释链</span>':'';
      const goal=(h.goal_id||(h.action||''));
      const t=h.finished?new Date(h.finished*1000).toLocaleString('zh-CN'):'';
      return '<div class="history-item"><span class="h-id">'+h.task_id+'</span> <span class="'+cls+'">'+oc+'</span> ｜ 目标:'+goal+' ｜ 视觉:'+(h.vision?'是':'否')+' ｜ '+t+' '+rp+' '+xp+'</div>';
    }).join('');
  }).catch(()=>{markConn(false);});
}
function openHistory(){document.getElementById('history-modal').style.display='flex';loadHistory();}
function closeHistory(){document.getElementById('history-modal').style.display='none';}

function removeKey(){
  if(!confirm('确定移除 DeepSeek 密钥？移除后将仅使用本地规则解析指令。'))return;
  fetch('/api/config/deepseek/remove',{method:'POST'}).then(r=>r.json()).then(d=>{
    updateAiStatus(d);
    document.getElementById('ai-config-status').textContent='密钥已移除';
  }).catch(()=>{document.getElementById('ai-config-status').textContent='移除失败：连接中断';markConn(false);});
}

function restoreCurrent(){
  fetch('/api/current').then(r=>r.json()).then(d=>{
    markConn(true);
    if(d&&d.task_id){
      currentTaskId=d.task_id;
      document.getElementById('task-status').style.display='block';
      addChat('system','检测到进行中的任务 '+d.task_id+'，已恢复进度显示');
      pollTask(d.task_id);
    }
  }).catch(()=>{markConn(false);});
}

// 初始化：加载历史并恢复进行中的任务
loadHistory();
restoreCurrent();
</script>
<div class="history-modal" id="history-modal">
  <div class="history-box">
    <div style="display:flex;align-items:center;margin-bottom:8px"><h3 style="margin:0;font-size:15px">任务历史</h3><button onclick="closeHistory()" style="margin-left:auto;padding:4px 10px;cursor:pointer">关闭</button></div>
    <div id="history-list"></div>
  </div>
</div>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress request logging

    def _local_origin_allowed(self):
        origin = self.headers.get("Origin", "")
        return (not origin) or origin in {
            "http://127.0.0.1:%d" % SERVER_PORT,
            "http://localhost:%d" % SERVER_PORT,
            "http://[::1]:%d" % SERVER_PORT,
        }

    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            dashboard_html = DASHBOARD_HTML.replace(
                "__CAMERA_PREVIEW_POLL_MS__",
                str(CAMERA_PREVIEW_POLL_MS)).replace(
                    "__DASHBOARD_TITLE__",
                    str(DASHBOARD_CONFIG.get("title", "IFSC 无人机巡检任务仪表盘")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(dashboard_html.encode("utf-8"))
        elif self.path == "/api/telemetry":
            with telemetry_lock:
                data = json.dumps(latest_telemetry)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
        elif self.path.startswith("/api/camera/latest"):
            latest = None
            latest_mtime = 0.0
            # UE writes the active timestamp directory to current_run.txt.
            # Reading eight ring-buffer files is constant time; recursively
            # walking thousands of legacy PNGs once per browser poll caused a
            # request pile-up and left the preview permanently blank.
            pointer = os.path.join(
                CAMERA_FRAMES_WINDOWS_DIR, "current_run.txt")
            try:
                with open(pointer, "r", encoding="utf-8") as pointer_file:
                    run_id = pointer_file.read().strip()
            except OSError:
                run_id = ""
            if (
                len(run_id) == 15 and run_id[8] == "_"
                and run_id[:8].isdigit() and run_id[9:].isdigit()
            ):
                run_dir = os.path.join(CAMERA_FRAMES_WINDOWS_DIR, run_id)
                for slot in range(8):
                    candidate = os.path.join(
                        run_dir, "latest_%02d.png" % slot)
                    try:
                        mtime = os.path.getmtime(candidate)
                    except OSError:
                        continue
                    if mtime > latest_mtime:
                        latest = candidate
                        latest_mtime = mtime
            # Do not present a frame from an old mission as a live camera.
            if latest is None or time.time() - latest_mtime > CAMERA_STALE_AFTER_SEC:
                self.send_response(404)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            stat = os.stat(latest)
            etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            try:
                with open(latest, "rb") as camera_file:
                    payload = camera_file.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/api/ai-status":
            self._send_json({
                "enabled": bool(DEEPSEEK_API_KEY),
                "model": DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None,
            })
        elif self.path == "/api/map":
            try:
                with open(DEFAULT_MAP, "r", encoding="utf-8") as f:
                    map_data = json.load(f)
            except Exception:
                map_data = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(map_data).encode("utf-8"))
        elif self.path == "/api/history":
            self._send_json(_load_history()[::-1])
        elif self.path == "/api/current":
            self._send_json({"task_id": _read_current()})
        elif self.path == "/api/trajectory-preview":
            with trajectory_preview_lock:
                preview = dict(trajectory_preview_state)
            preview["exists"] = os.path.isfile(UNREAL_ROUTE_PREVIEW_PATH)
            self._send_json(preview)
        elif self.path.startswith("/api/explainability/"):
            explanation_id = self.path.rsplit("/", 1)[-1]
            try:
                self._send_json(explainability_store.read(explanation_id))
            except (OSError, ValueError, json.JSONDecodeError):
                self._send_json({"error": "解释记录不存在"}, 404)
        elif self.path.startswith("/api/task/"):
            if self.path.endswith("/report"):
                tid = self.path[len("/api/task/"):-len("/report")]
                self._serve_report(tid)
            else:
                tid = self.path.rsplit("/", 1)[-1]
                task = get_task_status(tid)
                if task is None:
                    self._send_json({"error": "任务不存在"}, 404)
                else:
                    self._send_json(task)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", "replace")
        params = parse_qs(body)

        if self.path == "/api/config/deepseek":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的配置请求"}, 403)
                return
            api_key = params.get("api_key", [""])[0].strip()
            ok, error = validate_and_persist_deepseek_key(api_key)
            if not ok:
                self._send_json({"error": error}, 400)
            else:
                self._send_json({
                    "enabled": True,
                    "model": DEEPSEEK_MODEL,
                })
        elif self.path == "/api/config/deepseek/remove":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的配置请求"}, 403)
                return
            remove_deepseek_key()
            self._send_json({"enabled": False, "model": None})
        elif self.path == "/api/command":
            text = params.get("text", [""])[0]
            requested_vision = params.get("vision", ["0"])[0] == "1"
            wants_default_plan = bool(
                MAP_DATA.get("default_plan")
                and re.search(r"完整巡检|全桥巡检|飞越(?:整座|全)?桥|从.*桥头.*对岸", text))
            local_plan = (
                [dict(step) for step in MAP_DATA["default_plan"]]
                if wants_default_plan else parse_plan_local(text))
            if local_plan is not None:
                ai_plan, ai_error = parse_plan_with_deepseek(text)
                if ai_plan is not None:
                    plan = ai_plan
                    parser_name = "deepseek"
                else:
                    try:
                        plan = validate_plan(local_plan)
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, 400)
                        return
                    parser_name = "rules"
                if requested_vision:
                    for step in plan:
                        if step["action"] == "fly_to":
                            step["vision"] = True
                explanation = _create_explanation(
                    text, requested_vision, parser_name,
                    ai_error if parser_name == "rules" else None,
                    validate_plan(plan))
                self._send_json({
                    "action": "sequence",
                    "goal_id": None,
                    "vision": any(
                        step.get("vision", False) for step in plan
                        if step["action"] == "fly_to"),
                    "reply": "已解析为 %d 步任务队列，请确认顺序后执行。" % len(plan),
                    "result": "",
                    "plan": plan,
                    "parser": parser_name,
                    "ai_fallback_reason": (
                        ai_error if parser_name == "rules" else None),
                    "explanation_id": explanation["explanation_id"],
                    "explainability": public_summary(explanation),
                    "error": None,
                })
                return
            rule_parsed = parse_command(text)
            if rule_parsed[0] != "unknown":
                action, goal_id, parsed_vision, reply = rule_parsed
                parser_name = "rules"
                ai_error = None
            else:
                ai_parsed, ai_error = parse_command_with_deepseek(text)
                if ai_parsed is not None:
                    action, goal_id, parsed_vision, reply = ai_parsed
                    parser_name = "deepseek"
                else:
                    action, goal_id, parsed_vision, reply = rule_parsed
                    parser_name = "rules"
            # The checkbox is an explicit operator request. Keep natural-language
            # detection as an additional route, but never let it clear the UI flag.
            vision = requested_vision or parsed_vision
            if vision and not parsed_vision and action == "fly_to":
                reply += "（视觉确认模式）"
            explanation = None
            if action == "fly_to" and goal_id:
                explanation = _create_explanation(
                    text, requested_vision, parser_name,
                    ai_error if parser_name == "rules" else None,
                    validate_plan([{
                        "action": "fly_to", "goal_id": goal_id,
                        "vision": vision,
                    }]))
            result = ""
            if action == "status":
                res = execute_command(action, goal_id, vision)
                result = res.get("result", "")
            self._send_json({
                "action": action, "goal_id": goal_id, "vision": vision,
                "reply": reply, "result": result,
                "parser": parser_name,
                "ai_fallback_reason": ai_error if parser_name == "rules" else None,
                "explanation_id": (
                    explanation["explanation_id"] if explanation else None),
                "explainability": (
                    public_summary(explanation) if explanation else None),
                "error": None if action == "status" else None,
            })
        elif self.path.startswith("/api/task/") and self.path.endswith("/stop"):
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的任务请求"}, 403)
                return
            tid = self.path[len("/api/task/"):-len("/stop")]
            ok, msg = stop_task(tid)
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"error": msg}, 400)
        elif self.path == "/api/execute":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的任务请求"}, 403)
                return
            action = params.get("action", [""])[0]
            goal = params.get("goal", [""])[0]
            vision = params.get("vision", ["0"])[0] == "1"
            res = execute_command(action, goal if goal else None, vision)
            if "error" in res:
                self._send_json(res, 400)
            else:
                self._send_json(res)
        elif self.path == "/api/review-plan":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的任务请求"}, 403)
                return
            raw_plan = params.get("plan", [""])[0]
            explanation_id = params.get("explanation_id", [""])[0] or None
            try:
                payload = json.loads(raw_plan)
                steps = payload.get("steps") if isinstance(payload, dict) else payload
                review = build_plan_review(steps)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            ai_review, ai_error = review_plan_with_deepseek(review)
            review["ai"] = ai_review
            review["reviewer"] = "deepseek" if ai_review else "local"
            review["ai_fallback_reason"] = ai_error if not ai_review else None
            if explanation_id:
                try:
                    record = explainability_store.attach_review(
                        explanation_id, review)
                    review["explanation_id"] = explanation_id
                    review["explainability"] = public_summary(record)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self._send_json({"error": str(exc)}, 400)
                    return
            self._send_json(review)
        elif self.path == "/api/generate-trajectory":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的轨迹生成请求"}, 403)
                return
            raw_plan = params.get("plan", [""])[0]
            explanation_id = params.get("explanation_id", [""])[0] or None
            try:
                payload = json.loads(raw_plan)
                steps = payload.get("steps") if isinstance(payload, dict) else payload
                result = generate_trajectory_preview(steps, explanation_id)
            except (TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json(result)
        elif self.path == "/api/execute-plan":
            if not self._local_origin_allowed():
                self._send_json({"error": "拒绝非本机页面的任务请求"}, 403)
                return
            raw_plan = params.get("plan", [""])[0]
            explanation_id = params.get("explanation_id", [""])[0] or None
            try:
                payload = json.loads(raw_plan)
                steps = payload.get("steps") if isinstance(payload, dict) else payload
            except Exception:
                self._send_json({"error": "任务队列 JSON 无效"}, 400)
                return
            res = execute_plan(steps, explanation_id)
            if "error" in res:
                self._send_json(res, 400)
            else:
                self._send_json(res)
        else:
            self.send_response(404)
            self.end_headers()

    def _render_report_html(self, task, text):
        import html as _html
        safe = _html.escape(text)
        outcome = task.get("outcome") or "?"
        badge_color = {
            "PASS": "#52c41a", "STOPPED": "#faad14",
            "CHECK/FAIL": "#e8533f", "ERROR": "#e8533f", "TIMEOUT": "#e8533f",
        }.get(outcome, "#888")
        return (
            "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<title>任务报告 " + _html.escape(str(task.get("task_id"))) + "</title>"
            "<style>body{font-family:'Segoe UI',system-ui,sans-serif;background:#f5f5f5;padding:24px}"
            ".card{background:#fff;border-radius:8px;padding:20px;max-width:900px;margin:auto;box-shadow:0 1px 4px rgba(0,0,0,.1)}"
            "h1{font-size:18px}.badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:13px;background:"
            + badge_color + "}"
            "pre{white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;font-size:13px;line-height:1.5;background:#fafafa;padding:12px;border-radius:6px}</style>"
            "</head><body><div class=\"card\">"
            "<h1>任务报告 <span class=\"badge\">" + _html.escape(outcome) + "</span></h1>"
            "<p>任务ID：" + _html.escape(str(task.get("task_id"))) + " ｜ 目标："
            + _html.escape(str(task.get("goal_id"))) + " ｜ 视觉：" + ("是" if task.get("vision") else "否") + "</p>"
            "<pre>" + safe + "</pre>"
            "</div></body></html>"
        )

    def _serve_report(self, tid):
        task = get_task_status(tid)
        if task is None:
            self._send_json({"error": "任务不存在"}, 404)
            return
        rp = task.get("report_path")
        if not rp or not os.path.isfile(rp):
            # No summary.md (e.g. a stopped/cancelled task, or sim didn't reach
            # completion). Still serve a clickable, readable report page rather
            # than a bare 404 so the "报告" link always works.
            note = task.get("summary") or "该任务未生成 summary.md（可能是被停止/取消，或仿真未到达完成阶段）。"
            html = self._render_report_html(task, note)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return
        try:
            with open(rp, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("读取报告失败：" + str(e)).encode("utf-8"))
            return
        html = self._render_report_html(task, text)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def main():
    global DEFAULT_MAP, DEFAULT_UE_MAP, GOAL_WHITELIST, SERVER_PORT
    import argparse
    parser = argparse.ArgumentParser(description="IFSC Bridge Inspection Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=int, default=DEFAULT_TELEMETRY_PORT)
    parser.add_argument("--map", type=str, default=DEFAULT_MAP)
    parser.add_argument("--ue-map", type=str, default="",
                        help="optional override; defaults to semantic JSON ue_map")
    args = parser.parse_args()
    SERVER_PORT = args.port

    DEFAULT_MAP = os.path.abspath(args.map)
    GOAL_WHITELIST, map_ue_map = load_map_dispatch_config(DEFAULT_MAP)
    try:
        with open(DEFAULT_MAP, "r", encoding="utf-8") as map_file:
            _refresh_map_runtime_config(json.load(map_file))
    except (OSError, ValueError) as exc:
        parser.error("invalid semantic map JSON: %s" % exc)
    DEFAULT_UE_MAP = args.ue_map or map_ue_map
    if (not re.fullmatch(r"/Game/[A-Za-z0-9_./-]+", DEFAULT_UE_MAP)
            or ".." in DEFAULT_UE_MAP):
        parser.error("invalid --ue-map asset path")

    # Start UDP telemetry listener in background thread
    t = threading.Thread(target=telemetry_listener, args=(args.telemetry_port,), daemon=True)
    t.start()
    print(f"[dashboard] telemetry listener on UDP {args.telemetry_port}")
    print(f"[dashboard] map file: {args.map}")
    print(f"[dashboard] UE map: {DEFAULT_UE_MAP}")

    # ThreadingHTTPServer so a long controller run never blocks telemetry polling.
    if ":" in args.host:
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        server_class = IPv6ThreadingHTTPServer
        display_host = "[%s]" % args.host
    else:
        server_class = ThreadingHTTPServer
        display_host = args.host
    server = server_class((args.host, args.port), DashboardHandler)
    print(f"[dashboard] open http://{display_host}:{args.port} in your browser")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
