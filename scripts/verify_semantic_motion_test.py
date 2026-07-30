#!/usr/bin/env python3
"""Pure regression checks for semantic braking and heading profiles."""

import math

from semantic_motion_profile import (
    HeadingSlewLimiter,
    braking_limited_speed,
    profiled_velocity_command,
    stopping_distance,
    wrap_angle_rad,
)


def close(actual, expected, tolerance=1e-6):
    return abs(actual - expected) <= tolerance


def main():
    checks = []

    def check(name, condition):
        checks.append(bool(condition))
        print(("PASS" if condition else "FAIL"), name)

    check("stopping distance includes response reserve",
          close(stopping_distance(1.0, 0.5, 0.3), 1.3))

    far = braking_limited_speed(20.0, 1.0, 1.25, 0.45)
    near = braking_limited_speed(1.0, 1.0, 1.25, 0.45)
    faster = braking_limited_speed(1.0, 1.4, 1.25, 0.45)
    stopped = braking_limited_speed(0.10, 0.4, 1.25, 0.45)
    check("far target permits cruise speed", close(far, 1.25))
    check("near target enters braking envelope", 0.0 < near < far)
    check("higher measured speed brakes earlier", faster < near)
    check("stop radius commands zero speed", close(stopped, 0.0))

    far_vx, far_vy = profiled_velocity_command(
        6.0, -4.0, 0.0, 0.0, 1.25, 1.25)
    check("vector command preserves route direction",
          far_vx > 0.0 and far_vy < 0.0 and
          close(far_vy / far_vx, -4.0 / 6.0))
    brake_vx, brake_vy = profiled_velocity_command(
        1.0, 0.0, 0.6, 0.0, 0.15, 1.25)
    check("velocity feedback can command braking", brake_vx < 0.0 and close(brake_vy, 0.0))
    limited_vx, limited_vy = profiled_velocity_command(
        1.0, 1.0, -2.0, -2.0, 1.25, 1.25)
    check("vector command respects magnitude limit",
          math.hypot(limited_vx, limited_vy) <= 1.25 + 1e-6)

    limiter = HeadingSlewLimiter(max_rate_deg_s=60.0, min_speed_mps=0.08)
    limiter.reset(0.0, 10.0)
    heading, active = limiter.update(0.0, 1.0, 10.1)
    check("heading rate is limited", active and close(
        math.degrees(heading), 6.0, 1e-5))
    held, active = limiter.update(0.01, 0.0, 10.2)
    check("low speed preserves heading", not active and close(held, heading))

    limiter.reset(math.radians(179.0), 20.0)
    wrapped, active = limiter.update(
        math.cos(math.radians(-179.0)),
        math.sin(math.radians(-179.0)),
        20.1)
    check("heading uses shortest path across wrap",
          active and abs(math.degrees(wrap_angle_rad(
              wrapped - math.radians(179.0)))) < 6.1)

    if not all(checks):
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
