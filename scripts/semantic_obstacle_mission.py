#!/usr/bin/env python3
"""Semantic A-to-B mission with UE multi-range obstacle feedback.

Runs inside WSL. UE sends JSON telemetry to this process; the controller sends
velocity/altitude setpoints to PX4, which retains low-level attitude control.
"""

import argparse
import json
import math
import os
import socket
import sys
import threading
import time

import takeoff_offboard as flight
from semantic_mission_verdict import evaluate_mission_result
from semantic_route_planner import intersected_obstacle_ids, plan_safe_route

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


class TelemetryReceiver:
    def __init__(self, port, trace_path=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(0.2)
        self.latest = None
        self.latest_monotonic = 0.0
        self.samples = 0
        self.trace = open(trace_path, "w", encoding="utf-8") if trace_path else None

    def close(self):
        if self.trace:
            self.trace.close()
        self.sock.close()

    def poll(self, timeout=0.2):
        deadline = time.monotonic() + timeout
        newest = None
        while time.monotonic() < deadline:
            try:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                data, _ = self.sock.recvfrom(16384)
            except socket.timeout:
                break
            try:
                item = json.loads(data.decode("utf-8", "ignore"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            newest = item
            self.samples += 1
            self.latest_monotonic = time.monotonic()
        if newest is not None:
            self.latest = newest
            if self.trace:
                self.trace.write(json.dumps(newest, ensure_ascii=False) + "\n")
                self.trace.flush()
        return self.latest

    def wait_ready(self, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sample = self.poll(0.5)
            if sample and sample.get("proximity_enabled") and "range_front_m" in sample:
                return sample
        raise RuntimeError("no UE proximity telemetry received")


class VisionScanner:
    """Run filesystem frame reads and OpenCV detection off the control loop."""

    def __init__(self, vision_module, camera_dir, window_sec=3.0, max_frames=8):
        self.vision_module = vision_module
        self.camera_dir = camera_dir
        self.window_sec = window_sec
        self.max_frames = max_frames
        self.request_event = threading.Event()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.snapshot = None
        self.thread = threading.Thread(
            target=self._run, name="vision-scanner", daemon=True)
        self.thread.start()

    def request(self):
        self.request_event.set()

    def latest(self):
        with self.lock:
            return dict(self.snapshot) if self.snapshot is not None else None

    def close(self):
        self.stop_event.set()
        self.request_event.set()
        self.thread.join(timeout=0.2)

    def _run(self):
        while not self.stop_event.is_set():
            if not self.request_event.wait(0.2):
                continue
            self.request_event.clear()
            if self.stop_event.is_set():
                break
            started = time.monotonic()
            try:
                recent = self.vision_module.recent_frames(
                    self.camera_dir,
                    window_sec=self.window_sec,
                    max_frames=self.max_frames)
                aggregate = (
                    self.vision_module.aggregate_detections(recent)
                    if recent else [])
                snapshot = {
                    "completed_monotonic": time.monotonic(),
                    "duration_sec": time.monotonic() - started,
                    "frame_count": len(recent),
                    "detections": aggregate,
                    "error": None,
                }
            except Exception as exc:
                snapshot = {
                    "completed_monotonic": time.monotonic(),
                    "duration_sec": time.monotonic() - started,
                    "frame_count": 0,
                    "detections": [],
                    "error": str(exc),
                }
            with self.lock:
                self.snapshot = snapshot


def resolve_goal(map_data, requested):
    key = requested.strip().lower()
    for goal_id, goal in map_data["goals"].items():
        aliases = [str(value).lower() for value in goal.get("aliases", [])]
        if key == goal_id.lower() or key in aliases:
            return goal_id, goal
    raise ValueError("unknown semantic goal %r; choices: %s" % (
        requested, ", ".join(sorted(map_data["goals"]))))


def send_velocity_altitude_setpoint(sock, mav_cmd, sys_id, comp_id, vx, vy, altitude):
    send_velocity_position_z_setpoint(
        sock, mav_cmd, sys_id, comp_id, vx, vy, -altitude)


def send_velocity_position_z_setpoint(sock, mav_cmd, sys_id, comp_id, vx, vy, z_setpoint):
    # Ignore PX/PY, VZ, acceleration and yaw-rate. While translating, command
    # yaw along the horizontal velocity vector so the nose, onboard camera and
    # body-mounted range fans point in the actual direction of travel. When
    # nearly stationary, ignore yaw instead of snapping back to zero.
    horizontal_speed = math.hypot(vx, vy)
    type_mask = 2531
    yaw = 0.0
    if horizontal_speed >= 0.08:
        yaw = math.atan2(vy, vx)
    else:
        type_mask |= 1024  # POSITION_TARGET_TYPEMASK_YAW_IGNORE
    message = mav_cmd.set_position_target_local_ned_encode(
        0, sys_id, comp_id, flight.MAV.MAV_FRAME_LOCAL_NED, type_mask,
        0.0, 0.0, z_setpoint, vx, vy, 0.0,
        0.0, 0.0, 0.0, yaw, 0.0)
    sock.sendto(message.pack(mav_cmd), flight.PX4_ADDR)


def send_velocity_touchdown_setpoint(
        sock, mav_cmd, sys_id, comp_id, vx, vy, down_velocity):
    # Use velocity control on all three axes. Horizontal UE-telemetry feedback
    # avoids the PX4/UE local-origin offset that otherwise moves the aircraft
    # while a position-only landing setpoint is active.
    type_mask = 3527
    message = mav_cmd.set_position_target_local_ned_encode(
        0, sys_id, comp_id, flight.MAV.MAV_FRAME_LOCAL_NED, type_mask,
        0.0, 0.0, 0.0, vx, vy, down_velocity,
        0.0, 0.0, 0.0, 0.0, 0.0)
    sock.sendto(message.pack(mav_cmd), flight.PX4_ADDR)


def local_position(sample, origin):
    return (
        float(sample.get("position_x_m", 0.0)) - origin[0],
        float(sample.get("position_y_m", 0.0)) - origin[1],
        float(sample.get("position_z_m", 0.0)) - origin[2],
    )


def horizontal_hold_velocity(receiver, origin, target_x, target_y, max_speed=0.45):
    sample = receiver.latest or {}
    if not sample:
        return 0.0, 0.0, float("inf")
    x, y, _ = local_position(sample, origin)
    error_x = target_x - x
    error_y = target_y - y
    velocity_x = float(sample.get("velocity_x_mps", 0.0))
    velocity_y = float(sample.get("velocity_y_mps", 0.0))
    # Use stronger velocity damping than the original position-only approach.
    # Long legs can enter the final approach above 0.8 m/s; without enough
    # damping the vehicle crosses the target before the reversed command takes
    # effect and visibly oscillates for several seconds.
    gain = 0.72
    damping = 0.68
    vx = max(-max_speed, min(max_speed, error_x * gain - velocity_x * damping))
    vy = max(-max_speed, min(max_speed, error_y * gain - velocity_y * damping))
    return vx, vy, math.hypot(error_x, error_y)


def stream_position(sock, mav, mav_cmd, sys_id, comp_id, hb, x, y, z, seconds):
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        flight.send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, x, y, z)
        flight.send_hb(sock, mav, hb)
        time.sleep(0.1)


def wait_landed_with_xy_feedback(
        sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
        x, y, touchdown_speed, timeout=32.0):
    print("[*] confirming landed before DISARM with UE XY hold "
          "(touchdown vz=%.2f->%.2fm/s) ..." % (
              touchdown_speed, flight.MAX_TOUCHDOWN_SPEED), flush=True)
    started = time.monotonic()
    stable_ground_since = None
    px4_ground_hint = False
    last_sample = None
    while time.monotonic() - started < timeout:
        elapsed = time.monotonic() - started
        ramp = min(1.0, elapsed / flight.TOUCHDOWN_RAMP_SECONDS)
        down_velocity = touchdown_speed + (
            flight.MAX_TOUCHDOWN_SPEED - touchdown_speed) * ramp
        vx, vy, _ = horizontal_hold_velocity(
            receiver, origin, x, y, max_speed=0.25)
        send_velocity_touchdown_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, down_velocity)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)

        ground_sample = flight.read_posz_from_csv()
        if ground_sample is not None:
            last_sample = ground_sample
            posz, motors = ground_sample
            if abs(posz) < 0.025:
                if stable_ground_since is None:
                    stable_ground_since = time.monotonic()
                elif time.monotonic() - stable_ground_since >= 1.5:
                    hint = ", px4_ground_hint" if px4_ground_hint else ""
                    print("[*] landed stable (csv posz=%.3f motors=%.2f%s)" % (
                        posz, motors, hint), flush=True)
                    return True
            else:
                stable_ground_since = None
        try:
            sock.settimeout(0.15)
            data, _ = sock.recvfrom(4096)
            for message in flight.parse_buf(mav, data):
                if (message.get_type() == "EXTENDED_SYS_STATE" and
                        getattr(message, "landed_state", None) ==
                        flight.MAV.MAV_LANDED_STATE_ON_GROUND):
                    px4_ground_hint = True
        except socket.timeout:
            pass
        time.sleep(0.02)

    if last_sample is None:
        print("[!] landed not confirmed within %.1fs (no UE HIL sample)" % timeout)
    else:
        print("[!] landed not confirmed within %.1fs "
              "(last posz=%.3f motors=%.2f)" % (
                  timeout, last_sample[0], last_sample[1]))
    return False


def land_at(
        sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
        x, y, altitude, touchdown_speed):
    # First remove horizontal velocity and converge to the target using UE
    # ground truth. PX4 does not fuse horizontal EV position in this project,
    # so its position origin can differ from the semantic map origin.
    print("[*] pre-landing XY capture at local=(%.2f, %.2f) ..." % (
        x, y), flush=True)
    settle_started = time.monotonic()
    stable_since = None
    last_print = 0.0
    while time.monotonic() - settle_started < 20.0:
        vx, vy, error = horizontal_hold_velocity(
            receiver, origin, x, y, max_speed=0.65)
        send_velocity_altitude_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, altitude)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)
        sample = receiver.latest or {}
        horizontal_speed = math.hypot(
            float(sample.get("velocity_x_mps", 0.0)),
            float(sample.get("velocity_y_mps", 0.0)))
        elapsed = time.monotonic() - settle_started
        if elapsed - last_print >= 1.0:
            print("[land-capture %4.1fs] error=%.3fm speed=%.3fm/s "
                  "cmd=(%.2f,%.2f)" % (
                      elapsed, error, horizontal_speed, vx, vy), flush=True)
            last_print = elapsed
        if error <= 0.15 and horizontal_speed <= 0.12:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 1.0:
                print("[*] pre-landing XY stable: error=%.3fm speed=%.3fm/s" % (
                    error, horizontal_speed), flush=True)
                break
        else:
            stable_since = None
        time.sleep(0.05)
    else:
        raise RuntimeError(
            "pre-landing XY did not settle within 20s")

    approach_height = min(0.20, max(0.08, altitude * 0.20))
    started = time.monotonic()
    descend_seconds = 10.0
    while time.monotonic() - started < descend_seconds:
        alpha = min(1.0, (time.monotonic() - started) / descend_seconds)
        z_sp = -altitude * (1.0 - alpha) - approach_height * alpha
        vx, vy, _ = horizontal_hold_velocity(receiver, origin, x, y)
        send_velocity_position_z_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, z_sp)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)
        time.sleep(0.05)

    hold_started = time.monotonic()
    while time.monotonic() - hold_started < 1.5:
        vx, vy, _ = horizontal_hold_velocity(receiver, origin, x, y)
        send_velocity_position_z_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, -approach_height)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)
        time.sleep(0.05)

    started = time.monotonic()
    while time.monotonic() - started < 5.0:
        alpha = min(1.0, (time.monotonic() - started) / 5.0)
        z_sp = -approach_height * (1.0 - alpha) + flight.LANDING_SETPOINT_Z * alpha
        vx, vy, _ = horizontal_hold_velocity(
            receiver, origin, x, y, max_speed=0.30)
        send_velocity_position_z_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, z_sp)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)
        time.sleep(0.05)

    landed = wait_landed_with_xy_feedback(
        sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
        x, y, touchdown_speed, timeout=32.0)
    # UE ground contact has already been confirmed above. Force-disarm
    # immediately at that point instead of waiting through a denied normal
    # disarm, which can leave enough time for PX4 battery/GCS failsafes to fire.
    disarmed = (
        flight.disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, force=True)
        if landed else
        flight.retry_clean_disarm(
            sock, mav, mav_cmd, sys_id, comp_id, hb, x, y, attempts=1))
    return landed, disarmed


