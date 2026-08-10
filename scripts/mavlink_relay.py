#!/usr/bin/env python3
"""Bridge PX4's WSL-local GCS MAVLink stream to Windows QGroundControl.

Single-instance relay: one process per PX4 SITL instance. For dual-drone,
run two processes with different env vars:
  instance 1: PX4_GCS_PORT=18570 QGC_RELAY_PORT=14550 python3 mavlink_relay.py
  instance 2: PX4_GCS_PORT=18571 QGC_RELAY_PORT=14551 python3 mavlink_relay.py
"""

import os
import socket
import struct


# WSL 端 listen 端口 = QGC_RELAY_PORT (默认 14550)
# Windows QGC 把消息发到 WSL_IP:QGC_RELAY_PORT；relay 转发到 PX4_GCS_PORT
# PX4 把 GCS 消息发到 127.0.0.1:PX4_GCS_PORT；relay 转发到 Windows:QGC_WIN_PORT
LISTEN_PORT = int(os.environ.get("QGC_RELAY_PORT", "14550"))
PX4_ADDR = ("127.0.0.1", int(os.environ.get("PX4_GCS_PORT", "18570")))
WIN_IP = os.environ.get("QGC_WIN_IP", "172.19.208.1")
WIN_ADDR = (WIN_IP, int(os.environ.get("QGC_WIN_PORT", os.environ.get("QGC_RELAY_PORT", "14550"))))
# Keep the autonomous controller on a separate local port.  PX4's MAV_0
# stream targets the relay port, so duplicate PX4 telemetry instead of making
# QGC and the controller share (and race on) UDP 14550.
CONTROLLER_ADDR = (
    "127.0.0.1",
    int(os.environ.get("CONTROLLER_RELAY_PORT", "51917")),
)

COMMAND_NAMES = {
    22: "MAV_CMD_NAV_TAKEOFF",
    400: "MAV_CMD_COMPONENT_ARM_DISARM",
}
ACK_RESULT_NAMES = {
    0: "ACCEPTED",
    1: "TEMPORARILY_REJECTED",
    2: "DENIED",
    3: "UNSUPPORTED",
    4: "FAILED",
    5: "IN_PROGRESS",
    6: "CANCELLED",
}


def iter_mavlink_messages(data):
    """Yield (message_id, payload) for MAVLink v1/v2 frames in a datagram."""
    offset = 0
    while offset < len(data):
        magic = data[offset]
        if magic == 0xFD and offset + 10 <= len(data):
            payload_len = data[offset + 1]
            signature_len = 13 if data[offset + 2] & 0x01 else 0
            frame_len = 10 + payload_len + 2 + signature_len
            if offset + frame_len <= len(data):
                message_id = int.from_bytes(data[offset + 7 : offset + 10], "little")
                yield message_id, data[offset + 10 : offset + 10 + payload_len]
                offset += frame_len
                continue
        elif magic == 0xFE and offset + 6 <= len(data):
            payload_len = data[offset + 1]
            frame_len = 6 + payload_len + 2
            if offset + frame_len <= len(data):
                yield data[offset + 5], data[offset + 6 : offset + 6 + payload_len]
                offset += frame_len
                continue
        offset += 1


def describe_control_message(message_id, payload):
    if message_id == 76 and len(payload) >= 30:  # COMMAND_LONG
        # MAVLink 2 truncates trailing zero fields, including confirmation.
        padded = payload.ljust(33, b"\x00")
        params = struct.unpack_from("<7f", padded)
        command = struct.unpack_from("<H", padded, 28)[0]
        name = COMMAND_NAMES.get(command, str(command))
        return (
            f"COMMAND_LONG {name} target={padded[30]}/{padded[31]} "
            f"confirm={padded[32]} p1={params[0]:.3f} p2={params[1]:.3f} "
            f"p5={params[4]:.7f} p6={params[5]:.7f} p7={params[6]:.3f}"
        )

    if message_id == 75 and len(payload) >= 22:  # COMMAND_INT
        padded = payload.ljust(27, b"\x00")
        command = struct.unpack_from("<H", padded, 20)[0]
        name = COMMAND_NAMES.get(command, str(command))
        z = struct.unpack_from("<f", padded, 16)[0]
        return (
            f"COMMAND_INT {name} target={padded[22]}/{padded[23]} "
            f"frame={padded[24]} x={struct.unpack_from('<i', padded, 8)[0]} "
            f"y={struct.unpack_from('<i', padded, 12)[0]} z={z:.3f}"
        )

    if message_id == 77 and len(payload) >= 3:  # COMMAND_ACK
        command = struct.unpack_from("<H", payload)[0]
        name = COMMAND_NAMES.get(command, str(command))
        result = payload[2]
        return f"COMMAND_ACK {name} result={ACK_RESULT_NAMES.get(result, result)}"

    return None


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[relay] listening on 0.0.0.0:{LISTEN_PORT}", flush=True)
    print(
        f"[relay] PX4 {PX4_ADDR} -> QGC {WIN_ADDR} + controller {CONTROLLER_ADDR}",
        flush=True,
    )

    px4_packets = 0
    qgc_packets = 0
    while True:
        data, addr = sock.recvfrom(65535)
        from_qgc = not addr[0].startswith("127.")
        if not from_qgc:
            px4_packets += 1
            sock.sendto(data, WIN_ADDR)
            sock.sendto(data, CONTROLLER_ADDR)
        else:
            qgc_packets += 1
            sock.sendto(data, PX4_ADDR)

        direction = "QGC->PX4" if from_qgc else "PX4->QGC"
        for message_id, payload in iter_mavlink_messages(data):
            description = describe_control_message(message_id, payload)
            if description:
                print(f"[relay] {direction} {description}", flush=True)

        total = px4_packets + qgc_packets
        if total == 1 or total % 5000 == 0 or (from_qgc and qgc_packets == 1):
            print(
                f"[relay] packets px4_to_qgc={px4_packets} "
                f"qgc_to_px4={qgc_packets} last_source={addr[0]}:{addr[1]}",
                flush=True,
            )


if __name__ == "__main__":
    main()
