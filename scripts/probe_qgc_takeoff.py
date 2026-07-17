#!/usr/bin/env python3
"""Probe QGC-style PX4 takeoff command acceptance without arming."""

from __future__ import annotations

import argparse
import os
import re
import socket
import time

from pymavlink import mavutil


ACK_RE = re.compile(
    r"PX4->QGC COMMAND_ACK MAV_CMD_NAV_TAKEOFF result=([A-Z_]+)"
)


def default_relay_endpoint() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        probe.connect(("192.0.2.1", 9))
        return f"udpout:{probe.getsockname()[0]}:14550"
    finally:
        probe.close()


def read_new_text(path: str, offset: int) -> tuple[str, int]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read(), handle.tell()
    except FileNotFoundError:
        return "", offset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send QGC 5.0.x's NAV_TAKEOFF parameters without an arm command."
    )
    parser.add_argument("--endpoint", default=default_relay_endpoint())
    parser.add_argument("--relay-log", default="/tmp/relay.log")
    parser.add_argument("--altitude", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    offset = os.path.getsize(args.relay_log) if os.path.exists(args.relay_log) else 0
    connection = mavutil.mavlink_connection(
        args.endpoint,
        source_system=255,
        source_component=mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER,
    )
    nan = float("nan")

    # This exactly covers the compatibility failure. No ARM_DISARM command is sent.
    connection.mav.command_long_send(
        1,
        mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0,
        -1.0,
        0.0,
        0.0,
        nan,
        nan,
        nan,
        args.altitude,
    )
    connection.close()

    deadline = time.monotonic() + args.timeout
    captured = ""

    while time.monotonic() < deadline:
        chunk, offset = read_new_text(args.relay_log, offset)
        captured += chunk
        match = ACK_RE.search(captured)

        if match:
            result = match.group(1)
            print(f"NAV_TAKEOFF_ACK={result}")
            print("ARM_COMMAND_SENT=False")
            return 0 if result == "ACCEPTED" else 1

        time.sleep(0.1)

    print("NAV_TAKEOFF_ACK=TIMEOUT")
    print("ARM_COMMAND_SENT=False")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
