#!/bin/bash
# Start PX4 SITL-HIL against UE5 on Windows, plus the WSL->Windows MAVLink relay.
#
# Current operating model (2026-07-13):
#   1) Start the MAVLink relay before PX4's foreground pipe; otherwise the
#      relay code never runs because px4 keeps the script in the foreground.
#   2) Run px4 through stdbuf -oL so /tmp/px4_sitl.log is readable in real time.
#   3) Apply the current HIL/EKF parameters that let heading and position settle
#      enough for arm/offboard validation in the UE wind-field map.
#
# 2026-07-21: Added --instance N for dual-drone support.
#   instance N=1 (default): SIM 4560, GCS 18570, QGC relay 14550, lock /tmp/px4_lock-0
#   instance N=2:           SIM 4561, GCS 18571, QGC relay 14551, lock /tmp/px4_lock-1
set -uo pipefail

# Parse --instance N (default 1)
INSTANCE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance)
      INSTANCE="$2"
      shift 2
      ;;
    --instance=*)
      INSTANCE="${1#*=}"
      shift
      ;;
    *)
      shift
      ;;
  esac
done
if ! [[ "$INSTANCE" =~ ^[0-9]+$ ]] || [ "$INSTANCE" -lt 1 ] || [ "$INSTANCE" -gt 4 ]; then
  echo "[launch] invalid --instance: $INSTANCE (expected 1-4)" >&2
  exit 4
fi
INSTANCE_INDEX=$((INSTANCE - 1))  # PX4 uses 0-based index for ports/locks
SIM_PORT=$((4560 + INSTANCE_INDEX))
GCS_PORT=$((18570 + INSTANCE_INDEX))
QGC_RELAY_PORT=$((14550 + INSTANCE_INDEX))
LOCK_FILE="/tmp/px4_lock-${INSTANCE_INDEX}"
SOCK_FILE="/tmp/px4-sock-${INSTANCE_INDEX}"
SITL_LOG="/tmp/px4_sitl_${INSTANCE}.log"
echo "[launch] instance=$INSTANCE (index=$INSTANCE_INDEX) SIM=$SIM_PORT GCS=$GCS_PORT QGC=$QGC_RELAY_PORT"

VEHICLE_TYPE="${SZTU_VEHICLE_TYPE:-quad}"
export PX4_SIM_MODEL=none_iris
case "$VEHICLE_TYPE" in
  quad)
    export PX4_SYS_AUTOSTART=10016
    DEFAULT_THR_HOVER=0.62
    ;;
  hex)
    # 6011 is PX4's POSIX Typhoon H480 Hexa-X airframe. Generic 6001 only
    # exists in the embedded ROMFS airframe directory in this pinned build.
    export PX4_SYS_AUTOSTART=6011
    DEFAULT_THR_HOVER=0.50
    ;;
  rover)
    # 51000_gz_rover_ackermann enables the Gazebo bridge. Use the bundled
    # external-simulator variant instead so simulator_mavlink connects to UE.
    export PX4_SYS_AUTOSTART=51001
    DEFAULT_THR_HOVER=0.50
    ;;
  *)
    echo "[launch] unsupported SZTU_VEHICLE_TYPE=$VEHICLE_TYPE (expected quad, hex, or rover)" >&2
    exit 2
    ;;
esac
echo "[launch] vehicle=$VEHICLE_TYPE SYS_AUTOSTART=$PX4_SYS_AUTOSTART"
if [ -z "${PX4_SIM_HOST_ADDR:-}" ]; then
  PX4_SIM_HOST_ADDR=$(ip route 2>/dev/null | awk '/default/ {print $3; exit}')
  PX4_SIM_HOST_ADDR="${PX4_SIM_HOST_ADDR:-172.19.208.1}"
fi
export PX4_SIM_HOST_ADDR

PX4_ROOT="${PX4_ROOT:-$HOME/PX4-Autopilot}"
PX4_BIN="$PX4_ROOT/build/px4_sitl_default/bin/px4"
# 双机并行：每个 instance 用独立 rootfs（避免 eeprom 冲突）
if [ "$INSTANCE" -gt 1 ]; then
  PX4_ROOTFS="$PX4_ROOT/build/px4_sitl_default/rootfs_instance_${INSTANCE}"
  if [ ! -d "$PX4_ROOTFS" ]; then
    cp -r "$PX4_ROOT/build/px4_sitl_default/rootfs" "$PX4_ROOTFS"
    echo "[launch] created independent rootfs for instance $INSTANCE: $PX4_ROOTFS"
  fi
else
  PX4_ROOTFS="$PX4_ROOT/build/px4_sitl_default/rootfs"
