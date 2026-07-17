#!/usr/bin/env python3
r"""Force-disarm PX4 SITL-HIL over the local WSL MAVLink command port.

Run inside WSL while PX4 is active:
    python3 px4_force_disarm.py

Windows wrapper:
    powershell -NoProfile -ExecutionPolicy Bypass -File outputs\px4_emergency_stop.ps1
"""
import socket
import time
from pymavlink import mavutil

MAV = mavutil.mavlink
PX4_ADDR = ("127.0.0.1", 18570)
FORCE_MAGIC = 21196


def parse_buf(mav, data):
    try:
        return mav.parse_buffer(data) or []
    except Exception:
        return []


def connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for port in (14550, 51917, 0):
        try:
            sock.bind(("0.0.0.0", port))
            break
        except OSError:
            continue
    sock.settimeout(0.5)

    mav = MAV.MAVLink(None, 1, 1)
    mav_cmd = MAV.MAVLink(None, 1, 190)
    hb = mav.heartbeat_encode(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, 0)

    sys_id = comp_id = None
    for _ in range(20):
        sock.sendto(hb.pack(mav), PX4_ADDR)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        for msg in parse_buf(mav, data):
            if (
                msg.get_type() == "HEARTBEAT"
                and getattr(msg, "type", None) != MAV.MAV_TYPE_GCS
                and getattr(msg, "autopilot", None) != MAV.MAV_AUTOPILOT_INVALID
            ):
                sys_id = msg.get_srcSystem()
                comp_id = msg.get_srcComponent()
                break
        if sys_id:
            return sock, mav, mav_cmd, hb, sys_id, comp_id
    raise RuntimeError("no PX4 heartbeat on 127.0.0.1:18570")


def main():
    sock, mav, mav_cmd, hb, sys_id, comp_id = connect()
    print(f"PX4 sys={sys_id} comp={comp_id}")

    cmd = mav_cmd.command_long_encode(
        sys_id,
        comp_id,
        MAV.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        0,            # disarm
        FORCE_MAGIC,  # force
        0, 0, 0, 0, 0,
    )

    deadline = time.time() + 5.0
    last_send = 0.0
    while time.time() < deadline:
        if time.time() - last_send > 0.5:
            sock.sendto(cmd.pack(mav_cmd), PX4_ADDR)
            sock.sendto(hb.pack(mav), PX4_ADDR)
            last_send = time.time()
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        for msg in parse_buf(mav, data):
            if msg.get_type() == "COMMAND_ACK" and msg.command == MAV.MAV_CMD_COMPONENT_ARM_DISARM:
                print(f"ARM_DISARM ACK result={msg.result}")
                if msg.result == 0:
                    print("FORCE DISARM OK")
                    return 0
                return 2
            if msg.get_type() == "HEARTBEAT" and not (msg.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED):
                print("PX4 already disarmed")
                return 0

    print("no disarm ACK within 5s")
    return 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
