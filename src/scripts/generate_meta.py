#!/usr/bin/env python3
"""
generate_meta.py - Generates comprehensive metadata JSON for a Verilog/SystemVerilog module.

Usage:
    python3 generate_meta.py <rtl_file> --slang <slang_path> --output-dir <dir>
        --repo-url <url> [--include-dir <dir>] [--defines KEY=VAL ...]

Metadata includes:
  - Module interface (ports, parameters, types, widths)
  - Internal signals, registers, nets
  - Procedural blocks (always, initial, etc.)
  - FSM detection
  - Combinatorial vs sequential logic
  - Defines/ifdefs in module
  - Source repo tracking info
  - Compilation status
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_slang_ast(slang_path, rtl_file, include_dirs=None, defines=None):
    """Run slang to get AST JSON for a file."""
    import tempfile
    fd, ast_file = tempfile.mkstemp(suffix=".json")

    cmd = [slang_path, str(rtl_file), "--ast-json", ast_file, "--allow-use-before-declare"]

    if include_dirs:
        for inc in include_dirs:
            cmd.extend(["-I", str(inc)])

    if defines:
        for d in defines:
            cmd.extend(["-D", d])

    result = subprocess.run(cmd, capture_output=True, text=True)
    os.close(fd)

    if os.path.exists(ast_file):
        with open(ast_file) as f:
            ast = json.load(f)
        os.unlink(ast_file)
        return ast, result.stdout + result.stderr, result.returncode
    else:
        return None, result.stdout + result.stderr, result.returncode


def run_slang_lint(slang_path, rtl_file, include_dirs=None, defines=None):
    """Run slang lint-only check."""
    cmd = [slang_path, str(rtl_file), "--lint-only", "--allow-use-before-declare"]

    if include_dirs:
        for inc in include_dirs:
            cmd.extend(["-I", str(inc)])

    if defines:
        for d in defines:
            cmd.extend(["-D", d])

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def parse_width_from_type(type_str):
    """Parse the bit width from a type string."""
    match = re.search(r'\[(\d+):(\d+)\]', type_str)
    if match:
        high = int(match.group(1))
        low = int(match.group(2))
        return abs(high - low) + 1
    return 1


def parse_range_from_type(type_str):
    """Parse [high:low] range from type string."""
    match = re.search(r'\[(\d+):(\d+)\]', type_str)
    if match:
        return [int(match.group(1)), int(match.group(2))]
    return None


def detect_fsm_candidates(variables, procedural_blocks_info):
    """Detect potential FSM state registers heuristically."""
    fsm_candidates = []
    state_keywords = ["state", "fsm", "cs", "ns", "current_state", "next_state",
                      "st_", "_st", "phase", "mode"]

    for var in variables:
        name_lower = var["name"].lower()
        if any(kw in name_lower for kw in state_keywords):
            fsm_candidates.append({
                "signal": var["name"],
                "type": var["type"],
                "width": parse_width_from_type(var["type"]),
                "reason": "name matches FSM pattern"
            })

    return fsm_candidates


def scan_file_for_directives(rtl_file):
    """Scan the RTL file for preprocessor directives."""
    directives = {
        "defines": [],
        "ifdefs": [],
        "includes": [],
        "timescale": None,
    }

    try:
        with open(rtl_file) as f:
            content = f.read()
    except Exception:
        return directives

    # Find `define
    for match in re.finditer(r'`define\s+(\w+)(?:\s+(.*))?', content):
        directives["defines"].append({
            "name": match.group(1),
            "value": match.group(2).strip() if match.group(2) else None
        })

    # Find `ifdef / `ifndef / `elsif
    for match in re.finditer(r'`(ifdef|ifndef|elsif)\s+(\w+)', content):
        directives["ifdefs"].append({
            "directive": match.group(1),
            "macro": match.group(2)
        })

    # Find `include
    for match in re.finditer(r'`include\s+"([^"]+)"', content):
        directives["includes"].append(match.group(1))

    # Find `timescale
    match = re.search(r'`timescale\s+(.+)', content)
    if match:
        directives["timescale"] = match.group(1).strip()

    return directives


def analyze_procedural_block(block):
    """Analyze a procedural block to determine if it's combinatorial or sequential."""
    proc_kind = block.get("procedureKind", "")
    body = block.get("body", {})
    timing = body.get("timing", {})
    timing_kind = timing.get("kind", "")

    info = {
        "kind": proc_kind,
        "is_combinatorial": False,
        "is_sequential": False,
        "sensitivity": "unknown",
        "clock": None,
        "reset": None,
    }

    if proc_kind == "AlwaysComb":
        info["is_combinatorial"] = True
        info["sensitivity"] = "combinatorial"
    elif proc_kind == "AlwaysFF":
        info["is_sequential"] = True
        info["sensitivity"] = "sequential"
    elif proc_kind == "Always":
        if timing_kind == "ImplicitEvent":
            info["is_combinatorial"] = True
            info["sensitivity"] = "implicit (combinatorial)"
        elif timing_kind == "SignalEvent":
            # Check for clock edge
            info["is_sequential"] = True
            info["sensitivity"] = "explicit edge"
        elif timing_kind == "EventList":
            # Analyze the event list for edges
            events = timing.get("events", [])
            has_edge = False
            for event in events:
                edge = event.get("edge", "")
                if edge in ["PosEdge", "NegEdge"]:
                    has_edge = True
                    signal_name = ""
                    expr = event.get("expr", {})
                    if expr.get("kind") == "NamedValue":
                        signal_name = expr.get("symbol", "").split()[-1]

                    if any(ck in signal_name.lower() for ck in ["clk", "clock"]):
                        info["clock"] = signal_name
                    elif any(rs in signal_name.lower() for rs in ["rst", "reset", "rstn"]):
                        info["reset"] = signal_name

            if has_edge:
                info["is_sequential"] = True
                info["sensitivity"] = "edge-triggered"
            else:
                info["is_combinatorial"] = True
                info["sensitivity"] = "level-sensitive"

    return info


