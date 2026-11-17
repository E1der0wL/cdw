#!/usr/bin/env bash
# Run on a connected RHEL 8.10 x86_64 build machine with Python 3.12.14.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_DIR="${1:-$PROJECT_DIR/dist/offline-rhel8}"
BUILD_PYTHON="${BUILD_PYTHON:-python3.12}"
BUILD_ENV="$BUNDLE_DIR/build-env"
WHEELHOUSE="$BUNDLE_DIR/wheelhouse"
RUNTIME_LOCK="$PROJECT_DIR/requirements/runtime.lock"

if [[ -e "$BUNDLE_DIR" ]]; then
    echo "Choose a new bundle directory; existing directories are not overwritten." >&2
    exit 1
fi
"$BUILD_PYTHON" -c 'import platform,sys; assert sys.version_info[:3] == (3,12,14); assert platform.system() == "Linux"; assert platform.machine() == "x86_64"; assert platform.libc_ver() == ("glibc","2.28")'
mkdir -p -- "$WHEELHOUSE"
"$BUILD_PYTHON" -m venv "$BUILD_ENV"
# Read build requirements from the authoritative project metadata.
mapfile -t BUILD_REQUIREMENTS < <("$BUILD_PYTHON" -c 'import sys,tomllib; print("\n".join(tomllib.load(open(sys.argv[1],"rb"))["build-system"]["requires"]))' "$PROJECT_DIR/pyproject.toml")
"$BUILD_ENV/bin/python" -m pip install "${BUILD_REQUIREMENTS[@]}"
"$BUILD_ENV/bin/python" -m pip download --only-binary=:all: --require-hashes -r "$RUNTIME_LOCK" -d "$WHEELHOUSE"
"$BUILD_ENV/bin/python" -m pip wheel --no-deps --no-build-isolation --wheel-dir "$WHEELHOUSE" "$PROJECT_DIR"
cp -- "$RUNTIME_LOCK" "$BUNDLE_DIR/runtime.lock"
cp -- "$PROJECT_DIR/scripts/install_offline.sh" "$PROJECT_DIR/scripts/check_environment.py" "$BUNDLE_DIR/"
cp -- "$PROJECT_DIR/README.md" "$BUNDLE_DIR/"
mkdir -p -- "$BUNDLE_DIR/docs"
cp -- "$PROJECT_DIR/docs/external-task-protocol.md" "$BUNDLE_DIR/docs/"
(
    cd -- "$BUNDLE_DIR"
    sha256sum wheelhouse/*.whl runtime.lock install_offline.sh check_environment.py README.md docs/external-task-protocol.md > SHA256SUMS
)
echo "Bundle created at $BUNDLE_DIR. Transfer wheelhouse, docs, runtime.lock, install_offline.sh, check_environment.py, README.md and SHA256SUMS."
