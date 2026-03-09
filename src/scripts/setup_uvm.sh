#!/usr/bin/env bash
# setup_uvm.sh - Clones the Accellera UVM library for use with slang
# Usage: ./setup_uvm.sh [version] [install_dir]
#   version: UVM version tag (default: 2020.3.1)
#   install_dir: directory to clone UVM into (default: ../../import/uvm-core)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

VERSION="${1:-2020.3.1}"
INSTALL_DIR="${2:-$ROOT_DIR/import/uvm-core}"

UVM_REPO="https://github.com/accellera-official/uvm-core.git"

echo "[setup_uvm] Version: ${VERSION}"
echo "[setup_uvm] Install dir: ${INSTALL_DIR}"

if [ -f "${INSTALL_DIR}/src/uvm_pkg.sv" ]; then
    echo "[setup_uvm] UVM already installed at ${INSTALL_DIR}"
    exit 0
fi

mkdir -p "$(dirname "${INSTALL_DIR}")"

echo "[setup_uvm] Cloning UVM ${VERSION} from ${UVM_REPO} ..."
git clone --depth 1 --branch "${VERSION}" "${UVM_REPO}" "${INSTALL_DIR}"

if [ -f "${INSTALL_DIR}/src/uvm_pkg.sv" ]; then
    echo "[setup_uvm] Success: UVM ${VERSION} installed"
    echo "[setup_uvm] UVM source: ${INSTALL_DIR}/src/"
else
    echo "[setup_uvm] ERROR: uvm_pkg.sv not found after clone"
    exit 1
fi
