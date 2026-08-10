#!/usr/bin/env python3
"""Arm + OFFBOARD controlled takeoff for PX4 SITL-HIL (UE5).

Run INSIDE WSL (PX4 diag port 127.0.0.1:18570 is local to WSL):
    python3 takeoff_offboard.py [--alt 2.0] [--hold 6] [--climb 4] [--descend 10]

Improvements (2026-07-13):
- After ARM, wait for the EKF/position estimate to settle, then switch to
  OFFBOARD and *retry until OFFBOARD is actually engaged*. Engagement is detected
  via the COMMAND_ACK of MAV_CMD_DO_SET_MODE (result==0): PX4 streams its
  HEARTBEAT/telemetry to the GCS port (14550 -> relay -> QGC), NOT to this script's
  command port (18570), so we cannot read HEARTBEAT.custom_mode directly.
  NOTE: DO_SET_MODE's custom_mode parameter uses PX4_CUSTOM_MAIN_MODE_OFFBOARD (=6),
  which is DIFFERENT from NAVIGATION_STATE_OFFBOARD (=14) shown in the HEARTBEAT.
  Sending 14 makes PX4 reject with "Unsupported main mode"; 6 is the correct command value.
- After the position-controlled descent, ramp a bounded downward velocity and
  use UE HIL ground truth to confirm contact. Force-disarm is allowed only after
  UE confirms the body is on the floor; an airborne timeout remains a failure.
"""
import socket
import time
import argparse
import math
import glob
from pymavlink import mavutil

MAV = mavutil.mavlink
PX4_ADDR = ("127.0.0.1", 18570)

# DO_SET_MODE's custom_mode parameter uses PX4_CUSTOM_MAIN_MODE_OFFBOARD (=6).
# This is DIFFERENT from NAVIGATION_STATE_OFFBOARD (=14) reported in HEARTBEAT.
# Sending 14 -> PX4 rejects with "Unsupported main mode". 6 is the command value.
MODE_OFFBOARD = 6               # PX4_CUSTOM_MAIN_MODE_OFFBOARD (correct for DO_SET_MODE)
MAV_LANDED_STATE_ON_GROUND = 1
LANDING_SETPOINT_Z = 0.10
DEFAULT_TOUCHDOWN_SPEED = 0.12
MAX_TOUCHDOWN_SPEED = 0.25
TOUCHDOWN_RAMP_SECONDS = 12.0


def parse_buf(mav, data):
    try:
        return mav.parse_buffer(data) or []
    except Exception:
        return []


def connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # UDP 14550 belongs exclusively to mavlink_relay.py so QGroundControl keeps
    # receiving PX4 telemetry throughout an autonomous mission.  Binding the
    # controller to the same reused port let it steal datagrams from the relay,
    # which made QGC switch to Comms Lost as soon as step 1 began.
    for p in (51917, 0):
        try:
            sock.bind(("0.0.0.0", p))
            break
        except OSError:
            continue
    sock.settimeout(1.0)
    mav = MAV.MAVLink(None, 1, 1)
    mav_cmd = MAV.MAVLink(None, 1, 190)  # PX4 rejects commands from 1/1
    hb = mav.heartbeat_encode(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, 0)
    sys_id = comp_id = None
    for _ in range(15):
        sock.sendto(hb.pack(mav), PX4_ADDR)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        for msg in parse_buf(mav, data):
            if msg.get_type() == "HEARTBEAT":
                # filter out QGC heartbeat, only accept PX4 heartbeat
                # PX4: type=QUADROTOR(2) autopilot=PX4(12)
                # QGC: type=GCS(6) autopilot=INVALID(0)
                if msg.type == MAV.MAV_TYPE_GCS or msg.autopilot != MAV.MAV_AUTOPILOT_PX4:
                    continue
                sys_id = msg.get_srcSystem()
                comp_id = msg.get_srcComponent()
        if sys_id is None and len(data) >= 7 and data[0] == 0xFD:
            # Fallback: parse raw bytes, but only accept if not QGC sys_id=255
            raw_sys_id = data[5]
            if raw_sys_id != 255:
                sys_id = raw_sys_id
                comp_id = data[6]
        if sys_id:
            break
    if not sys_id:
        raise RuntimeError("no PX4 heartbeat on %s" % (PX4_ADDR,))
    return sock, mav, mav_cmd, hb, sys_id, comp_id


