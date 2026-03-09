#!/usr/bin/env bash
# run_blockify.sh - Main orchestration script for blockify
#
# Usage:
#   ./run_blockify.sh <repo_url> [--include-dir <dir>] [--define KEY=VAL] [--files <pattern>]
#   ./run_blockify.sh <repo_url> --vip          # Process as UVM Verification IP
#
# Examples:
#   ./run_blockify.sh https://github.com/freecores/round_robin_arbiter
#   ./run_blockify.sh https://github.com/freecores/dma_axi --include-dir src/dma_axi32 --files "src/dma_axi32/*.v"
#   ./run_blockify.sh https://github.com/mbits-mirafra/axi4_avip --vip
#
# This script:
#   1. Ensures slang is installed
#   2. Clones the repository
#   3. Finds all Verilog files
#   4. Generates testbenches and metadata for each file
#      (or in --vip mode: generates VIP metadata for the whole repo)
#   5. Reports results

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

SLANG="${ROOT_DIR}/tools/bin/slang"
IMPORT_DIR="${ROOT_DIR}/import"
TB_DIR="${ROOT_DIR}/src/example_tbs"
META_DIR="${ROOT_DIR}/src/example_meta"
OUT_DIR="${ROOT_DIR}/out"

# Parse arguments
REPO_URL=""
INCLUDE_DIRS=()
DEFINES=()
FILE_PATTERN=""
SUBDIR=""
VIP_MODE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --include-dir|-I)
            INCLUDE_DIRS+=("$2")
            shift 2
            ;;
        --define|-D)
            DEFINES+=("$2")
            shift 2
            ;;
        --files)
            FILE_PATTERN="$2"
            shift 2
            ;;
        --subdir)
            SUBDIR="$2"
            shift 2
            ;;
        --vip)
            VIP_MODE=1
            shift
            ;;
        *)
            if [[ -z "$REPO_URL" ]]; then
                REPO_URL="$1"
            else
                echo "ERROR: Unknown argument: $1" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -z "$REPO_URL" ]]; then
    echo "Usage: run_blockify.sh <repo_url> [options]"
    echo ""
    echo "Options:"
    echo "  --include-dir, -I <dir>   Additional include directory (relative to repo root)"
    echo "  --define, -D <KEY=VAL>    Preprocessor define"
    echo "  --files <pattern>         File glob pattern (relative to repo root)"
    echo "  --subdir <dir>            Subdirectory to process (relative to repo root)"
    echo "  --vip                     Process as UVM Verification IP (uses UVM library)"
    exit 1
fi

# Extract repo name
REPO_NAME="$(basename "${REPO_URL}" .git)"
REPO_DIR="${IMPORT_DIR}/${REPO_NAME}"

echo "============================================"
echo "  blockify - RTL Analysis Pipeline"
echo "============================================"
echo "Repository: ${REPO_URL}"
echo "Repo name:  ${REPO_NAME}"
if [[ $VIP_MODE -eq 1 ]]; then
    echo "Mode:       VIP (UVM Verification IP)"
fi
echo ""

# Step 1: Setup slang
echo "--- Step 1: Setup slang ---"
bash "${SCRIPT_DIR}/setup_slang.sh"
echo ""

# Step 2: Clone repository
echo "--- Step 2: Clone repository ---"
bash "${SCRIPT_DIR}/clone_repo.sh" "${REPO_URL}"
echo ""

# ============================================
# VIP mode: generate VIP metadata and exit
# ============================================
if [[ $VIP_MODE -eq 1 ]]; then
    echo "--- Step 3: Setup UVM library ---"
    bash "${SCRIPT_DIR}/setup_uvm.sh"
    echo ""

    UVM_SRC="${IMPORT_DIR}/uvm-core/src"

    echo "--- Step 4: Generate VIP metadata ---"
    VIP_DIR="${REPO_DIR}"
    if [[ -n "$SUBDIR" ]]; then
        VIP_DIR="${REPO_DIR}/${SUBDIR}"
    fi

    RUN_TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
    RUN_DIR="${OUT_DIR}/${REPO_NAME}_${RUN_TIMESTAMP}"
    mkdir -p "${RUN_DIR}"

    META_RESULT=0
    python3 "${SCRIPT_DIR}/generate_vip_meta.py" \
        "${VIP_DIR}" \
        --slang "${SLANG}" \
        --uvm-src "${UVM_SRC}" \
        --output-dir "${META_DIR}" \
        --repo-url "${REPO_URL}" \
        --repo-name "${REPO_NAME}" \
        2>&1 | tee "${RUN_DIR}/vip_meta.log" || META_RESULT=$?

    echo ""
    echo "============================================"
    echo "  VIP Results Summary"
    echo "============================================"
    echo "Repository: ${REPO_NAME}"
    echo "Status: $([ $META_RESULT -eq 0 ] && echo 'PASS' || echo 'FAIL')"
    echo ""
    echo "Metadata:  ${META_DIR}/${REPO_NAME}_vip.json"
    echo "Run logs:  ${RUN_DIR}/"
    echo "============================================"

    {
        echo "Repository: ${REPO_URL}"
        echo "Repo name: ${REPO_NAME}"
        echo "Mode: VIP"
        echo "Run time: ${RUN_TIMESTAMP}"
        echo "Slang version: $("${SLANG}" --version 2>&1)"
        echo ""
        echo "Status: $([ $META_RESULT -eq 0 ] && echo 'PASS' || echo 'FAIL')"
    } > "${RUN_DIR}/summary.txt"

    exit $META_RESULT
