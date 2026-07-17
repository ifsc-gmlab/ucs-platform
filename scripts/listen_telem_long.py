import socket, json, time, sys

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 14660))
s.settimeout(2.0)
print(f"listening on 127.0.0.1:14660 for {DURATION:.0f}s ...", flush=True)

t0 = time.time()
n = 0
max_abs_roll = 0.0
max_abs_pitch = 0.0
max_pos_z = -1e9
min_pos_z = 1e9
max_pos_x = -1e9
min_pos_x = 1e9
wind_x_sum = 0.0
wind_x_count = 0
armed_seen = False
last = None
while time.time() - t0 < DURATION:
    try:
        data, _ = s.recvfrom(8192)
    except socket.timeout:
        continue
    try:
        o = json.loads(data.decode('utf-8', 'ignore'))
    except Exception:
        continue
    last = o
    n += 1
    r = o.get('roll_deg', 0.0)
    p = o.get('pitch_deg', 0.0)
    y = o.get('yaw_deg', 0.0)
    px = o.get('position_x_m', 0.0)
    py = o.get('position_y_m', 0.0)
    pz = o.get('position_z_m', 0.0)
    vx = o.get('velocity_x_mps', 0.0)
    vz = o.get('velocity_z_mps', 0.0)
    wind_x = o.get('wind_x_mps', 0.0)
    env = o.get('envelope_exceeded', False)
    reason = o.get('envelope_reason', '')
    max_abs_roll = max(max_abs_roll, abs(r))
    max_abs_pitch = max(max_abs_pitch, abs(p))
    max_pos_z = max(max_pos_z, pz)
    min_pos_z = min(min_pos_z, pz)
    max_pos_x = max(max_pos_x, px)
    min_pos_x = min(min_pos_x, px)
    if 'wind_x_mps' in o:
        wind_x_sum += wind_x
        wind_x_count += 1
    t = o.get('time_s', time.time() - t0)
    mark = ''
    if env:
        mark = f"  !! ENVELOPE: {reason}"
    print(
        f"[{n:3d} t={t:6.1f}] pos_x={px:7.3f}  pos_y={py:7.3f}  pos_z={pz:7.3f}  "
        f"roll={r:7.2f}  pitch={p:7.2f}  yaw={y:7.2f}  vz={vz:7.3f}  "
        f"vx={vx:7.3f}  wind_x={wind_x:7.3f}{mark}",
        flush=True)

print("\n=== SUMMARY ===", flush=True)
print(f"packets      : {n}", flush=True)
print(f"max |roll|   : {max_abs_roll:.2f} deg", flush=True)
print(f"max |pitch|  : {max_abs_pitch:.2f} deg", flush=True)
print(f"pos_x range  : [{min_pos_x:.3f}, {max_pos_x:.3f}] m", flush=True)
print(f"pos_z range  : [{min_pos_z:.3f}, {max_pos_z:.3f}] m", flush=True)
if last:
    print(f"final pos_x  : {last.get('position_x_m',0.0):.3f} m", flush=True)
    print(f"final pos_z  : {last.get('position_z_m',0.0):.3f} m", flush=True)
if wind_x_count:
    print(f"mean wind_x  : {wind_x_sum / wind_x_count:.3f} m/s", flush=True)
print("\nVERDICT:", flush=True)
if max_abs_roll < 55.0 and max_abs_pitch < 55.0 and max_pos_z > 1.0:
    print("  PASS - drone stayed level (no ground roll) and climbed above 1 m.", flush=True)
else:
    print("  CHECK - roll/pitch exceeded 55 deg or never climbed >1 m.", flush=True)