def write_result(path, result):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)


def load_mission_steps(args, map_data):
    """Load and validate a user-confirmed mission plan.

    Legacy single-goal calls are normalized to the same step model so the
    controller has only one execution path.
    """
    if args.plan:
        # Windows PowerShell 5.1 commonly writes UTF-8 JSON with a BOM.
        # utf-8-sig accepts both BOM and plain UTF-8 mission files.
        with open(args.plan, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        raw_steps = payload.get("steps") if isinstance(payload, dict) else payload
    else:
        raw_steps = [
            {"action": "fly_to", "goal_id": args.goal, "vision": bool(args.vision)},
            {"action": "land"},
        ]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("mission plan must contain a non-empty steps list")

    steps = []
    landed = False
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError("mission step %d must be an object" % (index + 1))
        action = str(raw.get("action", "")).strip().lower()
        if landed:
            raise ValueError("mission step %d appears after land" % (index + 1))
        if action == "fly_to":
            goal_id, _ = resolve_goal(map_data, str(raw.get("goal_id", "")))
            steps.append({
                "action": "fly_to",
                "goal_id": goal_id,
                "vision": bool(raw.get("vision", False)),
            })
        elif action == "hover":
            seconds = float(raw.get("seconds", 3.0))
            if not 0.5 <= seconds <= 30.0:
                raise ValueError("hover seconds must be between 0.5 and 30")
            steps.append({"action": "hover", "seconds": seconds})
        elif action == "land":
            steps.append({"action": "land"})
            landed = True
        else:
            raise ValueError("unsupported mission action %r" % action)

    if not any(step["action"] == "fly_to" for step in steps):
        raise ValueError("mission plan needs at least one fly_to step")
    if steps[-1]["action"] != "land":
        steps.append({"action": "land"})
    return steps


def hover_at(
        sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
        target_x, target_y, altitude, seconds):
    print("[sequence] hover %.1fs at local=(%.2f, %.2f)" % (
        seconds, target_x, target_y), flush=True)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        vx, vy, _ = horizontal_hold_velocity(
            receiver, origin, target_x, target_y, max_speed=0.35)
        send_velocity_altitude_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, altitude)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.03)
        time.sleep(0.04)