fi
FIFO="/tmp/px4_cmd_fifo_${INSTANCE}"
MPC_Z_P_VALUE="${SZTU_MPC_Z_P:-1.5}"
MPC_Z_VEL_P_ACC_VALUE="${SZTU_MPC_Z_VEL_P_ACC:-3.0}"
MPC_Z_VEL_I_ACC_VALUE="${SZTU_MPC_Z_VEL_I_ACC:-1.5}"
MPC_Z_VEL_D_ACC_VALUE="${SZTU_MPC_Z_VEL_D_ACC:-0.0}"
MPC_THR_HOVER_VALUE="${SZTU_MPC_THR_HOVER:-$DEFAULT_THR_HOVER}"
EKF2_ABL_LIM_VALUE="${SZTU_EKF2_ABL_LIM:-5.0}"
EKF2_EVP_NOISE_VALUE="${SZTU_EKF2_EVP_NOISE:-0.05}"
EKF2_EVV_NOISE_VALUE="${SZTU_EKF2_EVV_NOISE:-0.05}"
EKF2_EVP_GATE_VALUE="${SZTU_EKF2_EVP_GATE:-15.0}"
EKF2_EVV_GATE_VALUE="${SZTU_EKF2_EVV_GATE:-10.0}"
EKF2_GPS_P_NOISE_VALUE="${SZTU_EKF2_GPS_P_NOISE:-0.05}"
EKF2_GPS_V_NOISE_VALUE="${SZTU_EKF2_GPS_V_NOISE:-0.05}"

if [ "$VEHICLE_TYPE" = "rover" ]; then
  ROVER_AIRFRAME_SOURCE="${SZTU_ROVER_AIRFRAME:-$HOME/szefitl/px4_airframes/51001_none_rover_ackermann}"
  ROVER_AIRFRAME_TARGET="$PX4_ROOTFS/etc/init.d-posix/airframes/51001_none_rover_ackermann"
  if [ ! -f "$ROVER_AIRFRAME_SOURCE" ]; then
    echo "[launch] rover airframe missing: $ROVER_AIRFRAME_SOURCE" >&2
    exit 3
  fi
  cp "$ROVER_AIRFRAME_SOURCE" "$ROVER_AIRFRAME_TARGET"
  chmod +x "$ROVER_AIRFRAME_TARGET"
fi

cd "$PX4_ROOT"

# 双机：清理当前 instance 对应的锁文件，不动其他 instance
rm -f "$PX4_ROOTFS/parameters.bson" \
      "$PX4_ROOTFS/parameters_backup.bson" \
      "$PX4_ROOTFS/dataman" \
      /dev/shm/px4*_${INSTANCE} \
      "$LOCK_FILE" "$SOCK_FILE" "$SITL_LOG" \
      "$FIFO" 2>/dev/null || true

mkfifo "$FIFO"
trap "rm -f \"$FIFO\"" EXIT

# Bridge PX4's WSL-local GCS stream to Windows/QGC.
# 双机：每个 instance 转发到不同的 Win 端口（14550 + INSTANCE - 1）
RELAY_SCRIPT="$HOME/szefitl/mavlink_relay.py"
if [ -f "$RELAY_SCRIPT" ]; then
  WIN_IP=$(ip route 2>/dev/null | awk '/default/ {print $3; exit}')
  WIN_IP="${WIN_IP:-172.19.208.1}"
  # 单 instance 时清理旧 relay，多 instance 时不动其他 relay
  if [ "$INSTANCE" -eq 1 ]; then
    pkill -f mavlink_relay.py 2>/dev/null || true
  fi
  QGC_WIN_IP="$WIN_IP" \
  PX4_GCS_PORT="$GCS_PORT" \
  QGC_RELAY_PORT="$QGC_RELAY_PORT" \
  setsid python3 "$RELAY_SCRIPT" > "/tmp/relay_${INSTANCE}.log" 2>&1 < /dev/null &
  echo "[launch] mavlink relay (instance $INSTANCE): $GCS_PORT -> Windows($WIN_IP):$QGC_RELAY_PORT (pid $!)"
fi