def send_hb(sock, mav, hb):
    sock.sendto(hb.pack(mav), PX4_ADDR)


def is_armed(msg):
    return bool(msg.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED)


def arm(sock, mav, mav_cmd, sys_id, comp_id, hb, timeout=45.0):
    """Send ARM repeatedly (PX4 may deny while EKF/health settles) and return
    True as soon as PX4 reports armed (COMMAND_ACK result==0 or HEARTBEAT armed
    bit). Robust against the previous false-negative: we drain all incoming
    messages continuously instead of a single blocking recv per attempt."""
    print("[*] ARM (PX4 may deny until EKF/health ready) ...")
    cmd = mav_cmd.command_long_encode(sys_id, comp_id, MAV.MAV_CMD_COMPONENT_ARM_DISARM,
                                      0, 1, 0, 0, 0, 0, 0, 0)
    t0 = time.time()
    last_send = -1.0
    while time.time() - t0 < timeout:
        if time.time() - last_send >= 0.5:
            sock.sendto(cmd.pack(mav_cmd), PX4_ADDR)
            send_hb(sock, mav, hb)
            last_send = time.time()
        try:
            sock.settimeout(0.3)
            data, _ = sock.recvfrom(4096)
            for m in parse_buf(mav, data):
                if m.get_type() == "COMMAND_ACK" and m.command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                    print("  ACK result=%s" % m.result)
                    if m.result == 0:
                        return True
                if m.get_type() == "HEARTBEAT" and is_armed(m):
                    return True
        except socket.timeout:
            pass
    return False


def set_mode(sock, mav, mav_cmd, sys_id, comp_id, custom):
    cmd = mav_cmd.command_long_encode(sys_id, comp_id, MAV.MAV_CMD_DO_SET_MODE,
                                      0, MAV.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, custom,
                                      0, 0, 0, 0, 0)
    sock.sendto(cmd.pack(mav_cmd), PX4_ADDR)


def send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, x, y, z):
    # position-only; ignore velocity/accel/yaw/yaw_rate (typemask 3576)
    typemask = 3576
    msg = mav_cmd.set_position_target_local_ned_encode(
        0, sys_id, comp_id, MAV.MAV_FRAME_LOCAL_NED, typemask,
        x, y, z, 0, 0, 0, 0, 0, 0, 0, 0)
    sock.sendto(msg.pack(mav_cmd), PX4_ADDR)


def send_touchdown_setpoint(sock, mav, mav_cmd, sys_id, comp_id, x, y, down_velocity):
    # Hold X/Y position while commanding a small positive NED Z velocity.
    # Ignore PZ, VX/VY, acceleration, yaw and yaw-rate (typemask 3548).
    typemask = 3548
    msg = mav_cmd.set_position_target_local_ned_encode(
        0, sys_id, comp_id, MAV.MAV_FRAME_LOCAL_NED, typemask,
        x, y, 0.0, 0.0, 0.0, down_velocity, 0.0, 0.0, 0.0, 0.0, 0.0)
    sock.sendto(msg.pack(mav_cmd), PX4_ADDR)