def extract_module_metadata(ast, rtl_file, repo_url, include_dirs, defines,
                            compile_ok, compile_output, slang_version):
    """Extract comprehensive metadata from slang AST."""
    design = ast.get("design", {})
    definitions = ast.get("definitions", [])
    modules_meta = []

    for member in design.get("members", []):
        if member.get("kind") != "Instance":
            continue

        body = member.get("body", {})
        if body.get("kind") != "InstanceBody":
            continue

        mod_name = body["name"]

        # Find definition info
        defn_info = {}
        for defn in definitions:
            if defn.get("name") == mod_name:
                defn_info = defn
                break

        # Extract ports
        ports = []
        for m in body.get("members", []):
            if m.get("kind") == "Port":
                port_type = m.get("type", "")
                ports.append({
                    "name": m["name"],
                    "direction": m.get("direction", ""),
                    "type": port_type,
                    "width": parse_width_from_type(port_type),
                    "range": parse_range_from_type(port_type),
                })

        # Extract parameters
        parameters = []
        for m in body.get("members", []):
            if m.get("kind") == "Parameter":
                parameters.append({
                    "name": m["name"],
                    "type": m.get("type", ""),
                    "default_value": str(m.get("value", "")),
                    "is_local": m.get("isLocal", False),
                })

        # Extract internal variables (registers)
        variables = []
        for m in body.get("members", []):
            if m.get("kind") == "Variable":
                var_type = m.get("type", "")
                variables.append({
                    "name": m["name"],
                    "type": var_type,
                    "width": parse_width_from_type(var_type),
                    "range": parse_range_from_type(var_type),
                    "lifetime": m.get("lifetime", ""),
                })

        # Extract nets (wires)
        nets = []
        for m in body.get("members", []):
            if m.get("kind") == "Net":
                net_type = m.get("type", "")
                # Skip nets that are also ports (they appear as both)
                port_names = {p["name"] for p in ports}
                if m["name"] not in port_names:
                    nets.append({
                        "name": m["name"],
                        "type": net_type,
                        "width": parse_width_from_type(net_type),
                        "range": parse_range_from_type(net_type),
                    })

        # Analyze procedural blocks
        procedural_blocks = []
        for m in body.get("members", []):
            if m.get("kind") == "ProceduralBlock":
                block_info = analyze_procedural_block(m)
                procedural_blocks.append(block_info)

        # Count combinatorial vs sequential
        n_comb = sum(1 for b in procedural_blocks if b["is_combinatorial"])
        n_seq = sum(1 for b in procedural_blocks if b["is_sequential"])

        # Detect FSM candidates
        fsm_candidates = detect_fsm_candidates(variables, procedural_blocks)

        # Extract generate blocks
        generate_blocks = []
        for m in body.get("members", []):
            if m.get("kind") in ["GenerateBlock", "GenerateBlockArray"]:
                generate_blocks.append({
                    "kind": m.get("kind", ""),
                    "name": m.get("name", ""),
                })

        # Extract instances (submodule instantiations)
        instances = []
        for m in body.get("members", []):
            if m.get("kind") == "Instance":
                inst_body = m.get("body", {})
                instances.append({
                    "instance_name": m.get("name", ""),
                    "module_name": inst_body.get("name", ""),
                })

        # Scan for preprocessor directives
        directives = scan_file_for_directives(str(rtl_file))

        # Determine clocks and resets from ports
        clocks = [p["name"] for p in ports
                  if p["direction"] == "In" and
                  any(ck in p["name"].lower() for ck in ["clk", "clock"])]
        resets = [p["name"] for p in ports
                  if p["direction"] == "In" and
                  any(rs in p["name"].lower() for rs in ["rst", "reset", "rstn"])]

        # Build module metadata
        module_meta = {
            "module_name": mod_name,
            "definition_kind": defn_info.get("definitionKind", "Module"),
            "interface": {
                "ports": ports,
                "parameters": parameters,
                "total_input_bits": sum(p["width"] for p in ports if p["direction"] == "In"),
                "total_output_bits": sum(p["width"] for p in ports if p["direction"] == "Out"),
                "num_inputs": sum(1 for p in ports if p["direction"] == "In"),
                "num_outputs": sum(1 for p in ports if p["direction"] == "Out"),
                "num_inouts": sum(1 for p in ports if p["direction"] == "InOut"),
            },
            "internals": {
                "variables": variables,
                "nets": nets,
                "num_registers": len(variables),
                "num_wires": len(nets),
            },
            "logic_analysis": {
                "procedural_blocks": procedural_blocks,
                "num_combinatorial_blocks": n_comb,
                "num_sequential_blocks": n_seq,
                "clocks": clocks,
                "resets": resets,
            },
            "hierarchy": {
                "instances": instances,
                "generate_blocks": generate_blocks,
                "num_submodules": len(instances),
                "is_leaf": len(instances) == 0,
            },
            "fsm_analysis": {
                "candidates": fsm_candidates,
                "has_potential_fsm": len(fsm_candidates) > 0,
            },
            "preprocessor": directives,
        }

        modules_meta.append(module_meta)

    return modules_meta


