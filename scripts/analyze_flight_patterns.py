#!/usr/bin/env python3
"""Validate hover/circle motion from one UE5 + PX4 HIL run."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

try:
    from analyze_flight_run import TELEM_RE, parse_hil_csv, parse_summary_flags
except ModuleNotFoundError:
    from outputs.analyze_flight_run import TELEM_RE, parse_hil_csv, parse_summary_flags


def parse_metadata(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            result[key.strip()] = value.strip()
    return result


def parse_samples(path: Path) -> list[dict[str, float]]:
    samples: list[dict[str, float]] = []
    if not path.exists():
        return samples
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = TELEM_RE.search(line)
        if not match or match.group("pos_x") is None or match.group("pos_y") is None:
            continue
        sample = {
            key: float(value)
            for key, value in match.groupdict().items()
            if key != "n" and value is not None
        }
        samples.append(sample)
    return samples


def parse_circle_settings(metadata: dict[str, str]) -> tuple[float, float, float, float]:
    raw = metadata.get("CircleRadius/Laps/Duration/Entry", "1.5 / 1 / 12 / 3")
    parts = [part.strip() for part in raw.split("/")]
    values = [float(part) for part in parts[:4]]
    while len(values) < 4:
        values.append(0.0)
    return values[0], values[1], values[2], values[3]


def path_length(samples: list[dict[str, float]]) -> float:
    total = 0.0
    for previous, current in zip(samples, samples[1:]):
        total += math.hypot(
            current["pos_x"] - previous["pos_x"],
            current["pos_y"] - previous["pos_y"],
        )
    return total


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = (len(ordered) - 1) * percentage / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def analyze(run_dir: Path) -> dict:
    metadata = parse_metadata(run_dir / "summary.md")
    flags = parse_summary_flags(run_dir / "summary.md")
    samples = parse_samples(run_dir / "telemetry.log")
    hil = parse_hil_csv(run_dir / "hil_debug.csv")
    pattern = metadata.get("FlightPattern", "unknown").lower()
    flight_timing = [
        float(part.strip())
        for part in metadata.get("Alt/Hold/Climb/Descend", "1 / 0 / 0 / 0").split("/")[:4]
    ]
    altitude = flight_timing[0]
    hold_seconds = flight_timing[1] if len(flight_timing) > 1 else 0.0
    climb_seconds = flight_timing[2] if len(flight_timing) > 2 else 0.0
    expected_wind_mps = float(metadata.get("WindMpsOverride", "-1"))
    wind_test = expected_wind_mps >= 0.5
    radius, laps, duration, entry = parse_circle_settings(metadata)

    checks: dict[str, bool] = {}
    metrics: dict[str, float | int | str | bool] = {
        "pattern": pattern,
        "samples": len(samples),
        "target_altitude_m": altitude,
        "wind_test": wind_test,
        "expected_wind_mps": expected_wind_mps,
        "circle_radius_m": radius,
        "circle_laps": laps,
        "circle_duration_s": duration,
        "circle_entry_s": entry,
    }
    issues: list[str] = []

    required_flags = [
        "UEStarted", "UEPortListening", "PX4Started", "PX4Connected",
        "PX4Ready", "FlightScriptRan", "Armed", "Offboard",
        "TakeoffDetected", "Disarmed", "TelemetryPass",
    ]
    for key in required_flags:
        checks[key] = flags.get(key) is True
    checks["NoFailsafe"] = flags.get("Failsafe") is False

    if not samples:
        issues.append("telemetry samples unavailable")
        return {
            "run_dir": str(run_dir), "pass": False, "issues": issues,
            "checks": checks, "metrics": metrics,
        }

    ground_candidates = [sample for sample in samples[:40] if sample["pos_z"] < 0.15]
    if not ground_candidates:
        ground_candidates = samples[: min(10, len(samples))]
    center_x = statistics.median(sample["pos_x"] for sample in ground_candidates)
    center_y = statistics.median(sample["pos_y"] for sample in ground_candidates)

    high_threshold = max(0.5, altitude * 0.65)
    airborne = [sample for sample in samples if sample["pos_z"] >= high_threshold]
    metrics["center_x_m"] = round(center_x, 3)
    metrics["center_y_m"] = round(center_y, 3)
    metrics["airborne_samples"] = len(airborne)
    checks["AirborneSamples"] = len(airborne) >= 20

    wind_values = [abs(sample.get("wind_x", 0.0)) for sample in samples]
    signed_wind_values = [sample.get("wind_x", 0.0) for sample in samples]
    max_abs_wind = max(wind_values, default=0.0)
    metrics["max_abs_wind_x_mps"] = round(max_abs_wind, 3)
    metrics["mean_wind_x_mps"] = round(statistics.mean(signed_wind_values), 3)
    metrics["max_abs_pitch_deg"] = round(max(abs(sample.get("pitch", 0.0)) for sample in samples), 3)
    metrics["max_motor_avg"] = hil.get("max_motor_avg")
    metrics["motor_margin"] = (
        round(1.0 - float(hil["max_motor_avg"]), 3)
        if hil.get("max_motor_avg") is not None else None
    )
    if wind_test:
        checks["WindPresent"] = max_abs_wind >= max(0.5, expected_wind_mps * 0.70)
        checks["AttitudeMargin"] = metrics["max_abs_pitch_deg"] < 35.0
        checks["MotorMargin"] = metrics["max_motor_avg"] is not None and metrics["max_motor_avg"] < 0.95
    else:
        checks["ZeroWind"] = max_abs_wind <= 0.05
    telemetry_final_z = samples[-1]["pos_z"]
    hil_final_grounded = (
        hil.get("available") is True
        and abs(float(hil.get("final_pos_z_m", math.inf))) < 0.10
        and float(hil.get("final_motor_avg", math.inf)) < 0.05
    )
    checks["FinalGrounded"] = abs(telemetry_final_z) < 0.10 or hil_final_grounded
    metrics["telemetry_final_pos_z_m"] = round(telemetry_final_z, 3)
    metrics["hil_final_pos_z_m"] = hil.get("final_pos_z_m")
    metrics["hil_final_motor_avg"] = hil.get("final_motor_avg")
    metrics["final_pos_z_m"] = (
        hil.get("final_pos_z_m") if hil_final_grounded else round(telemetry_final_z, 3)
    )

    if airborne:
        relative = [
            (sample["pos_x"] - center_x, sample["pos_y"] - center_y)
            for sample in airborne
        ]
        xs = [point[0] for point in relative]
        ys = [point[1] for point in relative]
        radial = [math.hypot(x, y) for x, y in relative]
        metrics.update({
            "x_span_m": round(max(xs) - min(xs), 3),
            "y_span_m": round(max(ys) - min(ys), 3),
            "max_radius_m": round(max(radial), 3),
            "horizontal_path_m": round(path_length(airborne), 3),
        })

        if pattern == "hover":
            target_crossing = next(
                (sample for sample in airborne if sample["pos_z"] >= altitude * 0.80),
                None,
            )
            remaining_climb = max(0.5, climb_seconds * 0.25)
            hover_settle = min(2.0, max(0.5, hold_seconds * 0.25))
            plateau_start = (
                target_crossing["t"] + remaining_climb + hover_settle
                if target_crossing else math.inf
            )
            plateau_duration = max(1.0, hold_seconds - hover_settle - 0.5)
            pattern_samples = [
                sample for sample in samples
                if plateau_start <= sample["t"] <= plateau_start + plateau_duration
            ]
            metrics["pattern_window_start_s"] = round(plateau_start, 3)
            metrics["pattern_window_duration_s"] = round(plateau_duration, 3)
            metrics["pattern_window_samples"] = len(pattern_samples)
            plateau_radial = [
                math.hypot(sample["pos_x"] - center_x, sample["pos_y"] - center_y)
                for sample in pattern_samples
            ]
            checks["HoverDrift"] = bool(plateau_radial) and max(plateau_radial) <= 0.65
            metrics["max_hover_drift_m"] = round(max(plateau_radial), 3) if plateau_radial else None
        elif pattern == "circle":
            pattern_samples = [
                sample for sample in airborne
                if math.hypot(sample["pos_x"] - center_x, sample["pos_y"] - center_y) >= radius * 0.60
            ]
            checks["CircleXSpan"] = metrics["x_span_m"] >= radius * 1.30
            checks["CircleYSpan"] = metrics["y_span_m"] >= radius * 1.30
            checks["CircleRadiusReached"] = metrics["max_radius_m"] >= radius * 0.75
            checks["CirclePathLength"] = metrics["horizontal_path_m"] >= radius * laps * 4.0
            takeoff_log = (run_dir / "takeoff_offboard.log").read_text(
                encoding="utf-8", errors="ignore"
            ) if (run_dir / "takeoff_offboard.log").exists() else ""
            checks["CircleCommandCompleted"] = (
                "CIRCLE START" in takeoff_log and "CIRCLE COMPLETE" in takeoff_log
            )
        else:
            pattern_samples = []
            issues.append(f"unsupported flight pattern: {pattern}")

        pattern_altitudes = [sample["pos_z"] for sample in pattern_samples]
        if pattern_altitudes:
            altitude_std = statistics.pstdev(pattern_altitudes)
            altitude_median = statistics.median(pattern_altitudes)
            altitude_robust_range = percentile(pattern_altitudes, 95) - percentile(pattern_altitudes, 5)
            metrics["pattern_altitude_median_m"] = round(altitude_median, 3)
            metrics["pattern_altitude_std_m"] = round(altitude_std, 3)
            metrics["pattern_altitude_5_95_range_m"] = round(altitude_robust_range, 3)
            checks["AltitudeHeld"] = altitude_std <= 0.20 and altitude_robust_range <= 0.70
        else:
            checks["AltitudeHeld"] = False

    for name, passed in checks.items():
        if not passed:
            issues.append(f"{name}=False")

    return {
        "run_dir": str(run_dir),
        "pass": not issues,
        "issues": issues,
        "checks": checks,
        "metrics": metrics,
    }


def write_reports(run_dir: Path, result: dict) -> None:
    (run_dir / "pattern_analysis.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    lines = [
        "# Flight Pattern Analysis",
        "",
        f"- Run: `{run_dir.name}`",
        f"- Pattern: `{result['metrics']['pattern']}`",
        f"- Verdict: {'PASS' if result['pass'] else 'CHECK'}",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {name}: {passed}" for name, passed in result["checks"].items())
    lines.extend(["", "## Metrics", ""])
    lines.extend(f"- {name}: {value}" for name, value in result["metrics"].items())
    if result["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in result["issues"])
    (run_dir / "pattern_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    result = analyze(run_dir)
    write_reports(run_dir, result)
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
