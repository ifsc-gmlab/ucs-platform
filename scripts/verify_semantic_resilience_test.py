#!/usr/bin/env python3
"""Offline checks for configuration validation and regression gates."""

import json
import os
import tempfile

from semantic_regression_gates import evaluate_gates
from semantic_runtime_config import load_runtime_config


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_invalid_config(config, message):
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(config, handle)
        invalid_path = handle.name
    try:
        try:
            load_runtime_config(invalid_path)
        except (KeyError, TypeError, ValueError):
            return
        raise AssertionError(message)
    finally:
        os.unlink(invalid_path)


def main():
    config = load_runtime_config()
    require(config["_sha256"], "config hash missing")
    require(config["wind_matrix"]["stable_wind_speeds_mps"] == [3, 5, 8],
            "stable wind matrix changed unexpectedly")

    require_invalid_config({"schema_version": 99},
                           "invalid schema was accepted")
    invalid_camera = json.loads(json.dumps(config))
    invalid_camera.pop("_path")
    invalid_camera.pop("_sha256")
    invalid_camera["camera"]["capture_hz"] = 20
    require_invalid_config(invalid_camera,
                           "unsupported camera capture rate was accepted")
    invalid_ranges = json.loads(json.dumps(invalid_camera))
    invalid_ranges["camera"]["capture_hz"] = 2
    invalid_ranges["mission"]["stop_range_m"] = 4
    require_invalid_config(invalid_ranges,
                           "inverted obstacle ranges were accepted")

    gates = config["regression_gates"]
    good = {
        "aggregate": {"average_final_error_m": 0.02,
                      "average_duration_sec": 100},
        "results": [{"collision_count": 0, "final_error_m": 0.02,
                     "max_arrival_speed_mps": 0.05}],
    }
    require(evaluate_gates(good, gates)["passed"], "good run rejected")
    collision = dict(good)
    collision["results"] = [dict(good["results"][0], collision_count=1)]
    require(not evaluate_gates(collision, gates)["passed"],
            "collision regression accepted")
    slow = dict(good)
    slow["aggregate"] = dict(good["aggregate"], average_duration_sec=140)
    require(not evaluate_gates(slow, gates, previous=good)["passed"],
            "duration regression accepted")
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
