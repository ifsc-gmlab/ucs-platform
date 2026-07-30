#!/usr/bin/env python3
"""Quantify vertical hover oscillation from a PX4 ULog."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
from pyulog import ULog


def get_data(ulog: ULog, name: str) -> dict[str, np.ndarray]:
    for dataset in ulog.data_list:
        if dataset.name == name and dataset.multi_id == 0:
            return dataset.data
    raise KeyError(f"missing ULog topic: {name}")


def interp(target_t: np.ndarray, data: dict[str, np.ndarray], field: str) -> np.ndarray:
    source_t = np.asarray(data["timestamp"], dtype=float) / 1e6
    values = np.asarray(data[field], dtype=float)
    finite = np.isfinite(source_t) & np.isfinite(values)

    if np.count_nonzero(finite) < 2:
        return np.full_like(target_t, np.nan, dtype=float)

    return np.interp(target_t, source_t[finite], values[finite], left=np.nan, right=np.nan)


def step_interp(target_t: np.ndarray, data: dict[str, np.ndarray], field: str) -> np.ndarray:
    source_t = np.asarray(data["timestamp"], dtype=float) / 1e6
    values = np.asarray(data[field], dtype=float)
    indices = np.searchsorted(source_t, target_t, side="right") - 1
    result = np.full_like(target_t, np.nan, dtype=float)
    valid = (indices >= 0) & (indices < len(values))
    result[valid] = values[indices[valid]]
    return result


def rate_interp(target_t: np.ndarray, data: dict[str, np.ndarray], field: str) -> np.ndarray:
    source_t = np.asarray(data["timestamp"], dtype=float) / 1e6
    values = np.asarray(data[field], dtype=float)
    finite = np.isfinite(source_t) & np.isfinite(values)
    source_t = source_t[finite]
    values = values[finite]

    if len(source_t) < 2:
        return np.full_like(target_t, np.nan, dtype=float)

    source_t, unique_indices = np.unique(source_t, return_index=True)
    values = values[unique_indices]

    if len(source_t) < 2:
        return np.full_like(target_t, np.nan, dtype=float)

    rate = np.gradient(values, source_t)
    return np.interp(target_t, source_t, rate, left=np.nan, right=np.nan)


def longest_true_segment(mask: np.ndarray) -> slice:
    padded = np.concatenate(([False], mask, [False])).astype(np.int8)
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)

    if len(starts) == 0:
        raise RuntimeError("no stable hover segment found")

    lengths = ends - starts
    best = int(np.argmax(lengths))
    return slice(int(starts[best]), int(ends[best]))


def dominant_frequency(t: np.ndarray, values: np.ndarray, minimum_hz: float = 0.1) -> float:
    duration = float(t[-1] - t[0])

    if len(t) < 20 or duration < 3.0:
        return math.nan

    sample_rate = 20.0
    uniform_t = np.arange(t[0], t[-1], 1.0 / sample_rate)
    uniform_v = np.interp(uniform_t, t, values)
    uniform_v -= np.mean(uniform_v)
    spectrum = np.abs(np.fft.rfft(uniform_v))
    frequencies = np.fft.rfftfreq(len(uniform_v), d=1.0 / sample_rate)
    band = (frequencies >= minimum_hz) & (frequencies <= 5.0)

    if not np.any(band):
        return math.nan

    indices = np.flatnonzero(band)
    return float(frequencies[indices[np.argmax(spectrum[band])]])


def fmt(value: float, digits: int = 4) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.{digits}f}"


def apply_px4_log_parameters(parameters: dict, px4_log: Path | None, names: tuple[str, ...]) -> None:
    if px4_log is None or not px4_log.exists():
        return

    text = px4_log.read_text(encoding="utf-8", errors="ignore")
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"

    for name in names:
        matches = re.findall(
            rf"{re.escape(name)}:\s*curr:\s*{number}\s*->\s*new:\s*({number})",
            text,
        )
        if matches:
            parameters[name] = float(matches[-1])


def summarize_window(
    t: np.ndarray,
    z: np.ndarray,
    vz: np.ndarray,
    z_sp: np.ndarray,
    thrust_z: np.ndarray,
) -> dict[str, float]:
    z_error = z - z_sp
    return {
        "duration_s": float(t[-1] - t[0]),
        "height_mean_m": -float(np.mean(z)),
        "height_peak_to_peak_m": float(np.ptp(z)),
        "height_robust_range_m": float(np.percentile(z, 95) - np.percentile(z, 5)),
        "height_error_rms_m": float(np.sqrt(np.mean(np.square(z_error)))),
        "vertical_speed_rms_mps": float(np.sqrt(np.mean(np.square(vz)))),
        "vertical_speed_peak_mps": float(np.max(np.abs(vz))),
        "dominant_frequency_hz": dominant_frequency(t, z_error),
        "jitter_frequency_hz": dominant_frequency(t, z_error, minimum_hz=0.4),
        "thrust_mean": -float(np.nanmean(thrust_z)),
        "thrust_std": float(np.nanstd(thrust_z)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ulog", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--px4-log", type=Path)
    args = parser.parse_args()

    ulog = ULog(str(args.ulog), message_name_filter_list=[
        "actuator_armed",
        "hover_thrust_estimate",
        "vehicle_local_position",
        "vehicle_local_position_groundtruth",
        "vehicle_local_position_setpoint",
        "vehicle_status",
        "vehicle_thrust_setpoint",
        "trajectory_setpoint",
    ])
    armed = get_data(ulog, "actuator_armed")
    local = get_data(ulog, "vehicle_local_position")
    local_sp = get_data(ulog, "vehicle_local_position_setpoint")
    status = get_data(ulog, "vehicle_status")
    thrust = get_data(ulog, "vehicle_thrust_setpoint")

    armed_t = np.asarray(armed["timestamp"], dtype=float) / 1e6
    armed_mask = np.asarray(armed["armed"], dtype=bool)

    if not np.any(armed_mask):
        raise RuntimeError("ULog contains no armed interval")

    arm_start = float(armed_t[np.flatnonzero(armed_mask)[0]])
    arm_end = float(armed_t[np.flatnonzero(armed_mask)[-1]])
    logger_started_armed = bool(armed_mask[0])

    t = np.asarray(local["timestamp"], dtype=float) / 1e6
    z = np.asarray(local["z"], dtype=float)
    vz = np.asarray(local["vz"], dtype=float)
    z_sp = interp(t, local_sp, "z")
    thrust_z = interp(t, thrust, "xyz[2]")
    armed_at_t = step_interp(t, armed, "armed")
    nav_state = step_interp(t, status, "nav_state")

    arm_estimated_z = float(np.interp(arm_start, t, z))
    arm_estimated_vz = float(np.interp(arm_start, t, vz))
    arm_groundtruth_z = math.nan

    try:
        arm_groundtruth = get_data(ulog, "vehicle_local_position_groundtruth")
        arm_groundtruth_z = float(interp(np.asarray([arm_start]), arm_groundtruth, "z")[0])
    except KeyError:
        pass

    try:
        trajectory = get_data(ulog, "trajectory_setpoint")
        command_z_rate = rate_interp(t, trajectory, "position[2]")
    except KeyError:
        command_z_rate = rate_interp(t, local_sp, "z")

    hover_candidate = (
        (armed_at_t > 0.5)
        & ((nav_state == 4) | (nav_state == 14))  # AUTO_LOITER or OFFBOARD.
        & np.isfinite(z)
        & np.isfinite(vz)
        & np.isfinite(z_sp)
        & (np.abs(command_z_rate) < 0.05)
        & (-z > 0.5)
    )
    hover_slice = longest_true_segment(hover_candidate)
    hover_t = t[hover_slice]
    hover_z = z[hover_slice]
    hover_vz = vz[hover_slice]
    hover_z_sp = z_sp[hover_slice]
    hover_thrust = thrust_z[hover_slice]
    overall = summarize_window(hover_t, hover_z, hover_vz, hover_z_sp, hover_thrust)

    try:
        groundtruth = get_data(ulog, "vehicle_local_position_groundtruth")
        groundtruth_z = interp(hover_t, groundtruth, "z")
        valid_gt = np.isfinite(groundtruth_z)
        estimated_centered = hover_z[valid_gt] - np.mean(hover_z[valid_gt])
        groundtruth_centered = groundtruth_z[valid_gt] - np.mean(groundtruth_z[valid_gt])
        estimator_groundtruth_rms = float(np.sqrt(np.mean(np.square(estimated_centered - groundtruth_centered))))
        if np.std(estimated_centered) > 1e-6 and np.std(groundtruth_centered) > 1e-6:
            estimator_groundtruth_corr = float(np.corrcoef(hover_z[valid_gt], groundtruth_z[valid_gt])[0, 1])
        else:
            estimator_groundtruth_corr = math.nan
        groundtruth_peak_to_peak = float(np.ptp(groundtruth_z[valid_gt]))
    except (KeyError, ValueError):
        estimator_groundtruth_rms = math.nan
        estimator_groundtruth_corr = math.nan
        groundtruth_peak_to_peak = math.nan

    try:
        hover_estimate = get_data(ulog, "hover_thrust_estimate")
        estimated_hover_thrust = interp(hover_t, hover_estimate, "hover_thrust")
        estimated_hover_thrust_mean = float(np.nanmean(estimated_hover_thrust))
    except KeyError:
        estimated_hover_thrust_mean = math.nan

    tracked_parameters = (
        "MPC_Z_P",
        "MPC_Z_VEL_P_ACC",
        "MPC_Z_VEL_I_ACC",
        "MPC_Z_VEL_D_ACC",
        "MPC_THR_HOVER",
        "EKF2_EV_CTRL",
        "EKF2_HGT_REF",
        "EKF2_EVP_NOISE",
        "EKF2_EVP_GATE",
        "EKF2_EVV_NOISE",
        "EKF2_EVV_GATE",
        "EKF2_IMU_CTRL",
        "EKF2_ABL_LIM",
    )
    parameters = dict(ulog.initial_parameters)
    parameter_changes = []

    for timestamp, name, value in ulog.changed_parameters:
        if name in tracked_parameters:
            parameters[name] = value
            parameter_changes.append((float(timestamp) / 1e6, name, float(value)))

    px4_log = args.px4_log
    if px4_log is None:
        sibling_log = args.ulog.parent / "px4_sitl.log"
        px4_log = sibling_log if sibling_log.exists() else None
    # A second armed flight can start a new ULog in the middle of one PX4 session.
    # Its initial parameter snapshot is newer than the startup shell log.
    if not logger_started_armed:
        apply_px4_log_parameters(parameters, px4_log, tracked_parameters)

    pid_change_times = [
        timestamp
        for timestamp, name, _ in parameter_changes
        if name != "MPC_THR_HOVER" and hover_t[0] <= timestamp <= hover_t[-1]
    ]

    def phase_metrics(start: float, end: float) -> dict[str, float] | None:
        mask = (hover_t >= start) & (hover_t <= end)

        if np.count_nonzero(mask) < 100 or end - start < 5.0:
            return None

        return summarize_window(
            hover_t[mask], hover_z[mask], hover_vz[mask], hover_z_sp[mask], hover_thrust[mask]
        )

    baseline = None
    final = None
    settling_margin_s = 10.0
    settled = phase_metrics(hover_t[0] + settling_margin_s, hover_t[-1])

    if pid_change_times:
        baseline = phase_metrics(
            hover_t[0] + settling_margin_s,
            min(pid_change_times) - 1.0,
        )
        final = phase_metrics(
            max(pid_change_times) + settling_margin_s,
            hover_t[-1],
        )

    metrics = {
        "armed_duration_s": arm_end - arm_start,
        "hover_start_s": hover_t[0] - arm_start,
        "arm_estimated_height_m": -arm_estimated_z,
        "arm_groundtruth_height_m": -arm_groundtruth_z,
        "arm_height_error_m": arm_groundtruth_z - arm_estimated_z,
        "arm_estimated_vz_mps": arm_estimated_vz,
        **overall,
        "estimated_hover_thrust_mean": estimated_hover_thrust_mean,
        "groundtruth_peak_to_peak_m": groundtruth_peak_to_peak,
        "estimator_groundtruth_rms_m": estimator_groundtruth_rms,
        "estimator_groundtruth_corr": estimator_groundtruth_corr,
    }

    invalid_reasons = []
    if math.isfinite(metrics["arm_height_error_m"]) and abs(metrics["arm_height_error_m"]) > 0.30:
        invalid_reasons.append(
            f"arm-time estimated/physical height error was {metrics['arm_height_error_m']:+.3f} m"
        )
    if abs(metrics["arm_estimated_vz_mps"]) > 0.30:
        invalid_reasons.append(
            f"arm-time estimated vertical speed was {metrics['arm_estimated_vz_mps']:+.3f} m/s NED"
        )
    if math.isfinite(metrics["estimator_groundtruth_rms_m"]) and metrics["estimator_groundtruth_rms_m"] > 0.50:
        invalid_reasons.append(
            "demeaned estimator/physical height disagreement exceeded 0.50 m"
        )

    validity = "INVALID FOR PID COMPARISON" if invalid_reasons else "VALID"
    parameter_source = (
        "ULog initial snapshot (logger started while armed)"
        if logger_started_armed
        else "PX4 startup log plus ULog parameter changes"
    )

    lines = [
        "# Vertical Oscillation Analysis",
        "",
        f"- ULog: `{args.ulog}`",
        f"- Armed duration: {fmt(metrics['armed_duration_s'], 2)} s",
        f"- Selected hover segment: +{fmt(metrics['hover_start_s'], 2)} s for {fmt(metrics['duration_s'], 2)} s",
        f"- Stable phase comparison excludes {settling_margin_s:.0f} s after hover entry or the final PID change",
        "",
        "## Analysis Validity",
        "",
        f"- Verdict: **{validity}**",
        f"- Parameter source: {parameter_source}",
        f"- Arm-time physical height: {fmt(metrics['arm_groundtruth_height_m'], 3)} m",
        f"- Arm-time estimated height: {fmt(metrics['arm_estimated_height_m'], 3)} m",
        f"- Arm-time estimated vertical speed (NED): {fmt(metrics['arm_estimated_vz_mps'], 3)} m/s",
    ]

    if invalid_reasons:
        lines.extend([f"- Reason: {reason}" for reason in invalid_reasons])
        lines.append("- Do not tune PID from this log; restart PX4/UE and repeat one flight in a fresh session.")

    lines.extend([
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Mean height | {fmt(metrics['height_mean_m'], 3)} m |",
        f"| Estimated height peak-to-peak | {fmt(metrics['height_peak_to_peak_m'], 3)} m |",
        f"| Estimated height 5-95% range | {fmt(metrics['height_robust_range_m'], 3)} m |",
        f"| Ground-truth height peak-to-peak | {fmt(metrics['groundtruth_peak_to_peak_m'], 3)} m |",
        f"| Height setpoint error RMS | {fmt(metrics['height_error_rms_m'], 3)} m |",
        f"| Vertical speed RMS | {fmt(metrics['vertical_speed_rms_mps'], 3)} m/s |",
        f"| Vertical speed peak | {fmt(metrics['vertical_speed_peak_mps'], 3)} m/s |",
        f"| Dominant height frequency | {fmt(metrics['dominant_frequency_hz'], 3)} Hz |",
        f"| Jitter-band frequency (0.4-5 Hz) | {fmt(metrics['jitter_frequency_hz'], 3)} Hz |",
        f"| Mean commanded thrust | {fmt(metrics['thrust_mean'], 4)} |",
        f"| Commanded thrust standard deviation | {fmt(metrics['thrust_std'], 4)} |",
        f"| Mean hover-thrust estimate | {fmt(metrics['estimated_hover_thrust_mean'], 4)} |",
        f"| Demeaned estimator/ground-truth RMS difference | {fmt(metrics['estimator_groundtruth_rms_m'], 4)} m |",
        f"| Estimator/ground-truth correlation | {fmt(metrics['estimator_groundtruth_corr'], 4)} |",
        "",
        "## PX4 Parameters",
        "",
        "| Parameter | Value |",
        "|---|---:|",
    ])

    for name in tracked_parameters:
        value = float(parameters[name]) if name in parameters else math.nan
        lines.append(f"| `{name}` | {fmt(value, 4)} |")

    lines.extend(["", "## Parameter Change Timeline", "", "| Time from arm | Parameter | Value |", "|---:|---|---:|"])

    for timestamp, name, value in parameter_changes:
        lines.append(f"| {timestamp - arm_start:.2f} s | `{name}` | {value:.4f} |")

    def append_phase(title: str, phase: dict[str, float] | None) -> None:
        lines.extend(["", f"## {title}", ""])

        if phase is None:
            lines.append("No stable window of at least 5 seconds was available.")
            return

        lines.extend([
            "| Metric | Value |",
            "|---|---:|",
            f"| Duration | {fmt(phase['duration_s'], 2)} s |",
            f"| Height peak-to-peak | {fmt(phase['height_peak_to_peak_m'], 3)} m |",
            f"| Height 5-95% range | {fmt(phase['height_robust_range_m'], 3)} m |",
            f"| Height error RMS | {fmt(phase['height_error_rms_m'], 3)} m |",
            f"| Vertical speed RMS | {fmt(phase['vertical_speed_rms_mps'], 3)} m/s |",
            f"| Jitter-band frequency | {fmt(phase['jitter_frequency_hz'], 3)} Hz |",
            f"| Thrust standard deviation | {fmt(phase['thrust_std'], 4)} |",
        ])

    append_phase("Stable Before First PID Change", baseline)
    append_phase("Stable After Final PID Change", final)
    append_phase("Settled Hover", settled)
    lines.append("")
    report = "\n".join(lines)
    print(report)

    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
