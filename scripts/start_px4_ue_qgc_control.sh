#!/bin/bash
# Enable QGC joystick/manual-control input, then start the normal UE HIL stack.
set -euo pipefail

export PX4_QGC_MANUAL_CONTROL=1
exec "$HOME/szefitl/start_px4_ue_hil.sh"
