#!/usr/bin/env bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

export AGNOS_VERSION="19.9"

export STAGING_ROOT="/data/safe_staging"

# Boot images follow the explicit physical profile, never a branch label.
profile_value="${SUNNYPILOT_HARDWARE_PROFILE:-}"
profile_file="${SUNNYPILOT_HARDWARE_PROFILE_FILE:-/data/hardware_profile}"
if [ -z "$profile_value" ] && [ -f "$profile_file" ]; then
  profile_value="$(cat "$profile_file")" || exit 1
  [ -n "$profile_value" ] || { echo "Empty hardware profile: $profile_file" >&2; exit 1; }
fi
case "$profile_value" in
  ""|standard|c3xl) ;;
  *) echo "Unsupported hardware profile: $profile_value" >&2; exit 1 ;;
esac
export AGNOS_MANIFEST_FILE="openpilot/common/hardware/comma/agnos.json"
if [ "$profile_value" = "c3xl" ]; then
  export AGNOS_MANIFEST_FILE="openpilot/common/hardware/comma/agnos-c3xl.json"
elif [ -r "${SUNNYPILOT_HARDWARE_MODEL_FILE:-/sys/firmware/devicetree/base/model}" ] && \
     [ "$(tr -d '\000' < "${SUNNYPILOT_HARDWARE_MODEL_FILE:-/sys/firmware/devicetree/base/model}")" = "comma tici" ]; then
  export AGNOS_MANIFEST_FILE="openpilot/common/hardware/comma/agnos-c3.json"
fi

SP_SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SP_SOURCE_ROOT:$SP_SOURCE_ROOT/opendbc_repo:$SP_SOURCE_ROOT/msgq_repo:$SP_SOURCE_ROOT/tinygrad_repo:$SP_SOURCE_ROOT/rednose_repo${PYTHONPATH:+:$PYTHONPATH}"
