#!/usr/bin/env bash
# Run inside the transferred bundle on the offline RHEL workstation.
set -euo pipefail

BUNDLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${1:?Usage: bash install_offline.sh /absolute/path/to/new/venv}"
TARGET_PYTHON="${TARGET_PYTHON:-python3.12}"

if [[ "$INSTALL_DIR" != /* || -e "$INSTALL_DIR" ]]; then
    echo "Choose an absolute path to a new virtual environment." >&2
    exit 1
fi
"$TARGET_PYTHON" -c 'import platform,sys; assert sys.version_info[:3] == (3,12,14); assert platform.system() == "Linux"; assert platform.machine() == "x86_64"'
(
    cd -- "$BUNDLE_DIR"
    sha256sum --check SHA256SUMS
)
"$TARGET_PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/python" -m pip install --no-index --find-links "$BUNDLE_DIR/wheelhouse" --require-hashes -r "$BUNDLE_DIR/runtime.lock"
"$INSTALL_DIR/bin/python" -m pip install --no-index --no-deps "$BUNDLE_DIR"/wheelhouse/cdw_desktop-*.whl
"$INSTALL_DIR/bin/python" "$BUNDLE_DIR/check_environment.py"
echo "Launch from the graphical desktop: $INSTALL_DIR/bin/python -m cdw"
