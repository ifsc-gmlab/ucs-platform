#!/bin/bash
set -euo pipefail

export PX4_SIM_MODEL=none_iris
export PX4_SIM_HOST_ADDR="${PX4_SIM_HOST_ADDR:-172.19.208.1}"

PX4_ROOT="${PX4_ROOT:-$HOME/PX4-Autopilot}"
PX4_BIN="$PX4_ROOT/build/px4_sitl_default/bin/px4"
PX4_ROOTFS="$PX4_ROOT/build/px4_sitl_default/rootfs"
FIFO=/tmp/px4_cmd_fifo

cd "$PX4_ROOT"

rm -f "$PX4_ROOTFS/parameters.bson" \
      "$PX4_ROOTFS/parameters_backup.bson" \
      "$PX4_ROOTFS/dataman" \
      /dev/shm/px4* \
      "$FIFO" 2>/dev/null || true

mkfifo "$FIFO"

(
  while true; do
    cat "$FIFO" || sleep 0.1
  done
) | "$PX4_BIN" > /tmp/px4_sitl.log 2>&1 &

PX4_PID=$!

sleep 2
{
  echo "param set CBRK_SUPPLY_CHK 894281"
  echo "param set COM_ARM_WO_GPS 1"
  echo "param set EKF2_MAG_DECL -0.0436"
  echo "param set EKF2_MAG_TYPE 1"
  echo "param set EKF2_GPS_CHECK 3"
  echo "param set EKF2_ABL_LIM 5.0"
  echo "param set EKF2_ABL_ACCLIM 35.0"
  echo "param set EKF2_ACC_NOISE 1.0"
  echo "param set EKF2_ACC_B_NOISE 0.001"
  echo "param set EKF2_BARO_NOISE 1.0"
  echo "param set EKF2_BARO_GATE 10.0"
  echo "param save"
} > "$FIFO"

wait "$PX4_PID"
