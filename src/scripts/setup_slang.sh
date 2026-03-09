#!/usr/bin/env bash
# setup_slang.sh - Downloads and installs precompiled slang from Hdl-tool-compiles
# Usage: ./setup_slang.sh [version] [install_dir]
#   version: slang version tag (default: v10.0)
#   install_dir: directory to install slang into (default: ../../tools)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

VERSION="${1:-v10.0}"
INSTALL_DIR="${2:-$ROOT_DIR/tools}"

REPO_URL="https://github.com/r987r/Hdl-tool-compiles/releases/download"
TARBALL="slang-${VERSION}-linux-x86_64.tar.gz"
DOWNLOAD_URL="${REPO_URL}/slang-${VERSION}/${TARBALL}"

echo "[setup_slang] Version: ${VERSION}"
echo "[setup_slang] Install dir: ${INSTALL_DIR}"

if [ -x "${INSTALL_DIR}/bin/slang" ]; then
    EXISTING_VERSION=$("${INSTALL_DIR}/bin/slang" --version 2>&1 || true)
    echo "[setup_slang] slang already installed: ${EXISTING_VERSION}"
    echo "[setup_slang] To reinstall, remove ${INSTALL_DIR} first"
    exit 0
fi

mkdir -p "${INSTALL_DIR}"
TMPFILE="$(mktemp /tmp/slang-XXXXXX.tar.gz)"

echo "[setup_slang] Downloading ${DOWNLOAD_URL} ..."
curl -fsSL "${DOWNLOAD_URL}" -o "${TMPFILE}"

echo "[setup_slang] Extracting to ${INSTALL_DIR} ..."
tar -xzf "${TMPFILE}" -C "${INSTALL_DIR}"
rm -f "${TMPFILE}"

if [ -x "${INSTALL_DIR}/bin/slang" ]; then
    echo "[setup_slang] Success: $("${INSTALL_DIR}/bin/slang" --version 2>&1)"
else
    echo "[setup_slang] ERROR: slang binary not found after extraction"
    exit 1
fi
