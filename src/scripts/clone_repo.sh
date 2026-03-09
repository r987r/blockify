#!/usr/bin/env bash
# clone_repo.sh - Clones a GitHub repo into import/ directory
# Usage: ./clone_repo.sh <repo_url> [branch]
#   repo_url: GitHub repository URL (e.g. https://github.com/freecores/dma_axi)
#   branch: optional branch/tag to checkout

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMPORT_DIR="$ROOT_DIR/import"

REPO_URL="${1:?Usage: clone_repo.sh <repo_url> [branch]}"
BRANCH="${2:-}"

# Extract repo name from URL
REPO_NAME="$(basename "${REPO_URL}" .git)"

DEST_DIR="${IMPORT_DIR}/${REPO_NAME}"

echo "[clone_repo] Repository: ${REPO_URL}"
echo "[clone_repo] Destination: ${DEST_DIR}"

if [ -d "${DEST_DIR}" ]; then
    echo "[clone_repo] Already exists: ${DEST_DIR}"
    echo "[clone_repo] Pulling latest..."
    cd "${DEST_DIR}"
    git pull --ff-only 2>/dev/null || echo "[clone_repo] Pull skipped (shallow clone)"
    exit 0
fi

mkdir -p "${IMPORT_DIR}"

CLONE_ARGS=(--depth 1)
if [ -n "${BRANCH}" ]; then
    CLONE_ARGS+=(--branch "${BRANCH}")
fi

echo "[clone_repo] Cloning (depth=1)..."
git clone "${CLONE_ARGS[@]}" "${REPO_URL}" "${DEST_DIR}"

echo "[clone_repo] Done: $(ls "${DEST_DIR}" | wc -l) files/dirs"