def wait_offboard(sock, mav, mav_cmd, sys_id, comp_id, hb, timeout=10.0):
    """Stream setpoints + heartbeat, repeatedly request OFFBOARD, and return
    True once PX4 ACKs MAV_CMD_DO_SET_MODE with result==0 (mode accepted).

    We CANNOT watch HEARTBEAT.custom_mode: PX4 streams its telemetry to the GCS
    port (14550 -> relay -> QGC), not to this script's command port (18570). The
    COMMAND_ACK for our DO_SET_MODE, however, IS echoed back to us, so it is the
    reliable engagement signal. (Verified: custom=6 -> 'ACK cmd=176 result=0'.)"""
    print("[*] requesting OFFBOARD (retrying until DO_SET_MODE ACK result==0) ...")
    t0 = time.time()
    last_req = -1.0
    while time.time() - t0 < timeout:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, 0.0)
        send_hb(sock, mav, hb)
        if time.time() - last_req >= 0.5:
            set_mode(sock, mav, mav_cmd, sys_id, comp_id, MODE_OFFBOARD)
            last_req = time.time()
        try:
            sock.settimeout(0.3)
            data, _ = sock.recvfrom(4096)
            for m in parse_buf(mav, data):
                if m.get_type() == "COMMAND_ACK" and m.command == MAV.MAV_CMD_DO_SET_MODE:
                    if m.result == 0:
                        print("[*] OFFBOARD engaged (DO_SET_MODE ACK result=0)")
                        return True
                    else:
                        print("[!] DO_SET_MODE rejected result=%s, retrying" % m.result)
        except socket.timeout:
            pass
    print("[!] OFFBOARD not engaged within %.1fs" % timeout)
    return False


def read_posz_from_csv():
    """Read the latest posz (world Z) from the UE-side HIL debug CSV. This is an
    out-of-band ground-truth check because PX4's LOCAL_POSITION_NED stream is not
    visible on this script's command port. Returns (posz, motors_avg) or None."""
    import os
    candidates = ["/mnt/c/tmp/hil_debug.csv", "/c/tmp/hil_debug.csv",
                  "C:/tmp/hil_debug.csv", "/tmp/hil_debug.csv"]
    candidates.extend(sorted(glob.glob("/mnt/c/tmp/hil_debug_*.csv"),
                             key=lambda path: os.path.getmtime(path), reverse=True))
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                f.seek(-600, 2)
                tail = f.read().decode(errors="ignore").splitlines()
            last = tail[-1].split(",")
            # columns: t,w,x,y,z,gx,gy,gz,ax,ay,az,m0,m1,m2,m3,posz,...
            posz = float(last[15])
            motors = [float(last[11]), float(last[12]), float(last[13]), float(last[14])]
            return posz, sum(motors) / 4.0
        except Exception:
            continue
    return None


def wait_landed(sock, mav, mav_cmd, sys_id, comp_id, hb, timeout=15.0,
                x=0.0, y=0.0, down_velocity=DEFAULT_TOUCHDOWN_SPEED,
                max_down_velocity=MAX_TOUCHDOWN_SPEED):
    """Continue a bounded downward velocity command until UE confirms ground.

    Position-only control can settle a few centimeters above the floor when the
    PX4 local estimate has a vertical offset after a long hover. A low positive
    NED Z velocity keeps converging toward contact without weakening the ground
    check or allowing an airborne force-disarm.
    """
    print("[*] confirming landed before DISARM (UE HIL posz; touchdown vz=%.2f->%.2fm/s) ..." % (
        down_velocity, max_down_velocity))
    t0 = time.time()
    stable_ground_since = None
    px4_ground_hint = False
    last_sample = None
    while time.time() - t0 < timeout:
        elapsed = time.time() - t0
        ramp = min(1.0, elapsed / TOUCHDOWN_RAMP_SECONDS)
        commanded_down_velocity = down_velocity + (max_down_velocity - down_velocity) * ramp
        send_touchdown_setpoint(
            sock, mav, mav_cmd, sys_id, comp_id, x, y, commanded_down_velocity)
        send_hb(sock, mav, hb)
        sample = read_posz_from_csv()
        if sample is not None:
            last_sample = sample
            posz, motors = sample
            if abs(posz) < 0.025:
                if stable_ground_since is None:
                    stable_ground_since = time.time()
                elif time.time() - stable_ground_since >= 1.5:
                    hint = ", px4_ground_hint" if px4_ground_hint else ""
                    print("[*] landed stable (csv posz=%.3f motors=%.2f%s)" % (posz, motors, hint))
                    return True
            else:
                stable_ground_since = None
        try:
            sock.settimeout(0.3)
            data, _ = sock.recvfrom(4096)
            for m in parse_buf(mav, data):
                if m.get_type() == "EXTENDED_SYS_STATE":
                    if getattr(m, "landed_state", None) == MAV_LANDED_STATE_ON_GROUND:
                        px4_ground_hint = True
        except socket.timeout:
            pass
    if last_sample is None:
        print("[!] landed not confirmed within %.1fs (no UE HIL sample)" % timeout)
    else:
        print("[!] landed not confirmed within %.1fs (last posz=%.3f motors=%.2f)" % (
            timeout, last_sample[0], last_sample[1]))
    return False