fi

# ============================================
# Standard RTL mode: process individual files
# ============================================

# Step 3: Find Verilog files
echo "--- Step 3: Finding Verilog files ---"
SEARCH_DIR="${REPO_DIR}"
if [[ -n "$SUBDIR" ]]; then
    SEARCH_DIR="${REPO_DIR}/${SUBDIR}"
fi

if [[ -n "$FILE_PATTERN" ]]; then
    mapfile -t VERILOG_FILES < <(find "${SEARCH_DIR}" -path "${SEARCH_DIR}/${FILE_PATTERN}" -name "*.v" -o -path "${SEARCH_DIR}/${FILE_PATTERN}" -name "*.sv" | sort)
else
    mapfile -t VERILOG_FILES < <(find "${SEARCH_DIR}" -name "*.v" -o -name "*.sv" | sort)
fi

# Filter out testbench files from the source (we generate our own)
FILTERED_FILES=()
for f in "${VERILOG_FILES[@]}"; do
    fname="$(basename "$f")"
    # Skip files that are testbenches or define-only files
    if [[ "$fname" == tb* ]] || [[ "$fname" == *_tb.v ]] || [[ "$fname" == *_test.v ]]; then
        echo "  Skipping testbench: $fname"
        continue
    fi
    if [[ "$fname" == *_defines.v ]] || [[ "$fname" == *_params.v ]]; then
        echo "  Skipping defines/params-only: $fname"
        continue
    fi
    FILTERED_FILES+=("$f")
done

echo "Found ${#FILTERED_FILES[@]} RTL files to process"
echo ""

# Build include dir arguments
INC_ARGS=()
for inc in "${INCLUDE_DIRS[@]}"; do
    INC_ARGS+=("-I" "${REPO_DIR}/${inc}")
done

# Build define arguments
DEF_ARGS=()
for def in "${DEFINES[@]}"; do
    DEF_ARGS+=("-D" "$def")
done

# Step 4: Process each file
echo "--- Step 4: Processing files ---"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
RESULTS=()

# Create run output directory
RUN_TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
RUN_DIR="${OUT_DIR}/${REPO_NAME}_${RUN_TIMESTAMP}"
mkdir -p "${RUN_DIR}"

for rtl_file in "${FILTERED_FILES[@]}"; do
    fname="$(basename "$rtl_file")"
    echo ""
    echo "=== Processing: ${fname} ==="

    # Check if this is a file that contains module definitions
    if ! grep -q '^\s*module\s' "$rtl_file" 2>/dev/null; then
        echo "  No module definition found, skipping"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        RESULTS+=("SKIP: ${fname} (no module definition)")
        continue
    fi

    # Generate testbench
    echo "  Generating testbench..."
    TB_RESULT=0
    python3 "${SCRIPT_DIR}/generate_tb.py" \
        "$rtl_file" \
        --slang "${SLANG}" \
        --output-dir "${TB_DIR}/${REPO_NAME}" \
        "${INC_ARGS[@]+"${INC_ARGS[@]}"}" \
        "${DEF_ARGS[@]+"${DEF_ARGS[@]}"}" \
        2>&1 | tee "${RUN_DIR}/${fname%.v}_tb.log" || TB_RESULT=$?

    # Generate metadata
    echo "  Generating metadata..."
    META_RESULT=0
    python3 "${SCRIPT_DIR}/generate_meta.py" \
        "$rtl_file" \
        --slang "${SLANG}" \
        --output-dir "${META_DIR}" \
        --repo-url "${REPO_URL}" \
        --repo-name "${REPO_NAME}" \
        "${INC_ARGS[@]+"${INC_ARGS[@]}"}" \
        "${DEF_ARGS[@]+"${DEF_ARGS[@]}"}" \
        2>&1 | tee "${RUN_DIR}/${fname%.v}_meta.log" || META_RESULT=$?

    if [[ $TB_RESULT -eq 0 ]] && [[ $META_RESULT -eq 0 ]]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        RESULTS+=("PASS: ${fname}")
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        RESULTS+=("FAIL: ${fname} (tb=${TB_RESULT}, meta=${META_RESULT})")
    fi
done

# Step 5: Summary
echo ""
echo "============================================"
echo "  Results Summary"
echo "============================================"
echo "Repository: ${REPO_NAME}"
echo "Total files: ${#FILTERED_FILES[@]}"
echo "Passed: ${PASS_COUNT}"
echo "Failed: ${FAIL_COUNT}"
echo "Skipped: ${SKIP_COUNT}"
echo ""
echo "Detailed results:"
for r in "${RESULTS[@]}"; do
    echo "  $r"
done
echo ""
echo "Testbenches: ${TB_DIR}/${REPO_NAME}/"
echo "Metadata:    ${META_DIR}/"
echo "Run logs:    ${RUN_DIR}/"
echo "============================================"

# Write summary to run dir
{
    echo "Repository: ${REPO_URL}"
    echo "Repo name: ${REPO_NAME}"
    echo "Run time: ${RUN_TIMESTAMP}"
    echo "Slang version: $("${SLANG}" --version 2>&1)"
    echo ""
    echo "Total files: ${#FILTERED_FILES[@]}"
    echo "Passed: ${PASS_COUNT}"
    echo "Failed: ${FAIL_COUNT}"
    echo "Skipped: ${SKIP_COUNT}"
    echo ""
    echo "Results:"
    for r in "${RESULTS[@]}"; do
        echo "  $r"
    done
} > "${RUN_DIR}/summary.txt"

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
