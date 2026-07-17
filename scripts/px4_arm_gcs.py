#!/usr/bin/env python3
import argparse
import socket
import time

from pymavlink import mavutil


MAV = mavutil.mavlink
PX4_ADDR = ("127.0.0.1", 18570)


def parse_messages(mav, data):
    try:
        return mav.parse_buffer(data) or []
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect", type=float, default=8.0)
    parser.add_argument("--arm", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for port in (14550, 51917, 0):
        try:
            sock.bind(("0.0.0.0", port))
            break
        except OSError:
            continue
    sock.settimeout(1.0)

    # This PX4 SITL port replies reliably after a 1/1 heartbeat, but rejects
    # COMMAND_LONG if the command itself is also from 1/1. Use 1/1 only to
    # register the UDP remote, then pack arm commands as component 190.
    mav = MAV.MAVLink(None, 1, 1)
    mav_cmd = MAV.MAVLink(None, 1, 190)
    heartbeat = mav.heartbeat_encode(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, 0)
    print(f"[1] local={sock.getsockname()[1]} hb=1/1 cmd=1/190 -> PX4 {PX4_ADDR}")

    px4_sys = None
    px4_comp = None
    seen_status = []
    seen_est = None
    seen_heartbeat = None

    for _ in range(5):
        sock.sendto(heartbeat.pack(mav), PX4_ADDR)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        for msg in parse_messages(mav, data):
            if msg.get_type() == "HEARTBEAT":
                px4_sys = msg.get_srcSystem()
                px4_comp = msg.get_srcComponent()
                seen_heartbeat = msg
        if px4_sys is None and len(data) >= 7 and data[0] == 0xFD:
            px4_sys = data[5]
            px4_comp = data[6]
        if px4_sys:
            break

    if not px4_sys:
        print("[!] no PX4 heartbeat")
        return 2

    print(f"[2] PX4 sys={px4_sys} comp={px4_comp}")
    print(f"[3] collecting {args.collect:.1f}s...")
    start = time.time()
    while time.time() - start < args.collect:
        sock.sendto(heartbeat.pack(mav), PX4_ADDR)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        for msg in parse_messages(mav, data):
            typ = msg.get_type()
            if typ == "HEARTBEAT":
                seen_heartbeat = msg
            elif typ == "ESTIMATOR_STATUS":
                seen_est = msg
            elif typ == "STATUSTEXT":
                text = msg.text.decode("utf-8", "ignore") if isinstance(msg.text, bytes) else msg.text
                seen_status.append(text.strip())

    if seen_est:
        print("=== ESTIMATOR_STATUS ===")
        for name in ["vel_ratio", "pos_horiz_ratio", "pos_vert_ratio", "mag_ratio", "hagl_ratio", "tas_ratio"]:
            if hasattr(seen_est, name):
                print(f"  {name}={getattr(seen_est, name):.4f}")
        if hasattr(seen_est, "gps_check_fail_flags"):
            print(f"  gps_check_fail_flags=0x{seen_est.gps_check_fail_flags:X}")
        if hasattr(seen_est, "innovation_check_flags"):
            print(f"  innovation_check_flags=0x{seen_est.innovation_check_flags:X}")

    if seen_status:
        print("=== recent STATUSTEXT ===")
        for line in seen_status[-12:]:
            print(f"  {line}")

    if seen_heartbeat:
        armed = bool(seen_heartbeat.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED)
        print(f"  armed={armed} mode={seen_heartbeat.custom_mode}")

    if args.arm:
        print("[4] ARM attempt as command source 1/190...")
        arm = mav_cmd.command_long_encode(px4_sys, px4_comp, MAV.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
        for _ in range(8):
            sock.sendto(arm.pack(mav_cmd), PX4_ADDR)
            deadline = time.time() + 1.0
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    break
                for msg in parse_messages(mav, data):
                    typ = msg.get_type()
                    if typ == "COMMAND_ACK":
                        print(f"  ACK command={msg.command} result={msg.result}")
                    elif typ == "STATUSTEXT":
                        text = msg.text.decode("utf-8", "ignore") if isinstance(msg.text, bytes) else msg.text
                        print(f"  {text.strip()}")
                    elif typ == "HEARTBEAT":
                        armed = bool(msg.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED)
                        print(f"  armed={armed}")
                        if armed:
                            print(">>> ARMED <<<")
                            return 0
            time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