def get_git_info(repo_dir):
    """Get git information for a repository."""
    info = {
        "remote_url": "",
        "commit_sha": "",
        "branch": "",
    }

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            info["remote_url"] = result.stdout.strip()

        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            info["commit_sha"] = result.stdout.strip()

        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            info["branch"] = result.stdout.strip()
    except Exception:
        pass

    return info


def main():
    parser = argparse.ArgumentParser(description="Generate metadata for Verilog module")
    parser.add_argument("rtl_file", help="Path to Verilog/SystemVerilog source file")
    parser.add_argument("--slang", required=True, help="Path to slang binary")
    parser.add_argument("--output-dir", required=True, help="Output directory for metadata")
    parser.add_argument("--repo-url", default="", help="Source repository URL")
    parser.add_argument("--repo-name", default="", help="Source repository name")
    parser.add_argument("--include-dir", "-I", action="append", default=[], help="Include directories")
    parser.add_argument("--define", "-D", action="append", default=[], help="Preprocessor defines")
    args = parser.parse_args()

    rtl_file = Path(args.rtl_file).resolve()
    output_dir = Path(args.output_dir).resolve()
    slang_path = Path(args.slang).resolve()

    if not rtl_file.exists():
        print(f"ERROR: RTL file not found: {rtl_file}", file=sys.stderr)
        sys.exit(1)

    # Get include dirs
    include_dirs = [str(rtl_file.parent)] + [str(Path(d).resolve()) for d in args.include_dir]

    # Get slang version
    result = subprocess.run([str(slang_path), "--version"], capture_output=True, text=True)
    slang_version = result.stdout.strip() if result.returncode == 0 else "unknown"

    # Run lint check
    print(f"[generate_meta] Linting {rtl_file.name}...")
    compile_ok, compile_output = run_slang_lint(str(slang_path), str(rtl_file),
                                                 include_dirs, args.define)
    print(f"[generate_meta] Lint: {'PASS' if compile_ok else 'FAIL'}")

    # Run AST extraction
    print(f"[generate_meta] Parsing AST for {rtl_file.name}...")
    ast, ast_output, ast_rc = run_slang_ast(str(slang_path), str(rtl_file),
                                             include_dirs, args.define)

    if ast is None:
        print(f"[generate_meta] ERROR: slang failed to parse {rtl_file.name}")
        print(ast_output)

        # Still generate a metadata file with failure info
        output_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "blockify_version": "0.1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "file": rtl_file.name,
                "file_path": str(rtl_file),
                "repo_url": args.repo_url,
                "repo_name": args.repo_name,
            },
            "compilation": {
                "status": "FAIL",
                "slang_version": slang_version,
                "output": compile_output,
                "defines_used": args.define,
                "include_dirs": include_dirs,
            },
            "modules": [],
        }

        repo_name = args.repo_name or "unknown"
        filename = f"{repo_name}_{rtl_file.stem}.json"
        meta_file = output_dir / filename
        meta_file.write_text(json.dumps(meta, indent=2))
        print(f"[generate_meta] Generated (with failure): {meta_file}")
        sys.exit(1)

    # Extract module metadata
    modules_meta = extract_module_metadata(
        ast, rtl_file, args.repo_url, include_dirs, args.define,
        compile_ok, compile_output, slang_version
    )

    # Get git info from the file's repo
    git_info = get_git_info(rtl_file.parent)

    # Build final metadata
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "blockify_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "file": rtl_file.name,
            "file_path": str(rtl_file),
            "repo_url": args.repo_url or git_info["remote_url"],
            "repo_name": args.repo_name,
            "commit_sha": git_info["commit_sha"],
            "branch": git_info["branch"],
        },
        "compilation": {
            "status": "PASS" if compile_ok else "FAIL",
            "slang_version": slang_version,
            "output": compile_output,
            "defines_used": args.define,
            "include_dirs_used": [str(d) for d in include_dirs],
        },
        "modules": modules_meta,
    }

    repo_name = args.repo_name or "unknown"
    filename = f"{repo_name}_{rtl_file.stem}.json"
    meta_file = output_dir / filename
    meta_file.write_text(json.dumps(meta, indent=2))
    print(f"[generate_meta] Generated: {meta_file}")


if __name__ == "__main__":
    main()
