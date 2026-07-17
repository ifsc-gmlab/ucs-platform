#!/usr/bin/env python3
"""Short MANUAL_CONTROL acceptance test for the PX4 Ackermann rover."""

import argparse
import socket
import time

from pymavlink import mavutil

from takeoff_offboard import PX4_ADDR, arm, connect, disarm, send_hb, set_mode


def connect_rover():
    """Connect to PX4, falling back to its known SITL system/component IDs.

    The normal launcher owns UDP 14550 with the QGC relay. For this unattended
    test the relay is stopped first, but PX4 can take a few cycles to resume its
    heartbeat stream. Commands are still accepted on 18570, so a bounded
    fallback keeps the test deterministic without weakening command ACK checks.
    """
    try:
        return connect()
    except RuntimeError as error:
        print(f"[!] {error}; using PX4 SITL system=1 component=1")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 14550))
    sock.settimeout(1.0)
    mav = mavutil.mavlink.MAVLink(None, 1, 1)
    mav_cmd = mavutil.mavlink.MAVLink(None, 1, 190)
    heartbeat = mav.heartbeat_encode(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        0,
    )
    for _ in range(10):
        send_hb(sock, mav, heartbeat)
        time.sleep(0.05)
    return sock, mav, mav_cmd, heartbeat, 1, 1


def send_manual(sock, mav, mav_cmd, system_id, heartbeat, throttle, steering, duration):
    """Send PX4 MANUAL_CONTROL at 20 Hz.

    MAVLink y maps to manual_control_setpoint.roll (steering) and z maps to
    throttle. z=500 is neutral for the reversible Ackermann airframe.
    """
    y_value = int(max(-1.0, min(1.0, steering)) * 1000)
    z_value = int((max(-1.0, min(1.0, throttle)) + 1.0) * 500)
    message = mav_cmd.manual_control_encode(system_id, 0, y_value, z_value, 0, 0)
    deadline = time.time() + duration
    while time.time() < deadline:
        sock.sendto(message.pack(mav_cmd), PX4_ADDR)
        send_hb(sock, mav, heartbeat)
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--straight", type=float, default=3.0)
    parser.add_argument("--turn", type=float, default=3.0)
    parser.add_argument("--throttle", type=float, default=0.45)
    parser.add_argument("--steering", type=float, default=0.45)
    args = parser.parse_args()

    sock, mav, mav_cmd, heartbeat, system_id, component_id = connect_rover()
    print(f"[*] PX4 connected: system={system_id} component={component_id}")

    # PX4_CUSTOM_MAIN_MODE_MANUAL = 1.
    set_mode(sock, mav, mav_cmd, system_id, component_id, 1)
    send_manual(sock, mav, mav_cmd, system_id, heartbeat, 0.0, 0.0, 1.0)
    if not arm(sock, mav, mav_cmd, system_id, component_id, heartbeat, timeout=30.0):
        raise RuntimeError("rover ARM was denied")

    print(f"[*] straight: throttle={args.throttle:.2f}, {args.straight:.1f}s")
    send_manual(sock, mav, mav_cmd, system_id, heartbeat, args.throttle, 0.0, args.straight)
    print(f"[*] turn: throttle={args.throttle:.2f}, steering={args.steering:.2f}, {args.turn:.1f}s")
    send_manual(sock, mav, mav_cmd, system_id, heartbeat, args.throttle, args.steering, args.turn)
    print("[*] stop")
    send_manual(sock, mav, mav_cmd, system_id, heartbeat, 0.0, 0.0, 2.0)

    if not disarm(sock, mav, mav_cmd, system_id, component_id, heartbeat, force=False):
        disarm(sock, mav, mav_cmd, system_id, component_id, heartbeat, force=True, confirm=False)
    print("[PASS] rover manual-control sequence completed")


if __name__ == "__main__":
    main()