def navigate_to_goal(
        sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
        args, map_data, goal_id, goal, require_vision, vision_scanner, result):
    goal_x, goal_y = [float(value) for value in goal["position_m"]]
    expected_label = goal_id
    vision_wait_start = None
    last_vision_request = 0.0
    last_vision_result_time = 0.0
    last_vision_error = None
    last_vision_seen = 0
    last_vision_frame_count = 0
    detected_labels = []
    vision_standoff = None
    vision_confirmed = False
    leg_avoidance_start = result["avoidance_events"]
    # Reuse the continuously updated sample. A blocking 0.5 s read here,
    # immediately after the post-climb read, can exceed COM_OF_LOSS_T and
    # produce a transient OFFBOARD-loss failsafe before the first route.
    initial_sample = receiver.latest or receiver.poll(0.05)
    if not initial_sample:
        raise RuntimeError("cannot plan route without current UE telemetry")
    start_x, start_y, _ = local_position(initial_sample, origin)
    obstacles = map_data.get("obstacles", [])
    direct_path_obstacles = intersected_obstacle_ids(
        (start_x, start_y), (goal_x, goal_y), obstacles)
    route = plan_safe_route(
        (start_x, start_y), (goal_x, goal_y),
        obstacles)
    if route is None:
        raise RuntimeError(
            "no safe map route from (%.2f, %.2f) to %s" %
            (start_x, start_y, goal_id))
    route_points = [
        (float(point[0]), float(point[1]))
        for point in route["points"][1:]
    ]
    waypoint_index = 0
    leg_detours = int(route["detour_waypoints"])
    result["planned_detour_waypoints"] += leg_detours
    print("[route] %s distance=%.2fm clearance=%.2fm detours=%d "
          "direct_obstacles=%s points=%s" % (
        goal_id, route["distance_m"], route["clearance_m"], leg_detours,
        direct_path_obstacles,
        json.dumps(route["points"], ensure_ascii=False)), flush=True)

    print("[sequence] fly_to %s (%s) local=(%.2f, %.2f) vision=%s" % (
        goal_id, goal.get("label", goal_id), goal_x, goal_y,
        require_vision), flush=True)
    started = time.monotonic()
    last_print = 0.0
    avoid_until_x = None
    detour_y = 0.0
    stale_since = None
    arrival_stable_since = None

    while time.monotonic() - started < args.mission_timeout:
        sample = receiver.poll(0.08)
        if not sample or time.monotonic() - receiver.latest_monotonic > 0.8:
            if stale_since is None:
                stale_since = time.monotonic()
            send_velocity_altitude_setpoint(
                sock, mav_cmd, sys_id, comp_id, 0.0, 0.0, args.alt)
            flight.send_hb(sock, mav, hb)
            if time.monotonic() - stale_since > 3.0:
                raise RuntimeError("UE perception telemetry stale for more than 3s")
            time.sleep(0.02)
            continue
        stale_since = None

        x, y, z = local_position(sample, origin)
        dx = goal_x - x
        dy = goal_y - y
        distance = math.hypot(dx, dy)
        horizontal_speed = math.hypot(
            float(sample.get("velocity_x_mps", 0.0)),
            float(sample.get("velocity_y_mps", 0.0)))
        front = float(sample.get("range_front_m", 0.0))
        left = float(sample.get("range_left_m", 0.0))
        right = float(sample.get("range_right_m", 0.0))

        nav_x, nav_y = route_points[waypoint_index]
        is_final_waypoint = waypoint_index == len(route_points) - 1
        nav_distance = math.hypot(nav_x - x, nav_y - y)
        if not is_final_waypoint and nav_distance <= 0.30:
            print("[route] reached detour waypoint %d/%d at (%.2f, %.2f)" % (
                waypoint_index + 1, len(route_points) - 1, x, y), flush=True)
            waypoint_index += 1
            nav_x, nav_y = route_points[waypoint_index]
            is_final_waypoint = waypoint_index == len(route_points) - 1
            nav_distance = math.hypot(nav_x - x, nav_y - y)

        if (is_final_waypoint and require_vision and not vision_confirmed and
                distance <= args.vision_radius):
            if vision_standoff is None:
                ux = dx / distance if distance > 1e-6 else 1.0
                uy = dy / distance if distance > 1e-6 else 0.0
                vision_standoff = (
                    goal_x - ux * args.standoff_m,
                    goal_y - uy * args.standoff_m)
                print("[vision] fixed standoff=(%.2f, %.2f), goal=(%.2f, %.2f)" % (
                    vision_standoff[0], vision_standoff[1],
                    goal_x, goal_y), flush=True)
            nav_x, nav_y = vision_standoff

        if (is_final_waypoint and require_vision and not vision_confirmed and
                distance <= args.vision_radius):
            vision_now = time.monotonic()
            if vision_scanner and vision_now - last_vision_request >= 0.5:
                vision_scanner.request()
                last_vision_request = vision_now
            snapshot = vision_scanner.latest() if vision_scanner else None
            if snapshot and snapshot["completed_monotonic"] > last_vision_result_time:
                last_vision_result_time = snapshot["completed_monotonic"]
                recent_count = snapshot["frame_count"]
                aggregate = snapshot["detections"]
                detected_labels = sorted({
                    item["label"] for item in aggregate
                    if item["confidence"] >= args.vision_min_conf
                })
                last_vision_frame_count = recent_count
                result["vision_last_detections"] = detected_labels
                result["vision_frame_count"] = recent_count
                result["vision_scan_duration_sec"] = round(
                    snapshot["duration_sec"], 3)
                if snapshot["error"]:
                    if snapshot["error"] != last_vision_error:
                        print("[vision] background scan failed: %s" %
                              snapshot["error"], flush=True)
                        last_vision_error = snapshot["error"]
                else:
                    seen = next((
                        item.get("frames_seen", 0) for item in aggregate
                        if item["label"] == expected_label), 0)
                    last_vision_seen = seen
                    if (expected_label in detected_labels and
                            seen >= max(1, recent_count // 2)):
                        vision_confirmed = True
                        print("[*] vision confirmed: %s "
                              "(seen=%d/%d frames, scan=%.3fs)" % (
                                  expected_label, seen, recent_count,
                                  snapshot["duration_sec"]), flush=True)

        if is_final_waypoint and distance <= args.arrival_radius:
            if not require_vision or vision_confirmed:
                # Do not hand a still-drifting vehicle to the next plan step.
                # A tighter gate prevents the landing capture phase from
                # spending many seconds cancelling residual cruise momentum.
                if horizontal_speed <= 0.08:
                    if arrival_stable_since is None:
                        arrival_stable_since = time.monotonic()
                    elif time.monotonic() - arrival_stable_since >= 0.6:
                        print("[sequence] reached %s at local=(%.2f, %.2f) "
                              "error=%.3fm speed=%.3fm/s" % (
                                  goal_id, x, y, distance, horizontal_speed),
                              flush=True)
                        leg_avoidance_events = (
                            result["avoidance_events"] -
                            leg_avoidance_start)
                        avoidance_required = bool(direct_path_obstacles)
                        return {
                            "action": "fly_to",
                            "goal_id": goal_id,
                            "goal_label": goal.get("label", goal_id),
                            "goal_position_m": [goal_x, goal_y],
                            "reached": True,
                            "arrival_error_m": round(distance, 4),
                            "arrival_speed_mps": round(horizontal_speed, 4),
                            "vision_required": bool(require_vision),
                            "vision_confirmed": bool(vision_confirmed),
                            "vision_frames": last_vision_frame_count,
                            "route_points_m": route["points"],
                            "route_distance_m": route["distance_m"],
                            "planned_detour_waypoints": leg_detours,
                            "avoidance_events": leg_avoidance_events,
                            "direct_path_obstacles": direct_path_obstacles,
                            "avoidance_required": avoidance_required,
                            "avoidance_demonstrated": (
                                not avoidance_required or
                                leg_detours >= 1 or
                                leg_avoidance_events >= 1),
                        }
                else:
                    arrival_stable_since = None
            if vision_wait_start is None:
                if require_vision and not vision_confirmed:
                    vision_wait_start = time.monotonic()
                    print("[vision] inside arrival radius, waiting for %s marker..." %
                          expected_label, flush=True)
            elif (require_vision and not vision_confirmed and
                  time.monotonic() - vision_wait_start > args.vision_wait):
                    raise RuntimeError(
                        "vision could not confirm goal %s within %.1fs "
                        "(last seen: %s)" % (
                            expected_label, args.vision_wait, detected_labels))
            if require_vision and not vision_confirmed:
                send_velocity_altitude_setpoint(
                    sock, mav_cmd, sys_id, comp_id, 0.0, 0.0, args.alt)
                flight.send_hb(sock, mav, hb)
                time.sleep(0.02)
                continue
        else:
            arrival_stable_since = None

        # The vehicle yaw now follows travel direction, so the front range fan
        # also faces the direction of motion. Keep the validated sensor detour
        # on forward legs; reverse legs retain the map-planned clear corridor.
        route_dx = nav_x - x
        forward_leg = route_dx >= -0.2
        # A map-planned detour already accounts for every known building and
        # the central block. Do not let the legacy one-axis reactive manoeuvre
        # replace that safe waypoint with a global-Y offset that can cut back
        # through the obstacle. Keep reactive avoidance only for direct legs,
        # and never start it inside the final approach radius.
        allow_reactive_detour = (
            leg_detours == 0 and
            nav_distance > args.trigger_range + 0.5
        )
        if (forward_leg and allow_reactive_detour and
                avoid_until_x is None and front < args.trigger_range):
            avoidance_side = 1 if right >= left else -1
            detour_y = y + avoidance_side * 3.2
            avoid_until_x = x + front + 3.0
            result["avoidance_events"] += 1
            print("[avoid] event=%d front=%.2f left=%.2f right=%.2f "
                  "side=%s detour_y=%.2f until_x=%.2f" % (
                      result["avoidance_events"], front, left, right,
                      "right" if avoidance_side > 0 else "left",
                      detour_y, avoid_until_x), flush=True)

        if forward_leg and avoid_until_x is not None and x < avoid_until_x:
            lateral_error = detour_y - y
            vy = max(-args.avoid_speed, min(args.avoid_speed, lateral_error * 0.9))
            if front <= args.stop_range:
                vx = 0.0
            elif front < args.trigger_range:
                ratio = ((front - args.stop_range) /
                         max(0.1, args.trigger_range - args.stop_range))
                vx = max(0.20, args.speed * max(0.0, min(1.0, ratio)))
            else:
                vx = args.speed
        else:
            if avoid_until_x is not None:
                print("[avoid] obstacle cleared at x=%.2f; returning to route" %
                      x, flush=True)
                avoid_until_x = None
            ndx = nav_x - x
            ndy = nav_y - y
            ndist = math.hypot(ndx, ndy)
            if require_vision and not vision_confirmed and vision_standoff is not None:
                hold_speed = min(0.65, args.speed)
                vx = max(-hold_speed, min(hold_speed, ndx * 0.65))
                vy = max(-hold_speed, min(hold_speed, ndy * 0.65))
            elif ndist < 4.0:
                vx, vy, _ = horizontal_hold_velocity(
                    receiver, origin, nav_x, nav_y,
                    max_speed=min(0.55, args.speed))
            else:
                scale = args.speed / max(ndist, 1e-6)
                vx = ndx * scale
                vy = ndy * scale

        send_velocity_altitude_setpoint(
            sock, mav_cmd, sys_id, comp_id, vx, vy, args.alt)
        flight.send_hb(sock, mav, hb)
        elapsed = time.monotonic() - started
        if elapsed - last_print >= 1.0:
            print("[nav %5.1fs] leg=%s pos=(%6.2f,%6.2f,%4.2f) "
                  "goal_err=%5.2f ranges F/L/R=%4.1f/%4.1f/%4.1f "
                  "cmd=(%4.2f,%4.2f) zone=%s collisions=%s" % (
                      elapsed, goal_id, x, y, z, distance, front, left, right,
                      vx, vy, sample.get("semantic_zone", "transit"),
                      sample.get("obstacle_collision_count", 0)), flush=True)
            last_print = elapsed
        time.sleep(0.02)

    raise RuntimeError(
        "semantic goal %s not reached within %.1fs" % (
            goal_id, args.mission_timeout))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", required=True, help="semantic map JSON")
    parser.add_argument("--goal", default="delivery_zone")
    parser.add_argument("--plan", help="JSON mission plan file")
    parser.add_argument("--instance", type=int, default=1)
    parser.add_argument("--telemetry-port", type=int, default=14660)
    parser.add_argument("--alt", type=float, default=2.0)
    parser.add_argument("--speed", type=float, default=1.25)
    parser.add_argument("--avoid-speed", type=float, default=1.10)
    parser.add_argument("--trigger-range", type=float, default=3.5)
    parser.add_argument("--stop-range", type=float, default=1.2)
    parser.add_argument("--arrival-radius", type=float, default=0.40)
    parser.add_argument("--final-radius", type=float, default=0.50,
                        help="maximum final horizontal landing error for PASS")
    parser.add_argument("--mission-timeout", type=float, default=70.0)
    parser.add_argument("--touchdown-speed", type=float, default=flight.DEFAULT_TOUCHDOWN_SPEED)
    parser.add_argument("--trace")
    parser.add_argument("--result")
    parser.add_argument("--vision", action="store_true",
                        help="require the onboard camera to SEE the correct landmark "
                             "marker before accepting goal arrival (visual semantic loop)")
    parser.add_argument("--camera-dir",
                        default="/mnt/d/SZTUWindFieldPX4Demo/Unreal/Saved/CameraFrames",
                        help="directory of UE-captured frame_*.png (WSL path to Windows Saved)")
    parser.add_argument("--vision-min-conf", type=float, default=0.15,
                        help="minimum detection confidence to accept a landmark")
    parser.add_argument("--vision-wait", type=float, default=10.0,
                        help="seconds to wait for visual confirmation once inside arrival radius")
    parser.add_argument("--vision-radius", type=float, default=8.0,
                        help="start the visual confirmation loop once within this distance of the goal")
    parser.add_argument("--standoff-m", type=float, default=3.5,
                        help="hover this far short of the goal (along the approach line) so the "
                             "forward-down camera keeps the landmark in frame instead of flying "
                             "directly overhead where the body occludes it")
    args = parser.parse_args()

    with open(args.map, "r", encoding="utf-8") as handle:
        map_data = json.load(handle)
    steps = load_mission_steps(args, map_data)
    fly_steps = [step for step in steps if step["action"] == "fly_to"]
    final_goal_id = fly_steps[-1]["goal_id"]
    _, final_goal = resolve_goal(map_data, final_goal_id)
    final_goal_x, final_goal_y = [
        float(value) for value in final_goal["position_m"]]
    vision_requested = any(
        step.get("vision", False) for step in fly_steps)

    vision_module = None
    vision_enabled = vision_requested
    if vision_requested:
        try:
            import vision_detector as vision_module
            print("[*] vision loop enabled; reading frames from %s" %
                  args.camera_dir, flush=True)
        except Exception as exc:
            raise RuntimeError("vision module unavailable: %s" % exc)
        try:
            import shutil
            base = args.camera_dir
            if os.path.isdir(base):
                subdirs = sorted(vision_module.list_run_dirs(base))
                for old in subdirs[:-2]:
                    shutil.rmtree(old, ignore_errors=True)
                if len(subdirs) > 2:
                    print("[vision] cleaned %d old run dir(s), kept %d" % (len(subdirs) - 2, 2), flush=True)
        except Exception as clean_exc:
            print("[vision] run-dir cleanup skipped: %s" % clean_exc, flush=True)
    vision_scanner = (
        VisionScanner(vision_module, args.camera_dir)
        if vision_enabled and vision_module is not None else None)

    flight.PX4_ADDR = ("127.0.0.1", 18570 + args.instance - 1)
    receiver = TelemetryReceiver(args.telemetry_port, args.trace)
    result = {
        "pass": False,
        "map_id": map_data.get("map_id"),
        "goal_id": final_goal_id,
        "goal_label": final_goal.get("label", final_goal_id),
        "goal_position_m": [final_goal_x, final_goal_y],
        "plan_steps": steps,
        "legs": [],
        "reached": False,
        "avoidance_events": 0,
        "planned_detour_waypoints": 0,
        "avoidance_required_legs": 0,
        "avoidance_demonstrated": True,
        "collision_count": None,
        "sensor_samples": 0,
        "landed": False,
        "disarmed": False,
        "final_position_m": None,
        "final_position_error_m": None,
        "vision_enabled": vision_enabled,
        "vision_confirmed": False,
        "vision_last_detections": [],
        "failure": None,
    }
    control = None
    armed = False
    landing_attempted = False
    last_target = (0.0, 0.0)
    origin = (0.0, 0.0, 0.0)

    try:
        initial = receiver.wait_ready()
        origin = (
            float(initial.get("position_x_m", 0.0)),
            float(initial.get("position_y_m", 0.0)),
            float(initial.get("position_z_m", 0.0)),
        )
        print("[sequence] accepted %d steps: %s" % (
            len(steps), json.dumps(steps, ensure_ascii=False)), flush=True)
        print("[*] UE telemetry origin=(%.3f, %.3f, %.3f)" % origin, flush=True)

        control = flight.connect()
        sock, mav, mav_cmd, hb, sys_id, comp_id = control
        print("[*] PX4 sys=%d comp=%d addr=%s" % (sys_id, comp_id, flight.PX4_ADDR), flush=True)

        for _ in range(20):
            flight.send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, 0.0)
            flight.send_hb(sock, mav, hb)
            receiver.poll(0.01)
            time.sleep(0.09)
        if not flight.arm(sock, mav, mav_cmd, sys_id, comp_id, hb):
            raise RuntimeError("PX4 arm failed")
        armed = True

        stream_position(sock, mav, mav_cmd, sys_id, comp_id, hb, 0.0, 0.0, 0.0, 3.0)
        if not flight.wait_offboard(sock, mav, mav_cmd, sys_id, comp_id, hb, timeout=10.0):
            raise RuntimeError("PX4 OFFBOARD mode rejected")

        print("[*] climbing to %.2fm" % args.alt, flush=True)
        stream_position(sock, mav, mav_cmd, sys_id, comp_id, hb, 0.0, 0.0, -args.alt, 5.0)
        send_velocity_altitude_setpoint(
            sock, mav_cmd, sys_id, comp_id, 0.0, 0.0, args.alt)
        flight.send_hb(sock, mav, hb)
        receiver.poll(0.05)

        landed_step_seen = False
        for index, step in enumerate(steps):
            print("[sequence] step %d/%d action=%s" % (
                index + 1, len(steps), step["action"]), flush=True)
            if step["action"] == "fly_to":
                goal_id, goal = resolve_goal(map_data, step["goal_id"])
                leg = navigate_to_goal(
                    sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
                    args, map_data, goal_id, goal, bool(step.get("vision")),
                    vision_scanner, result)
                result["legs"].append(leg)
                last_target = tuple(leg["goal_position_m"])
            elif step["action"] == "hover":
                hover_at(
                    sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
                    last_target[0], last_target[1], args.alt, step["seconds"])
            elif step["action"] == "land":
                landing_attempted = True
                result["landed"], result["disarmed"] = land_at(
                    sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
                    last_target[0], last_target[1],
                    args.alt, args.touchdown_speed)
                armed = not result["disarmed"]
                landed_step_seen = True

        if not landed_step_seen:
            raise RuntimeError("mission plan completed without land step")
        receiver.poll(0.5)

        final_sample = receiver.latest or {}
        if final_sample:
            final_x, final_y, _ = local_position(final_sample, origin)
            result["final_position_m"] = [
                round(final_x, 4), round(final_y, 4)]
            result["final_position_error_m"] = round(
                math.hypot(
                    last_target[0] - final_x,
                    last_target[1] - final_y), 4)
        result["collision_count"] = int(final_sample.get("obstacle_collision_count", 0))
        result["final_semantic_zone"] = final_sample.get("semantic_zone", "unknown")
        result["sensor_samples"] = receiver.samples
        result["reached"] = (
            len(result["legs"]) == len(fly_steps) and
            all(leg.get("reached") for leg in result["legs"]))
        required_vision_legs = [
            leg for leg in result["legs"] if leg.get("vision_required")]
        result["vision_confirmed"] = bool(
            required_vision_legs and
            all(leg.get("vision_confirmed") for leg in required_vision_legs))
        evaluate_mission_result(
            result, args.final_radius, vision_requested)
    except Exception as exc:
        result["failure"] = str(exc)
        print("[!] %s" % exc, flush=True)
        if control and armed and not landing_attempted:
            try:
                sock, mav, mav_cmd, hb, sys_id, comp_id = control
                sample = receiver.latest or {}
                x, y, _ = local_position(sample, origin) if sample else (last_target[0], last_target[1], 0.0)
                result["landed"], result["disarmed"] = land_at(
                    sock, mav, mav_cmd, sys_id, comp_id, hb, receiver, origin,
                    x, y, args.alt, args.touchdown_speed)
            except Exception as landing_exc:
                print("[!] emergency landing failed: %s" % landing_exc, flush=True)
    finally:
        if vision_scanner:
            vision_scanner.close()
        result["sensor_samples"] = receiver.samples
        if result["collision_count"] is None and receiver.latest:
            result["collision_count"] = int(receiver.latest.get("obstacle_collision_count", 0))
        write_result(args.result, result)
        receiver.close()

    print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["pass"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