def hold_position(sock, mav, mav_cmd, sys_id, comp_id, hb, x, y, z, seconds, label):
    print("[*] %s %.1fs ..." % (label, seconds))
    t0 = time.time()
    while time.time() - t0 < seconds:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, x, y, z)
        send_hb(sock, mav, hb)
        time.sleep(0.1)


def move_position(sock, mav, mav_cmd, sys_id, comp_id, hb,
                  start_x, start_y, end_x, end_y, z, seconds, label):
    print("[*] %s to x=%.2f y=%.2f over %.1fs ..." % (label, end_x, end_y, seconds))
    if seconds <= 0.0:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, end_x, end_y, z)
        return
    t0 = time.time()
    while time.time() - t0 < seconds:
        alpha = min(1.0, max(0.0, (time.time() - t0) / seconds))
        smooth = alpha * alpha * (3.0 - 2.0 * alpha)
        x = start_x + (end_x - start_x) * smooth
        y = start_y + (end_y - start_y) * smooth
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, x, y, z)
        send_hb(sock, mav, hb)
        time.sleep(0.1)


def fly_circle(sock, mav, mav_cmd, sys_id, comp_id, hb,
               radius, laps, duration, entry_seconds, altitude):
    z = -altitude
    move_position(sock, mav, mav_cmd, sys_id, comp_id, hb,
                  0.0, 0.0, radius, 0.0, z, entry_seconds, "entering circle")
    print("[*] CIRCLE START radius=%.2fm laps=%.2f duration=%.1fs" % (radius, laps, duration))
    t0 = time.time()
    while time.time() - t0 < duration:
        alpha = min(1.0, max(0.0, (time.time() - t0) / duration))
        theta = 2.0 * math.pi * laps * alpha
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id,
                      radius * math.cos(theta), radius * math.sin(theta), z)
        send_hb(sock, mav, hb)
        time.sleep(0.1)
    send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, radius, 0.0, z)
    print("[*] CIRCLE COMPLETE")
    move_position(sock, mav, mav_cmd, sys_id, comp_id, hb,
                  radius, 0.0, 0.0, 0.0, z, entry_seconds, "returning to circle center")


def retry_clean_disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, x, y, attempts=4):
    for attempt in range(1, attempts + 1):
        if disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, force=False,
                  ground_x=x, ground_y=y):
            return True
        if attempt < attempts:
            print("[!] clean DISARM denied -> holding on ground before retry %d/%d" % (attempt, attempts))
            hold_position(sock, mav, mav_cmd, sys_id, comp_id, hb, x, y, LANDING_SETPOINT_Z, 1.5, "ground hold")
    return False


def disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, force=False, confirm=True,
           ground_x=None, ground_y=None):
    """Send DISARM. If confirm=True, accept either a successful ARM_DISARM ACK
    or a HEARTBEAT whose armed bit is clear. PX4 can apply the command while the
    corresponding ACK is lost among high-rate HIL traffic. When ground_x/y are
    supplied after UE-confirmed touchdown, keep streaming the same bounded
    touchdown setpoint so OFFBOARD does not time out while PX4 answers."""
    print("[*] DISARM%s ..." % (" (force)" if force else ""))
    # MAV_CMD_COMPONENT_ARM_DISARM uses the magic value 21196 for force disarm.
    # param2=1 is not a force-disarm request and PX4 keeps rejecting it as
    # "not landed" when its landed detector lags the UE-side ground-truth CSV.
    param2 = 21196 if force else 0
    cmd = mav_cmd.command_long_encode(sys_id, comp_id, MAV.MAV_CMD_COMPONENT_ARM_DISARM,
                                      0, 0, param2, 0, 0, 0, 0, 0)
    sock.sendto(cmd.pack(mav_cmd), PX4_ADDR)
    if not confirm:
        return True
    # PX4 echoes the ACK back to this port; result==0 means disarmed.
    t0 = time.time()
    while time.time() - t0 < 3.0:
        if ground_x is not None and ground_y is not None:
            send_touchdown_setpoint(
                sock, mav, mav_cmd, sys_id, comp_id,
                ground_x, ground_y, DEFAULT_TOUCHDOWN_SPEED)
        send_hb(sock, mav, hb)
        try:
            sock.settimeout(0.1)
            data, _ = sock.recvfrom(4096)
            for m in parse_buf(mav, data):
                if m.get_type() == "COMMAND_ACK" and m.command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                    if m.result == 0:
                        print("[*] disarmed (ACK result=0)")
                        return True
                    else:
                        print("[!] disarm ACK result=%s" % m.result)
                        return False
                if m.get_type() == "HEARTBEAT" and not is_armed(m):
                    print("[*] disarmed (HEARTBEAT armed bit cleared)")
                    return True
        except socket.timeout:
            pass
    print("[!] no disarm ACK within 3s")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alt", type=float, default=2.0, help="target altitude (m)")
    ap.add_argument("--hold", type=float, default=6.0, help="hover seconds at altitude")
    ap.add_argument("--climb", type=float, default=4.0, help="climb duration (s)")
    ap.add_argument("--descend", type=float, default=10.0, help="smooth descend duration to low hover (s)")
    ap.add_argument("--touchdown-speed", type=float, default=DEFAULT_TOUCHDOWN_SPEED,
                    help="initial positive NED Z speed used to reach UE ground contact (m/s)")
    ap.add_argument("--settle", type=float, default=3.0,
                    help="seconds to let EKF converge after ARM before OFFBOARD")
    ap.add_argument("--dx", type=float, default=0.0,
                    help="local NED X translation after climb/hover; negative X flies into the +X wind")
    ap.add_argument("--dy", type=float, default=0.0,
                    help="local NED Y translation after climb/hover")
    ap.add_argument("--move", type=float, default=0.0,
                    help="seconds to translate from the origin to dx/dy before descent")
    ap.add_argument("--circle-radius", type=float, default=0.0,
                    help="circle radius in local NED metres; 0 disables circle flight")
    ap.add_argument("--circle-laps", type=float, default=1.0,
                    help="number of circle laps")
    ap.add_argument("--circle-duration", type=float, default=0.0,
                    help="seconds spent on the circle")
    ap.add_argument("--circle-entry", type=float, default=3.0,
                    help="seconds used to enter and leave the circle")
    ap.add_argument("--instance", type=int, default=1,
                    help="PX4 instance id (1=port 18570, 2=18571, ...)")
    args = ap.parse_args()

    # 双机并行：按 --instance 调整 PX4 端口
    global PX4_ADDR
    PX4_ADDR = ("127.0.0.1", 18570 + args.instance - 1)
    print(f"[*] instance={args.instance} → PX4_ADDR={PX4_ADDR}")

    circle_enabled = args.circle_radius > 0.0 or args.circle_duration > 0.0
    if circle_enabled and (args.circle_radius <= 0.0 or args.circle_duration <= 0.0 or args.circle_laps <= 0.0):
        ap.error("circle-radius, circle-duration and circle-laps must all be positive")
    if circle_enabled and (abs(args.dx) > 1e-3 or abs(args.dy) > 1e-3 or args.move > 0.0):
        ap.error("circle flight cannot be combined with dx/dy translation")
    if not 0.03 <= args.touchdown_speed <= MAX_TOUCHDOWN_SPEED:
        ap.error("touchdown-speed must be between 0.03 and %.2f m/s" % MAX_TOUCHDOWN_SPEED)

    sock, mav, mav_cmd, hb, sys_id, comp_id = connect()
    print("[*] PX4 sys=%d comp=%d" % (sys_id, comp_id))

    # Stream initial setpoint (hold at origin) BEFORE arming.
    print("[*] pre-streaming setpoints (hold) ...")
    for _ in range(20):  # ~2s @ 10Hz
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, 0.0)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    if not arm(sock, mav, mav_cmd, sys_id, comp_id, hb):
        print("[!] ARM failed")
        return 2

    # Let EKF / position estimate settle so OFFBOARD is accepted immediately.
    print("[*] ARMED; settling %.1fs for EKF convergence ..." % args.settle)
    t0 = time.time()
    while time.time() - t0 < args.settle:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, 0.0)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    if not wait_offboard(sock, mav, mav_cmd, sys_id, comp_id, hb, timeout=10.0):
        disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, force=True)  # bail in place
        return 3

    print("[*] climbing to %.1f m ..." % args.alt)
    t0 = time.time()
    while time.time() - t0 < args.climb:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, -args.alt)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    print("[*] hovering %.1fs ..." % args.hold)
    t0 = time.time()
    while time.time() - t0 < args.hold:
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, 0.0, 0.0, -args.alt)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    target_x = args.dx
    target_y = args.dy
    if circle_enabled:
        fly_circle(sock, mav, mav_cmd, sys_id, comp_id, hb,
                   args.circle_radius, args.circle_laps, args.circle_duration,
                   args.circle_entry, args.alt)
        target_x = 0.0
        target_y = 0.0
    elif args.move > 0.0 and (abs(target_x) > 1e-3 or abs(target_y) > 1e-3):
        print("[*] translating at altitude to x=%.2f y=%.2f over %.1fs%s ..." % (
            target_x, target_y, args.move,
            " (headwind: wind is +X, command is -X)" if target_x < 0.0 else ""))
        t0 = time.time()
        while time.time() - t0 < args.move:
            alpha = min(1.0, max(0.0, (time.time() - t0) / args.move))
            send_setpoint(sock, mav, mav_cmd, sys_id, comp_id,
                          target_x * alpha, target_y * alpha, -args.alt)
            send_hb(sock, mav, hb)
            time.sleep(0.1)

    approach_height = min(0.20, max(0.05, args.alt * 0.20))
    print("[*] descending smoothly to %.2fm over %.1fs ..." % (approach_height, args.descend))
    t0 = time.time()
    while time.time() - t0 < args.descend:
        alpha = min(1.0, max(0.0, (time.time() - t0) / args.descend))
        z_sp = -args.alt * (1.0 - alpha) - approach_height * alpha
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, target_x, target_y, z_sp)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    hold_position(sock, mav, mav_cmd, sys_id, comp_id, hb, target_x, target_y, -approach_height, 1.5, "settling at low hover")

    touchdown_seconds = 5.0
    print("[*] final soft touchdown over %.1fs (ending %.2fm below ground reference) ..." % (
        touchdown_seconds, LANDING_SETPOINT_Z))
    t0 = time.time()
    while time.time() - t0 < touchdown_seconds:
        alpha = min(1.0, max(0.0, (time.time() - t0) / touchdown_seconds))
        z_sp = -approach_height * (1.0 - alpha) + LANDING_SETPOINT_Z * alpha
        send_setpoint(sock, mav, mav_cmd, sys_id, comp_id, target_x, target_y, z_sp)
        send_hb(sock, mav, hb)
        time.sleep(0.1)

    landed = wait_landed(
        sock, mav, mav_cmd, sys_id, comp_id, hb,
        timeout=32.0, x=target_x, y=target_y,
        down_velocity=args.touchdown_speed,
        max_down_velocity=MAX_TOUCHDOWN_SPEED)

    # Prefer a clean DISARM. If PX4's landed detector still lags, force-disarm
    # only after UE ground contact has been confirmed, so the visual body is not
    # cut loose while still hovering.
    print("[*] DISARM (clean first; force only after confirmed ground contact) ...")
    if not retry_clean_disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, target_x, target_y, attempts=1):
        if landed:
            print("[!] clean DISARM still denied at confirmed ground contact -> force disarm")
            disarm(sock, mav, mav_cmd, sys_id, comp_id, hb, force=True,
                   ground_x=target_x, ground_y=target_y)
        else:
            print("[!] skipping force DISARM because UE ground contact was not confirmed")
            return 4
    print("[*] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
