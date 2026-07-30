#!/usr/bin/env python3
"""Pure mission-verdict rules shared by the controller and regression tests."""


def evaluate_mission_result(result, final_radius, vision_requested):
    """Evaluate safety evidence without requiring a fake detour on clear legs."""
    legs = result.get("legs") or []
    avoidance_required_legs = [
        leg for leg in legs if leg.get("avoidance_required")]
    avoidance_demonstrated = all(
        leg.get("avoidance_demonstrated", False)
        for leg in avoidance_required_legs)
    result["avoidance_required_legs"] = len(avoidance_required_legs)
    result["avoidance_demonstrated"] = bool(avoidance_demonstrated)

    checks = [
        (result.get("reached"), "semantic goal not reached"),
        (avoidance_demonstrated,
         "required obstacle avoidance not demonstrated"),
        (result.get("collision_count") == 0,
         "obstacle collision detected"),
        (result.get("sensor_samples", 0) >= 20,
         "insufficient sensor samples"),
        (result.get("landed"), "landing not confirmed"),
        (result.get("disarmed"), "disarm not confirmed"),
        (result.get("final_position_error_m") is not None,
         "final position unavailable"),
        (
            result.get("final_position_error_m") is not None and
            result.get("final_position_error_m") <= final_radius,
            "final landing error %.3fm exceeds %.3fm" % (
                result.get("final_position_error_m") or 0.0,
                final_radius),
        ),
        (
            not vision_requested or result.get("vision_confirmed"),
            "vision confirmation not achieved",
        ),
    ]
    for passed, failure in checks:
        if not passed:
            result["pass"] = False
            result["failure"] = failure
            return False
    result["pass"] = True
    result["failure"] = None
    return True
