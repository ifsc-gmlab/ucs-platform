#!/usr/bin/env python3
"""Validated shared configuration for semantic mission tooling."""

import hashlib
import json
import math
import os

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "semantic_runtime_config.json")


def _number(section, key, minimum=None, maximum=None):
    if key not in section or isinstance(section[key], bool):
        raise ValueError("semantic config missing numeric value: %s" % key)
    value = float(section[key])
    if not math.isfinite(value):
        raise ValueError("semantic config value must be finite: %s" % key)
    if minimum is not None and value < minimum:
        raise ValueError("semantic config value below minimum: %s" % key)
    if maximum is not None and value > maximum:
        raise ValueError("semantic config value above maximum: %s" % key)
    return value


def load_runtime_config(path=None):
    path = os.path.abspath(path or DEFAULT_CONFIG_PATH)
    with open(path, "rb") as handle:
        raw = handle.read()
    config = json.loads(raw.decode("utf-8-sig"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported semantic config schema_version")
    for section in ("mission", "regression_gates", "wind_matrix", "camera"):
        if not isinstance(config.get(section), dict):
            raise ValueError("semantic config missing section: %s" % section)
    mission = config["mission"]
    positive = (
        "altitude_m", "cruise_speed_mps", "avoid_speed_mps",
        "trigger_range_m", "stop_range_m", "arrival_radius_m",
        "max_decel_mps2", "control_latency_s", "velocity_tracking_gain",
        "braking_stop_radius_m", "yaw_rate_deg_s", "yaw_min_speed_mps",
        "final_radius_m", "mission_timeout_s",
    )
    for key in positive:
        _number(mission, key, minimum=1e-9)
    if _number(mission, "stop_range_m") >= _number(mission, "trigger_range_m"):
        raise ValueError("stop_range_m must be smaller than trigger_range_m")

    gates = config["regression_gates"]
    _number(gates, "max_collisions", minimum=0)
    _number(gates, "max_arrival_speed_mps", minimum=0)
    _number(gates, "max_final_error_m", minimum=0)
    _number(gates, "max_average_error_regression_ratio", minimum=0)
    _number(gates, "max_average_duration_regression_ratio", minimum=0)

    for matrix_name in ("stable_wind_speeds_mps", "stress_wind_speeds_mps"):
        matrix = config["wind_matrix"].get(matrix_name)
        if not isinstance(matrix, list) or not matrix:
            raise ValueError("%s must be a non-empty list" % matrix_name)
        if any(not math.isfinite(float(value)) or float(value) <= 0
               for value in matrix):
            raise ValueError("%s must contain positive finite values" % matrix_name)

    camera = config["camera"]
    _number(camera, "capture_hz", minimum=0.5, maximum=4.0)
    _number(camera, "preview_poll_ms", minimum=100)
    _number(camera, "stale_after_s", minimum=0.5)
    config["_path"] = path
    config["_sha256"] = hashlib.sha256(raw).hexdigest()
    return config


def controller_defaults(config):
    mission = config["mission"]
    return {
        "alt": mission["altitude_m"],
        "speed": mission["cruise_speed_mps"],
        "avoid_speed": mission["avoid_speed_mps"],
        "trigger_range": mission["trigger_range_m"],
        "stop_range": mission["stop_range_m"],
        "arrival_radius": mission["arrival_radius_m"],
        "max_decel": mission["max_decel_mps2"],
        "control_latency": mission["control_latency_s"],
        "velocity_tracking_gain": mission["velocity_tracking_gain"],
        "braking_stop_radius": mission["braking_stop_radius_m"],
        "yaw_rate_deg_s": mission["yaw_rate_deg_s"],
        "yaw_min_speed": mission["yaw_min_speed_mps"],
        "final_radius": mission["final_radius_m"],
        "mission_timeout": mission["mission_timeout_s"],
    }
