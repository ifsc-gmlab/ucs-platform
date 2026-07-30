#!/bin/bash
# Enable QGC joystick/manual-control input, then start the normal UE HIL stack.
set -euo pipefail

export PX4_QGC_MANUAL_CONTROL=1
# QGC sessions remain open longer than automated OFFBOARD runs. Use wider
# simulation-only innovation noise/gates so small UE HIL timing jitter does not
# collapse the short Ready-to-Fly window before the guided takeoff is accepted.
export SZTU_EKF2_EVP_NOISE="${SZTU_EKF2_EVP_NOISE:-0.5}"
export SZTU_EKF2_EVV_NOISE="${SZTU_EKF2_EVV_NOISE:-0.5}"
export SZTU_EKF2_EVP_GATE="${SZTU_EKF2_EVP_GATE:-100.0}"
export SZTU_EKF2_EVV_GATE="${SZTU_EKF2_EVV_GATE:-100.0}"
export SZTU_EKF2_GPS_P_NOISE="${SZTU_EKF2_GPS_P_NOISE:-0.5}"
export SZTU_EKF2_GPS_V_NOISE="${SZTU_EKF2_GPS_V_NOISE:-0.5}"
exec "$HOME/szefitl/start_px4_ue_hil.sh" "$@"
