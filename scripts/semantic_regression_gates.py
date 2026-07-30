#!/usr/bin/env python3
"""Pure regression-gate evaluation shared by tests and reports."""


def evaluate_gates(summary, gates, previous=None):
    failures = []
    results = summary.get("results") or []
    live_rows = [row for row in results if row.get("final_error_m") is not None]
    if any((row.get("collision_count") or 0) > gates["max_collisions"]
           for row in live_rows):
        failures.append("collision limit exceeded")
    if any((row.get("max_arrival_speed_mps") or 0.0) >
           gates["max_arrival_speed_mps"] for row in live_rows):
        failures.append("arrival-speed limit exceeded")
    if any((row.get("final_error_m") or 0.0) >
           gates["max_final_error_m"] for row in live_rows):
        failures.append("final-error limit exceeded")

    current = summary.get("aggregate") or {}
    prior = (previous or {}).get("aggregate") or {}
    comparisons = (
        ("average_final_error_m", "max_average_error_regression_ratio",
         "average final error"),
        ("average_duration_sec", "max_average_duration_regression_ratio",
         "average duration"),
    )
    for metric, gate_name, label in comparisons:
        old = prior.get(metric)
        new = current.get(metric)
        if old is None or new is None or float(old) <= 0:
            continue
        ratio = (float(new) - float(old)) / float(old)
        if ratio > float(gates[gate_name]):
            failures.append("%s regressed %.1f%%" % (label, ratio * 100.0))
    return {"passed": not failures, "failures": failures}