{
  if [ "$VEHICLE_TYPE" = "hex" ]; then
    echo "param set PWM_MAIN_FUNC1 101"
    echo "param set PWM_MAIN_FUNC2 102"
    echo "param set PWM_MAIN_FUNC3 103"
    echo "param set PWM_MAIN_FUNC4 104"
    echo "param set PWM_MAIN_FUNC5 105"
    echo "param set PWM_MAIN_FUNC6 106"
    echo "param set PWM_MAIN_FUNC7 0"
    echo "param set PWM_MAIN_FUNC8 0"
  elif [ "$VEHICLE_TYPE" = "rover" ]; then
    echo "param set PWM_MAIN_FUNC1 101"
    echo "param set PWM_MAIN_FUNC2 201"
    echo "param set PWM_MAIN_FUNC3 0"
    echo "param set PWM_MAIN_FUNC4 0"
    echo "param set PWM_MAIN_FUNC5 0"
    echo "param set PWM_MAIN_FUNC6 0"
    echo "param set PWM_MAIN_FUNC7 0"
    echo "param set PWM_MAIN_FUNC8 0"
  else
    echo "param set PWM_MAIN_FUNC1 101"
    echo "param set PWM_MAIN_FUNC2 102"
    echo "param set PWM_MAIN_FUNC3 103"
    echo "param set PWM_MAIN_FUNC4 104"
  fi
  echo "param set CBRK_SUPPLY_CHK 894281"
  echo "param set COM_ARM_WO_GPS 1"
  # UE streams synthetic barometer and magnetometer fields in HIL_SENSOR, but
  # long HIL/QGC sessions can still report intermittent sensor timeouts. Do not
  # make either sensor mandatory for this external-vision/GPS validation setup.
  echo "param set SYS_HAS_BARO 0"
  echo "param set SYS_HAS_MAG 0"
  echo "param set EKF2_MAG_DECL -0.0436"
  echo "param set EKF2_MAG_TYPE 0"
  echo "param set EKF2_GPS_CHECK 0"
  # UE sends exact local-NED ODOMETRY. Fuse its vertical position and 3D
  # velocity (bits 2 + 4), but leave horizontal position to GPS/local origin.
  # Enabling the horizontal EV position bit shifts the OFFBOARD XY reference.
  echo "param set EKF2_EV_CTRL 6"
  echo "param set EKF2_HGT_REF 3"
  echo "param set EKF2_EVP_NOISE $EKF2_EVP_NOISE_VALUE"
  echo "param set EKF2_EVV_NOISE $EKF2_EVV_NOISE_VALUE"
  # UE provides exact transform-derived height. A 5-sigma position gate can
  # reject the real height correction after a transient and create a feedback
  # loop between estimator drift and vertical control. Run 20260716-154119
  # reached a test ratio of 8.73 and rejected EV height 413 times; 15 keeps
  # those corrections fused without weakening the 0.05 m observation weight.
  echo "param set EKF2_EVP_GATE $EKF2_EVP_GATE_VALUE"
  # The default 3-sigma EV velocity gate rejects the short ground-start
  # innovation before correction can converge, causing repeated Z/VZ resets.
  echo "param set EKF2_EVV_GATE $EKF2_EVV_GATE_VALUE"
  # The simulated specific force has a small persistent vertical residual
  # (~0.4-0.5 m/s^2). The EKF must be allowed to estimate up to that level or
  # the "High Accelerometer Bias" preflight check denies ARM. 0.1 was too tight
  # (below the residual -> ARM always rejected); 5.0 let the estimator absorb
  # excessive bias. Keep the validated AirSim-style plant baseline at 5.0, but
  # expose it to the Windows wrappers so a future controlled A/B run can test a
  # tighter limit without editing this launcher.
  echo "param set EKF2_IMU_CTRL 7"
  echo "param set EKF2_ABL_LIM $EKF2_ABL_LIM_VALUE"
  echo "param set EKF2_ABL_ACCLIM 35.0"
  echo "param set EKF2_ACC_NOISE 0.35"
  echo "param set EKF2_ACC_B_NOISE 0.003"
  # HIL_GPS position and velocity are generated directly from the UE transform.
  # Use simulation-appropriate noise floors so they constrain vertical drift.
  echo "param set EKF2_GPS_P_NOISE $EKF2_GPS_P_NOISE_VALUE"
  echo "param set EKF2_GPS_V_NOISE $EKF2_GPS_V_NOISE_VALUE"
  echo "param set EKF2_BARO_NOISE 1.0"
  echo "param set EKF2_BARO_GATE 10.0"
  if [ "$VEHICLE_TYPE" != "rover" ]; then
    # The UE plant uses AirSim-inspired per-rotor RPM lag plus linear drag.
    echo "param set MPC_Z_P $MPC_Z_P_VALUE"
    echo "param set MPC_Z_VEL_P_ACC $MPC_Z_VEL_P_ACC_VALUE"
    echo "param set MPC_Z_VEL_I_ACC $MPC_Z_VEL_I_ACC_VALUE"
    echo "param set MPC_Z_VEL_D_ACC $MPC_Z_VEL_D_ACC_VALUE"
    echo "param set MPC_THR_HOVER $MPC_THR_HOVER_VALUE"
  fi
  if [ "${PX4_QGC_MANUAL_CONTROL:-0}" = "1" ]; then
    echo "param set COM_RC_IN_MODE 1"
  fi
  # Ensure a GCS MAVLink stream on UDP 14550 (rover airframe doesn't create one by default)
  echo "param set MAV_0_MODE 0"
  echo "param save"

  # A FIFO reader exits whenever the current writer closes. Reopen it in a
  # loop so one-shot diagnostics never deliver EOF to the PX4 shell.
  while true; do
    cat "$FIFO" || sleep 0.1
  done
} | stdbuf -oL "$PX4_BIN" -i "$INSTANCE_INDEX" -w "$PX4_ROOTFS" > "$SITL_LOG" 2>&1
