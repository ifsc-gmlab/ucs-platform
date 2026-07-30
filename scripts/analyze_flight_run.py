#!/usr/bin/env python3
"""Analyze one PX4+UE validation run directory.

Default:
    python outputs/analyze_flight_run.py

Specific run:
    python outputs/analyze_flight_run.py outputs/verification-runs/20260713-115034
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "outputs" / "verification-runs"

TELEM_RE = re.compile(
    r"\[\s*(?P<n>\d+)\s+t=\s*(?P<t>[-+]?[0-9]*\.?[0-9]+)\]\s+"
    r"(?:pos_x=\s*(?P<pos_x>[-+]?[0-9]*\.?[0-9]+)\s+"
    r"pos_y=\s*(?P<pos_y>[-+]?[0-9]*\.?[0-9]+)\s+)?"
    r"pos_z=\s*(?P<pos_z>[-+]?[0-9]*\.?[0-9]+)\s+"
    r"roll=\s*(?P<roll>[-+]?[0-9]*\.?[0-9]+)\s+"
    r"pitch=\s*(?P<pitch>[-+]?[0-9]*\.?[0-9]+)\s+"
    r"yaw=\s*(?P<yaw>[-+]?[0-9]*\.?[0-9]+)\s+"
    r"vz=\s*(?P<vz>[-+]?[0-9]*\.?[0-9]+)"
    r"(?:\s+vx=\s*(?P<vx>[-+]?[0-9]*\.?[0-9]+))?"
    r"(?:\s+wind_x=\s*(?P<wind_x>[-+]?[0-9]*\.?[0-9]+))?"
)


def latest_run_dir() -> Path:
    runs = [p for p in RUNS_DIR.iterdir() if p.is_dir()] if RUNS_DIR.exists() else []
    if not runs:
        raise SystemExit(f"no run directories under {RUNS_DIR}")
    return sorted(runs, key=lambda p: p.name)[-1]


def parse_summary_flags(path: Path) -> dict[str, bool | str]:
    flags: dict[str, bool | str] = {}
    if not path.exists():
        return flags
    in_flags = False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip() == "## Verdict flags":
            in_flags = True
            continue
        if in_flags and line.startswith("## "):
            break
        if in_flags and line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            value = value.strip()
            if value in {"True", "False"}:
                flags[key.strip()] = value == "True"
            else:
                flags[key.strip()] = value
    return flags


def parse_telemetry(path: Path) -> dict:
    samples = []
    if not path.exists():
        return {"samples": 0, "available": False}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = TELEM_RE.search(line)
        if not match:
            continue
        d = {k: float(v) for k, v in match.groupdict().items() if k != "n" and v is not None}
        d["n"] = int(match.group("n"))
        samples.append(d)
    if not samples:
        return {"samples": 0, "available": False}

    pos_z = [s["pos_z"] for s in samples]
    roll = [abs(s["roll"]) for s in samples]
    pitch = [abs(s["pitch"]) for s in samples]
    vz = [s["vz"] for s in samples]
    times = [s["t"] for s in samples]
    duration = max(times) - min(times) if len(times) > 1 else 0.0
    sample_rate = len(samples) / duration if duration > 0 else None

    result = {
        "available": True,
        "samples": len(samples),
        "duration_s": round(duration, 3),
        "sample_rate_hz": round(sample_rate, 1) if sample_rate else None,
        "max_pos_z_m": round(max(pos_z), 3),
        "min_pos_z_m": round(min(pos_z), 3),
        "final_pos_z_m": round(pos_z[-1], 3),
        "max_abs_roll_deg": round(max(roll), 3),
        "max_abs_pitch_deg": round(max(pitch), 3),
        "max_abs_vz_mps": round(max(abs(v) for v in vz), 3),
        "mean_pos_z_m": round(statistics.mean(pos_z), 3),
    }
    if all("pos_x" in s for s in samples):
        pos_x = [s["pos_x"] for s in samples]
        result.update({
            "min_pos_x_m": round(min(pos_x), 3),
            "max_pos_x_m": round(max(pos_x), 3),
            "final_pos_x_m": round(pos_x[-1], 3),
            "net_pos_x_m": round(pos_x[-1] - pos_x[0], 3),
        })
    if all("vx" in s for s in samples):
        vx = [s["vx"] for s in samples]
        result["max_abs_vx_mps"] = round(max(abs(v) for v in vx), 3)
    if all("wind_x" in s for s in samples):
        wind_x = [s["wind_x"] for s in samples]
        result["mean_wind_x_mps"] = round(statistics.mean(wind_x), 3)
        if "net_pos_x_m" in result:
            result["headwind_motion_confirmed"] = result["mean_wind_x_mps"] > 0.5 and result["net_pos_x_m"] < -0.5
    return result


def quat_to_euler(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n <= 0:
        return 0.0, 0.0, 0.0
    w, x, y, z = w / n, x / n, y / n, z / n
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    sinp = 2 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1 else math.asin(sinp)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def parse_hil_csv(path: Path) -> dict:
    if not path.exists():
        return {"available": False, "rows": 0}
    rows = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("t"):
                rows.append(row)
    if not rows:
        return {"available": False, "rows": 0}

    def f(row: dict, key: str) -> float:
        return float(row[key])

    pos_z = [f(r, "posz") for r in rows]
    motor_avgs = []
    motor_spreads = []
    raw_rolls, raw_pitches = [], []
    for r in rows:
        motors = [f(r, "m0"), f(r, "m1"), f(r, "m2"), f(r, "m3")]
        motor_avgs.append(sum(motors) / 4.0)
        motor_spreads.append(max(motors) - min(motors))
        roll, pitch, _ = quat_to_euler(f(r, "craw_qw"), f(r, "craw_qx"), f(r, "craw_qy"), f(r, "craw_qz"))
        raw_rolls.append(abs(roll))
        raw_pitches.append(abs(pitch))

    times_us = [f(r, "t") for r in rows]
    duration_s = (max(times_us) - min(times_us)) / 1_000_000.0 if len(times_us) > 1 else 0.0
    return {
        "available": True,
        "rows": len(rows),
        "duration_s": round(duration_s, 3),
        "update_rate_hz": round(len(rows) / duration_s, 1) if duration_s > 0 else None,
        "max_pos_z_m": round(max(pos_z), 3),
        "final_pos_z_m": round(pos_z[-1], 3),
        "max_motor_avg": round(max(motor_avgs), 3),
        "max_motor_spread": round(max(motor_spreads), 3),
        "final_motor_avg": round(motor_avgs[-1], 3),
        "max_abs_raw_roll_deg": round(max(raw_rolls), 3),
        "max_abs_raw_pitch_deg": round(max(raw_pitches), 3),
    }


def parse_px4_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    return {
        "available": bool(text),
        "ready": "Ready for takeoff" in text,
        "armed": "Armed by external command" in text or "Armed by internal command" in text,
        "takeoff_detected": "Takeoff detected" in text,
        "disarmed": "Disarmed by external command" in text or "Disarmed by internal command" in text,
        "failsafe": "Failsafe activated" in text,
        "preflight_fail_count": len(re.findall(r"Preflight Fail", text)),
        "disarm_denied_count": len(re.findall(r"Disarming denied", text)),
    }


def parse_takeoff_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    return {
        "available": bool(text),
        "armed_ack": "ACK result=0" in text and "[*] ARMED" in text or "ARM (PX4" in text and "ACK result=0" in text,
        "offboard": "OFFBOARD engaged" in text,
        "force_disarm_ok": "disarmed (ACK result=0)" in text,
        "clean_disarm_denied": "clean DISARM denied" in text,
    }


def verdict(flags: dict, telemetry: dict, hil: dict, px4: dict, takeoff: dict) -> tuple[bool, list[str]]:
    checks = []
    required_flags = [
        "UEStarted",
        "UEPortListening",
        "PX4Started",
        "PX4Connected",
        "PX4Ready",
        "FlightScriptRan",
        "Armed",
        "Offboard",
        "TakeoffDetected",
        "Disarmed",
    ]
    for key in required_flags:
        if flags.get(key) is False:
            checks.append(f"{key}=False")
    if px4.get("failsafe"):
        checks.append("PX4 failsafe activated")
    if telemetry.get("available"):
        if telemetry["max_pos_z_m"] < 0.5:
            checks.append("telemetry max_pos_z_m < 0.5")
        if telemetry["max_abs_roll_deg"] >= 55:
            checks.append("telemetry roll >= 55deg")
        if telemetry["max_abs_pitch_deg"] >= 55:
            checks.append("telemetry pitch >= 55deg")
        if abs(telemetry["final_pos_z_m"]) >= 0.5:
            checks.append("telemetry final altitude not near ground")
    else:
        checks.append("telemetry unavailable")
    if hil.get("available") and hil["max_motor_avg"] >= 0.98:
        checks.append("motor average near saturation")
    return not checks, checks


def write_reports(run_dir: Path, data: dict) -> None:
    json_path = run_dir / "flight_analysis.json"
    md_path = run_dir / "flight_analysis.md"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    lines = [
        "# Flight Analysis",
        "",
        f"- Run: `{run_dir.name}`",
        f"- Verdict: {'PASS' if data['pass'] else 'CHECK'}",
    ]
    if data["issues"]:
        lines += ["", "## Issues"] + [f"- {issue}" for issue in data["issues"]]
    lines += [
        "",
        "## Summary Flags",
        "",
    ]
    for key, value in data["summary_flags"].items():
        lines.append(f"- {key}: {value}")

    for title, section in [
        ("Telemetry", data["telemetry"]),
        ("HIL CSV", data["hil_csv"]),
        ("PX4 Log", data["px4_log"]),
        ("Takeoff Log", data["takeoff_log"]),
    ]:
        lines += ["", f"## {title}", ""]
        for key, value in section.items():
            lines.append(f"- {key}: {value}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path, help="run directory; defaults to latest")
    parser.add_argument("--no-write", action="store_true", help="print only, do not write report files")
    args = parser.parse_args()

    run_dir = args.run_dir or latest_run_dir()
    run_dir = run_dir.resolve()
    if not run_dir.exists():
        raise SystemExit(f"run directory not found: {run_dir}")

    flags = parse_summary_flags(run_dir / "summary.md")
    telemetry = parse_telemetry(run_dir / "telemetry.log")
    hil = parse_hil_csv(run_dir / "hil_debug.csv")
    px4 = parse_px4_log(run_dir / "px4_sitl.log")
    takeoff = parse_takeoff_log(run_dir / "takeoff_offboard.log")
    ok, issues = verdict(flags, telemetry, hil, px4, takeoff)

    data = {
        "run_dir": str(run_dir),
        "pass": ok,
        "issues": issues,
        "summary_flags": flags,
        "telemetry": telemetry,
        "hil_csv": hil,
        "px4_log": px4,
        "takeoff_log": takeoff,
    }

    if not args.no_write:
        write_reports(run_dir, data)

    print(json.dumps(data, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
