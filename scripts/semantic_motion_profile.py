#!/usr/bin/env python3
"""Deterministic horizontal braking and heading profiles for semantic flight."""

import math


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def wrap_angle_rad(angle):
    """Wrap an angle to [-pi, pi)."""
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def stopping_distance(speed_mps, max_decel_mps2, response_time_s=0.0):
    """Distance needed to stop, including one control/plant response interval."""
    speed = max(0.0, float(speed_mps))
    decel = max(1e-6, float(max_decel_mps2))
    latency = max(0.0, float(response_time_s))
    return speed * latency + speed * speed / (2.0 * decel)


def braking_limited_speed(
        remaining_distance_m,
        measured_speed_mps,
        cruise_speed_mps,
        max_decel_mps2,
        stop_radius_m=0.10,
        response_time_s=0.30):
    """Return a safe speed cap that can stop inside the remaining distance.

    The square-root term is the constant-deceleration braking envelope. The
    measured-speed latency reserve starts braking earlier when the vehicle is
    already moving quickly, instead of relying on a fixed distance threshold.
    """
    distance = max(0.0, float(remaining_distance_m))
    measured = max(0.0, float(measured_speed_mps))
    cruise = max(0.0, float(cruise_speed_mps))
    decel = max(1e-6, float(max_decel_mps2))
    stop_radius = max(0.0, float(stop_radius_m))
    latency = max(0.0, float(response_time_s))
    usable_distance = max(0.0, distance - stop_radius - measured * latency)
    envelope_speed = math.sqrt(2.0 * decel * usable_distance)
    proportional_capture = max(0.0, (distance - stop_radius) * 1.10)
    return min(cruise, envelope_speed, proportional_capture)


def profiled_velocity_command(
        dx,
        dy,
        measured_vx,
        measured_vy,
        speed_cap_mps,
        max_command_speed_mps,
        tracking_gain=1.20):
    """Track a route-tangent speed cap with vector velocity feedback.

    Unlike independent X/Y clipping, this preserves the planned direction.
    When measured velocity exceeds the braking envelope, the feedback term can
    command a short reverse acceleration to remove residual momentum.
    """
    distance = math.hypot(float(dx), float(dy))
    speed_cap = max(0.0, float(speed_cap_mps))
    command_limit = max(0.0, float(max_command_speed_mps))
    gain = max(0.0, float(tracking_gain))
    if distance > 1e-9:
        desired_vx = float(dx) / distance * speed_cap
        desired_vy = float(dy) / distance * speed_cap
    else:
        desired_vx = 0.0
        desired_vy = 0.0

    command_vx = desired_vx + gain * (desired_vx - float(measured_vx))
    command_vy = desired_vy + gain * (desired_vy - float(measured_vy))
    magnitude = math.hypot(command_vx, command_vy)
    if magnitude > command_limit > 0.0:
        scale = command_limit / magnitude
        command_vx *= scale
        command_vy *= scale
    return command_vx, command_vy


class HeadingSlewLimiter:
    """Rate-limit commanded yaw while preserving heading at very low speed."""

    def __init__(self, max_rate_deg_s=55.0, min_speed_mps=0.08):
        self.max_rate_rad_s = math.radians(max(1.0, float(max_rate_deg_s)))
        self.min_speed_mps = max(0.0, float(min_speed_mps))
        self.heading_rad = None
        self.last_time_s = None

    def reset(self, heading_rad=None, now_s=None):
        self.heading_rad = (
            None if heading_rad is None else wrap_angle_rad(heading_rad))
        self.last_time_s = now_s

    def update(self, vx, vy, now_s):
        """Return (heading, active).

        active=False tells the MAVLink caller to ignore yaw so PX4 keeps the
        current heading while hovering instead of snapping toward zero.
        """
        now = float(now_s)
        speed = math.hypot(float(vx), float(vy))
        if speed < self.min_speed_mps:
            self.last_time_s = now
            return self.heading_rad, False

        desired = math.atan2(float(vy), float(vx))
        if self.heading_rad is None or self.last_time_s is None:
            self.heading_rad = wrap_angle_rad(desired)
            self.last_time_s = now
            return self.heading_rad, True

        dt = clamp(now - self.last_time_s, 0.0, 0.25)
        max_step = self.max_rate_rad_s * dt
        delta = wrap_angle_rad(desired - self.heading_rad)
        self.heading_rad = wrap_angle_rad(
            self.heading_rad + clamp(delta, -max_step, max_step))
        self.last_time_s = now
        return self.heading_rad, True
