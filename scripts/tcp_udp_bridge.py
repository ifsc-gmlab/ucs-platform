#!/usr/bin/env python3
"""TCP-UDP bridge: QGC(TCP localhost:14550) <-> PX4(UDP 127.0.0.1:18570).

WSL2's localhost forwarding supports TCP but NOT UDP. This bridge lets
QGC on Windows connect via TCP (which WSL2 forwards) to PX4's UDP MAVLink.
"""
import socket
import select
import sys
import os

PX4_UDP_HOST = "127.0.0.1"
PX4_UDP_PORT = int(os.environ.get("PX4_GCS_PORT", "18570"))
TCP_LISTEN_PORT = int(os.environ.get("QGC_RELAY_PORT", "14550"))

def main():
    # TCP listener (WSL2 localhost forwarding makes this accessible from Windows)
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_sock.bind(("0.0.0.0", TCP_LISTEN_PORT))
    tcp_sock.listen(5)
    print(f"[bridge] TCP listening on 0.0.0.0:{TCP_LISTEN_PORT} -> PX4 UDP {PX4_UDP_HOST}:{PX4_UDP_PORT}", flush=True)

    # UDP socket to PX4 — bind to 14550 so PX4 sends responses HERE
    # (PX4's MAV_0 sends to remote port 14550). SO_REUSEADDR lets us share
    # with the old relay if it's still dying.
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        udp_sock.bind(("0.0.0.0", TCP_LISTEN_PORT))
        print(f"[bridge] UDP bound to 0.0.0.0:{TCP_LISTEN_PORT} (receiving PX4 data)", flush=True)
    except OSError as e:
        print(f"[bridge] WARNING: UDP bind to {TCP_LISTEN_PORT} failed ({e}), using random port", flush=True)
    udp_sock.setblocking(False)

    clients = {}  # tcp_sock -> udp_addr

    while True:
        rlist = [tcp_sock, udp_sock] + list(clients.keys())
        readable, _, _ = select.select(rlist, [], [], 1.0)

        for s in readable:
            if s is tcp_sock:
                # New TCP connection from QGC
                conn, addr = tcp_sock.accept()
                conn.setblocking(False)
                clients[conn] = None
                print(f"[bridge] QGC connected from {addr}", flush=True)

            elif s is udp_sock:
                # Data from PX4 (UDP) -> forward to all TCP clients
                try:
                    data, px4_addr = udp_sock.recvfrom(65535)
                    px4_peer = px4_addr
                    for conn in list(clients.keys()):
                        try:
                            conn.sendall(data)
                        except Exception:
                            conn.close()
                            del clients[conn]
                except BlockingIOError:
                    pass

            elif s in clients:
                # Data from QGC (TCP) -> forward to PX4 (UDP)
                try:
                    data = s.recv(65535)
                    if data:
                        udp_sock.sendto(data, (PX4_UDP_HOST, PX4_UDP_PORT))
                        clients[s] = True  # mark as active
                    else:
                        # QGC disconnected
                        print(f"[bridge] QGC disconnected", flush=True)
                        s.close()
                        del clients[s]
                except BlockingIOError:
                    pass
                except Exception:
                    s.close()
                    del clients[s]

        # Log periodically
        if clients:
            sys.stdout.flush()

if __name__ == "__main__":
    main()
