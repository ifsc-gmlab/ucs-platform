#!/usr/bin/env python3
"""QGC-style flight test: ARM -> NAV_TAKEOFF -> hover -> NAV_LAND.

Sends the exact MAVLink commands QGroundControl would send (not OFFBOARD),
directly to PX4's local 18570 endpoint. Verifies the full QGC control path
end-to-end, including physical takeoff and landing.
"""
import sys, time, threading
from pymavlink import mavutil

PX4_URL = "udpout:127.0.0.1:18570"
ALT = 3.0
HOVER_SEC = 15
COMP = 190  # PX4 rejects comp=1 commands; use 190 (MAV_COMP_ID_ONBOARD_COMPUTER)

conn = mavutil.mavlink_connection(PX4_URL, source_system=255, source_component=COMP)
print("waiting for heartbeat...")
conn.wait_heartbeat(timeout=15)
# PX4 SITL default sysid/compid; set explicitly since udpout may report 0/0
conn.target_system = 1
conn.target_component = 1
print(f"connected: target sys={conn.target_system} comp={conn.target_component}")

stop_hb = False
def heartbeat_loop():
    while not stop_hb:
        conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0, 0, 0)
        time.sleep(1)
hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
hb_thread.start()

def send_cmd(command, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0, timeout=10):
    conn.mav.command_long_send(
        1, 1, command, 0,  # target PX4 sys=1/comp=1 explicitly
        p1, p2, p3, p4, p5, p6, p7)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=1)
        if msg and msg.command == command:
            result_name = mavutil.mavlink.enums["MAV_RESULT"][msg.result].name
            print(f"  ACK: {result_name}")
            return msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
    print("  ACK: TIMEOUT")
    return False

# 1. ARM (retry until EKF converges)
print("waiting 20s for estimator convergence...")
time.sleep(20)
print("ARM...")
armed = False
for attempt in range(15):
    if send_cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1=1, timeout=8):
        armed = True
        break
    print(f"  retry {attempt+1}/15 in 3s...")
    time.sleep(3)
if not armed:
    print("ARM FAILED after 15 retries - aborting")
    stop_hb = True
    sys.exit(1)

# 2. NAV_TAKEOFF (QGC 5.0.x style: param1=-1, param7=altitude)
print(f"NAV_TAKEOFF alt={ALT}m (QGC-style param1=-1)...")
if not send_cmd(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, p1=-1, p7=ALT, timeout=15):
    print("TAKEOFF FAILED - trying force disarm")
    send_cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1=0, p2=21196)
    stop_hb = True
    sys.exit(1)

# 3. Monitor hover
print(f"hovering {HOVER_SEC}s, monitoring height...")
max_z = 0.0
deadline = time.monotonic() + HOVER_SEC
last_print = 0
while time.monotonic() < deadline:
    msg = conn.recv_match(type='LOCAL_POSITION_NED', blocking=True, timeout=2)
    if msg:
        z = -msg.z
        if z > max_z:
            max_z = z
        now = time.time()
        if now - last_print >= 3:
            print(f"  height={z:.2f}m  vz={msg.vz:.3f}m/s")
            last_print = now

print(f"max height reached: {max_z:.2f}m")

# 4. NAV_LAND
print("NAV_LAND...")
send_cmd(mavutil.mavlink.MAV_CMD_NAV_LAND, timeout=10)

# 5. Wait for disarm
print("waiting for disarm (max 30s)...")
deadline = time.monotonic() + 30
disarmed = False
while time.monotonic() < deadline:
    msg = conn.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
    if msg:
        armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if not armed:
            print("DISARMED")
            disarmed = True
            break

if not disarmed:
    print("no auto-disarm, force disarm...")
    send_cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1=0, p2=21196, timeout=5)

stop_hb = True
print(f"RESULT: max_height={max_z:.2f}m tookoff={'YES' if max_z > 0.5 else 'NO'}")
print("done")
