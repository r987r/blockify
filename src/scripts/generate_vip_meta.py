#!/usr/bin/env python3
"""
generate_vip_meta.py - Generates comprehensive metadata JSON for UVM Verification IP (VIP) repositories.

Usage:
    python3 generate_vip_meta.py <vip_dir> --slang <slang_path> --output-dir <dir>
        --repo-url <url> [--repo-name <name>]

Uses slang with the UVM library to compile and parse the VIP, extracting:
  - SystemVerilog interface definitions (signals, modports, clocking blocks)
  - BFM (Bus Functional Model) information (ports, tasks, sub-instances)
  - UVM component classification (agent, driver, monitor, sequencer, etc.)
  - Package definitions (parameters, typedefs, enums, structs)
  - TLM port connections and virtual interface usage
  - VIP architecture and component hierarchy

Designed for VIPs following UVM methodology (e.g. mbits-mirafra avip repos).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# File classification helpers
# ---------------------------------------------------------------------------

ROLE_PATTERNS = [
    (r"_if\.sv$",                   "interface"),
    (r"_driver_bfm\.sv$",          "driver_bfm"),
    (r"_monitor_bfm\.sv$",         "monitor_bfm"),
    (r"_agent_bfm\.sv$",           "agent_bfm"),
    (r"_driver_proxy\.sv$",        "driver_proxy"),
    (r"_monitor_proxy\.sv$",       "monitor_proxy"),
    (r"_agent_config\.sv$",        "agent_config"),
    (r"_env_config\.sv$",          "env_config"),
    (r"_agent\.sv$",               "agent"),
    (r"_coverage\.sv$",            "coverage"),
    (r"_scoreboard\.sv$",          "scoreboard"),
    (r"_sequencer\.sv$",           "sequencer"),
    (r"_seq_item_converter\.sv$",  "seq_item_converter"),
    (r"_cfg_converter\.sv$",       "cfg_converter"),
    (r"_memory\.sv$",              "slave_memory"),
    (r"_tx\.sv$",                  "transaction"),
    (r"_env\.sv$",                 "env"),
    (r"_pkg\.sv$",                 "package"),
    (r"_globals_pkg\.sv$",         "globals_package"),
    (r"_global_pkg\.sv$",          "globals_package"),
    (r"virtual_sequencer.*\.sv$",  "virtual_sequencer"),
    (r"hdl_top\.sv$",              "hdl_top"),
    (r"hvl_top\.sv$",              "hvl_top"),
    (r"_base_test\.sv$",           "test"),
    (r"_test\.sv$",                "test"),
    (r"_assertions?\.sv$",         "assertions"),
    (r"_seq\.sv$",                 "sequence"),
    (r"_sequence\.sv$",            "sequence"),
    (r"_seqs\.sv$",                "sequence"),
]

HDL_DIR_KEYWORDS = ["hdl_top", "hdl", "bfm", "rtl"]
HVL_DIR_KEYWORDS = ["hvl_top", "hvl", "env", "test", "agent", "master", "slave"]


def classify_file_role(filepath):
    """Classify a VIP file's role based on its filename."""
    fname = os.path.basename(filepath)
    for pattern, role in ROLE_PATTERNS:
        if re.search(pattern, fname, re.IGNORECASE):
            return role
    return "other"


def classify_file_layer(filepath):
    """Classify which VIP layer a file belongs to (hdl/hvl/globals)."""
    parts = filepath.replace("\\", "/").lower()
    if "globals" in parts or "global" in parts:
        return "globals"
    for kw in HDL_DIR_KEYWORDS:
        if kw in parts:
            return "hdl"
    for kw in HVL_DIR_KEYWORDS:
        if kw in parts:
            return "hvl"
    return "unknown"


# ---------------------------------------------------------------------------
# Compile file (.f) parser
# ---------------------------------------------------------------------------

def parse_compile_file(compile_f_path, vip_dir):
    """Parse a VIP compile file (.f) to get source files and include dirs.

    Compile files typically live in sim/ and reference paths relative to
    a simulator subdirectory (e.g. sim/cadence_sim/), so paths like
    ``../../src/globals/`` resolve to the VIP root's ``src/globals/``.
    We try multiple base directories to find valid paths.
    """
    raw_incs = []
    raw_srcs = []

    with open(compile_f_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("+incdir+"):
                raw_incs.append(line[len("+incdir+"):])
            elif line.startswith("+") or line.startswith("-"):
                continue
            else:
                raw_srcs.append(line)

    # Try to find the correct base directory for resolving paths.
    # Compile files are often invoked from sim/<tool>/ so ../../ reaches
    # the repo root.  We try: compile_file dir, one level up, two levels
    # up (== vip_dir), and vip_dir directly.
    compile_dir = os.path.dirname(compile_f_path)
    candidates = [
        compile_dir,
        os.path.join(compile_dir, "cadence_sim"),  # common sim subdir
        os.path.join(compile_dir, "questasim"),
        vip_dir,
    ]

    def resolve(rel_path, is_dir=False):
        for base in candidates:
            p = os.path.normpath(os.path.join(base, rel_path))
            if (is_dir and os.path.isdir(p)) or (not is_dir and os.path.isfile(p)):
                return p
        # Fallback: resolve from vip_dir
        return os.path.normpath(os.path.join(vip_dir, rel_path))

    src_files = [resolve(s) for s in raw_srcs]
    inc_dirs = [resolve(d, is_dir=True) for d in raw_incs]

    return src_files, inc_dirs


def find_compile_file(vip_dir):
    """Find the compile .f file in the VIP repo."""
    sim_dir = os.path.join(vip_dir, "sim")
    if os.path.isdir(sim_dir):
        for f in os.listdir(sim_dir):
            if f.endswith("_compile.f") or f.endswith(".f"):
                return os.path.join(sim_dir, f)
    return None


# ---------------------------------------------------------------------------
# slang AST helpers
# ---------------------------------------------------------------------------

def run_slang_compile(slang_path, uvm_src, src_files, inc_dirs):
    """Compile VIP files with slang + UVM and get AST JSON."""
    fd, ast_file = tempfile.mkstemp(suffix=".json")

    cmd = [slang_path, uvm_src + "/uvm_pkg.sv"]
    cmd.extend(src_files)
    cmd.extend(["--ast-json", ast_file])
    cmd.extend(["--allow-use-before-declare"])
    cmd.append("-I")
    cmd.append(uvm_src)
    for inc in inc_dirs:
        cmd.extend(["-I", inc])

    result = subprocess.run(cmd, capture_output=True, text=True)
    os.close(fd)

    ast = None
    if os.path.exists(ast_file) and os.path.getsize(ast_file) > 0:
        try:
            with open(ast_file) as f:
                ast = json.load(f)
        except json.JSONDecodeError:
            pass
        os.unlink(ast_file)
    elif os.path.exists(ast_file):
        os.unlink(ast_file)

    compile_output = result.stdout + result.stderr
    compile_ok = result.returncode == 0

    return ast, compile_ok, compile_output


def run_slang_lint(slang_path, uvm_src, src_files, inc_dirs):
    """Run slang lint-only check on VIP files with UVM."""
    cmd = [slang_path, uvm_src + "/uvm_pkg.sv"]
    cmd.extend(src_files)
    cmd.extend(["--lint-only", "--allow-use-before-declare"])
    cmd.append("-I")
    cmd.append(uvm_src)
    for inc in inc_dirs:
        cmd.extend(["-I", inc])

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# UVM base class classification
# ---------------------------------------------------------------------------

def classify_uvm_base(base_class):
    """Classify UVM component type from base class name."""
    base_lower = base_class.lower()
    mapping = [
        ("uvm_agent", "agent"),
        ("uvm_driver", "driver"),
        ("uvm_monitor", "monitor"),
        ("uvm_sequencer", "sequencer"),
        ("uvm_sequence_item", "sequence_item"),
        ("uvm_sequence", "sequence"),
        ("uvm_env", "env"),
        ("uvm_test", "test"),
        ("uvm_scoreboard", "scoreboard"),
        ("uvm_subscriber", "subscriber"),
        ("uvm_component", "component"),
        ("uvm_object", "object"),
    ]
    for pattern, role in mapping:
        if pattern in base_lower:
            return role
    return "unknown"


# ---------------------------------------------------------------------------
# AST extraction from slang JSON
# ---------------------------------------------------------------------------

def parse_width_from_type(type_str):
    """Parse bit width from a type string."""
    match = re.search(r'\[(\d+):(\d+)\]', type_str)
    if match:
        return abs(int(match.group(1)) - int(match.group(2))) + 1
    return 1


def parse_range_from_type(type_str):
    """Parse [high:low] range from type string."""
    match = re.search(r'\[(\d+):(\d+)\]', type_str)
    if match:
        return [int(match.group(1)), int(match.group(2))]
    return None


def extract_packages_from_ast(ast):
    """Extract package info (classes, typedefs, parameters) from slang AST."""
    packages = []

    for member in ast.get("design", {}).get("members", []):
        if member.get("kind") != "CompilationUnit":
            continue
        for pkg in member.get("members", []):
            if pkg.get("kind") != "Package":
                continue
            pkg_name = pkg.get("name", "")
            # Skip UVM packages
            if "uvm" in pkg_name.lower():
                continue

            classes = []
            typedefs = []
            parameters = []

            for m in pkg.get("members", []):
                kind = m.get("kind", "")
                if kind == "ClassType":
                    cls = extract_class_info(m)
                    classes.append(cls)
                elif kind == "TypeAlias":
                    typedefs.append({
                        "name": m.get("name", ""),
                        "target": m.get("target", ""),
                    })
                elif kind == "Parameter":
                    parameters.append({
                        "name": m.get("name", ""),
                        "type": m.get("type", ""),
                        "value": str(m.get("value", "")),
                        "is_local": m.get("isLocal", False),
                    })

            packages.append({
                "name": pkg_name,
                "classes": classes,
                "typedefs": typedefs,
                "parameters": parameters,
            })

    return packages


def extract_class_info(cls_node):
    """Extract class metadata from an AST ClassType node."""
    name = cls_node.get("name", "")
    base_class = cls_node.get("baseClass", "")
    uvm_type = classify_uvm_base(base_class) if base_class else "unknown"

    properties = []
    methods = []
    tlm_ports = []
    virtual_interfaces = []

    for m in cls_node.get("members", []):
        kind = m.get("kind", "")
        mname = m.get("name", "")
        mtype = m.get("type", "")

        if kind == "ClassProperty":
            prop = {
                "name": mname,
                "type": clean_type_str(mtype),
            }
            properties.append(prop)

            # Detect TLM ports
            type_lower = mtype.lower()
            if any(t in type_lower for t in [
                "uvm_analysis_port", "uvm_analysis_imp", "uvm_analysis_export",
                "uvm_seq_item_pull_port", "uvm_seq_item_pull_imp",
                "uvm_tlm_analysis_fifo", "analysis_port",
                "seq_item_port",
            ]):
                tlm_ports.append({
                    "name": mname,
                    "port_type": clean_type_str(mtype),
                })

            # Detect virtual interface handles
            if "virtual" in type_lower or "_bfm" in mtype:
                if any(kw in mtype.lower() for kw in ["_bfm", "_if"]):
                    virtual_interfaces.append({
                        "name": mname,
                        "interface_type": clean_type_str(mtype),
                    })

        elif kind == "Subroutine":
            methods.append(mname)

    return {
        "name": name,
        "base_class": base_class,
        "uvm_component_type": uvm_type,
        "properties": properties,
        "methods": methods,
        "tlm_ports": tlm_ports,
        "virtual_interfaces": virtual_interfaces,
    }


def clean_type_str(type_str):
    """Clean up type string from AST (remove numeric addresses)."""
    # Remove numeric prefixes like "2292687585296 "
    cleaned = re.sub(r'^\d+\s+', '', str(type_str))
    # Remove package prefixes for common UVM types
    cleaned = re.sub(r'uvm_pkg::', '', cleaned)
    # Remove driver template params
    cleaned = re.sub(r'uvm_driver::(REQ|RSP)', r'\1', cleaned)
    return cleaned


def extract_instances_from_ast(ast):
    """Extract top-level instances (interfaces, modules) from slang AST."""
    instances = []

    for member in ast.get("design", {}).get("members", []):
        if member.get("kind") != "Instance":
            continue

        body = member.get("body", {})
        inst_name = body.get("name", "")

        ports = []
        sub_instances = []
        interface_ports = []
        parameters = []

        for m in body.get("members", []):
            kind = m.get("kind", "")
            if kind == "Port":
                port_type = m.get("type", "")
                ports.append({
                    "name": m.get("name", ""),
                    "direction": m.get("direction", ""),
                    "type": port_type,
                    "width": parse_width_from_type(port_type),
                    "range": parse_range_from_type(port_type),
                })
            elif kind == "Instance":
                sub_body = m.get("body", {})
                sub_ports = []
                for sm in sub_body.get("members", []):
                    if sm.get("kind") == "Port":
                        sp_type = sm.get("type", "")
                        sub_ports.append({
                            "name": sm.get("name", ""),
                            "direction": sm.get("direction", ""),
                            "type": sp_type,
                            "width": parse_width_from_type(sp_type),
                            "range": parse_range_from_type(sp_type),
                        })
                sub_instances.append({
                    "instance_name": m.get("name", ""),
                    "module_name": sub_body.get("name", ""),
                    "ports": sub_ports,
                })
            elif kind == "InterfacePort":
                interface_ports.append({
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                })
            elif kind == "Parameter":
                parameters.append({
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                    "value": str(m.get("value", "")),
                })

        instances.append({
            "name": inst_name,
            "ports": ports,
            "sub_instances": sub_instances,
            "interface_ports": interface_ports,
            "parameters": parameters,
        })

    return instances


def extract_definitions_from_ast(ast):
    """Extract definition metadata (interface/module types)."""
    definitions = []
    for defn in ast.get("definitions", []):
        name = defn.get("name", "")
        def_kind = defn.get("definitionKind", "")
        definitions.append({
            "name": name,
            "kind": def_kind,
        })
    return definitions


# ---------------------------------------------------------------------------
# Text-based fallback parsing for interface signals
# ---------------------------------------------------------------------------

def read_file_content(filepath):
    """Read file content, return empty string on failure."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def strip_comments(content):
    """Remove single-line and multi-line comments from SV source."""
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
    return content


def extract_interface_signals_from_text(content):
    """Extract signal declarations from interface body using text parsing."""
    cleaned = strip_comments(content)
    signals = []

    # Match interface block
    iface_match = re.search(
        r'interface\s+(\w+)\s*(?:\([^)]*\))?\s*;(.*?)endinterface',
        cleaned, re.DOTALL
    )
    if not iface_match:
        return signals

    body = iface_match.group(2)

    # Extract signal declarations
    pattern = re.compile(
        r'^\s*(logic|bit|reg|wire)\s+'
        r'(?:(\[[^\]]+\])\s+)?'
        r'(\w+)\s*;',
        re.MULTILINE
    )
    for m in pattern.finditer(body):
        signals.append({
            "name": m.group(3),
            "type": m.group(1),
            "range": m.group(2) or "",
        })

    return signals


def extract_clocking_blocks_from_text(content):
    """Extract clocking block names from file content."""
    blocks = []
    pattern = re.compile(r'clocking\s+(\w+)\s+@\s*\((\w+)\s+(\w+)\)')
    for m in pattern.finditer(content):
        blocks.append({
            "name": m.group(1),
            "edge": m.group(2),
            "clock": m.group(3),
        })
    return blocks


def extract_task_names_from_text(content):
    """Extract task names from file content."""
    cleaned = strip_comments(content)
    tasks = []
    for m in re.finditer(r'^\s*task\s+(\w+)', cleaned, re.MULTILINE):
        tasks.append(m.group(1))
    return tasks


def extract_modports_from_text(content):
    """Extract modport declarations from file content."""
    cleaned = strip_comments(content)
    modports = []
    for m in re.finditer(r'modport\s+(\w+)\s*\(([^)]+)\)', cleaned, re.DOTALL):
        modports.append({
            "name": m.group(1),
            "ports": m.group(2).strip(),
        })
    return modports


def extract_imports_from_text(content):
    """Extract import statements from file content."""
    imports = []
    for m in re.finditer(r'import\s+([\w:*]+)\s*;', content):
        imports.append(m.group(1))
    return imports


def extract_includes_from_text(content):
    """Extract `include directives from file content."""
    includes = []
    for m in re.finditer(r'`include\s+"([^"]+)"', content):
        includes.append(m.group(1))
    return includes


def extract_defines_from_text(content):
    """Extract `define directives from file content."""
    defines = []
    for m in re.finditer(r'`define\s+(\w+)(?:\s+(.*))?', content):
        defines.append({
            "name": m.group(1),
            "value": m.group(2).strip() if m.group(2) else None,
        })
    return defines


def extract_structs_from_text(content):
    """Extract struct definitions with fields from file content."""
    cleaned = strip_comments(content)
    structs = []
    for m in re.finditer(r'typedef\s+struct\s*\{([^}]*)\}\s*(\w+)\s*;',
                         cleaned, re.DOTALL):
        struct_body = m.group(1)
        struct_name = m.group(2)
        fields = []
        for f in re.finditer(r'([\w\[\]:*\s]+?)\s+(\w+)\s*;', struct_body):
            field_type = f.group(1).strip()
            field_name = f.group(2)
            if field_type and field_name:
                fields.append({"name": field_name, "type": field_type})
        structs.append({"name": struct_name, "fields": fields})
    return structs


def extract_enums_from_text(content):
    """Extract enum definitions from file content."""
    cleaned = strip_comments(content)
    enums = []
    for m in re.finditer(
        r'typedef\s+enum\s+([\w\[\]\s:]+)\s*\{([^}]*)\}\s*(\w+)\s*;',
        cleaned, re.DOTALL
    ):
        enum_type = m.group(1).strip()
        enum_body = m.group(2)
        enum_name = m.group(3)
        values = []
        for v in re.finditer(r'(\w+)\s*(?:=\s*([^,}]+))?', enum_body):
            values.append({
                "name": v.group(1),
                "value": v.group(2).strip() if v.group(2) else "",
            })
        enums.append({
            "name": enum_name,
            "base_type": enum_type,
            "values": values,
        })
    return enums


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def get_git_info(repo_dir):
    """Get git information for a repository."""
    info = {"remote_url": "", "commit_sha": "", "branch": ""}
    try:
        r = subprocess.run(["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            info["remote_url"] = r.stdout.strip()
        r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            info["commit_sha"] = r.stdout.strip()
        r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            info["branch"] = r.stdout.strip()
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Protocol detection
# ---------------------------------------------------------------------------

def detect_protocol(repo_name, files):
    """Detect the bus protocol from repo name or file names."""
    name_lower = repo_name.lower()
    for proto in ["axi4", "axi", "ahb", "apb", "uart", "spi", "i2c"]:
        if proto in name_lower:
            return proto
    for f in files:
        fname = os.path.basename(f).lower()
        for proto in ["axi4", "axi", "ahb", "apb", "uart", "spi", "i2c"]:
            if proto in fname:
                return proto
    return "unknown"


# ---------------------------------------------------------------------------
# Main VIP metadata generation
# ---------------------------------------------------------------------------

def find_sv_files(vip_dir):
    """Find all SystemVerilog source files in the VIP directory."""
    sv_files = []
    for root, dirs, files in os.walk(vip_dir):
        dirs[:] = [d for d in dirs
                   if not d.startswith('.') and d not in ('sim', 'doc', '.git')]
        for f in sorted(files):
            if f.endswith(('.sv', '.svh', '.v')):
                sv_files.append(os.path.join(root, f))
    return sv_files


def generate_vip_metadata(vip_dir, slang_path, uvm_src, repo_url, repo_name):
    """Generate comprehensive VIP metadata using slang AST + text parsing."""
    vip_dir = str(Path(vip_dir).resolve())

    # Try to find compile file for proper ordering
    compile_f = find_compile_file(vip_dir)
    if compile_f:
        src_files, inc_dirs = parse_compile_file(compile_f, vip_dir)
        print(f"[generate_vip_meta] Using compile file: {compile_f}")
        print(f"[generate_vip_meta] Source files: {len(src_files)}")
        print(f"[generate_vip_meta] Include dirs: {len(inc_dirs)}")
    else:
        src_files = find_sv_files(vip_dir)
        inc_dirs = list(set(os.path.dirname(f) for f in src_files))
        print(f"[generate_vip_meta] No compile file found, using file discovery")
        print(f"[generate_vip_meta] Source files: {len(src_files)}")

    if not src_files:
        print("[generate_vip_meta] WARNING: No SV files found")
        return None

    all_sv_files = find_sv_files(vip_dir)
    protocol = detect_protocol(repo_name, all_sv_files)
    git_info = get_git_info(vip_dir)

    # Get slang version
    result = subprocess.run([slang_path, "--version"], capture_output=True, text=True)
    slang_version = result.stdout.strip() if result.returncode == 0 else "unknown"

    # Step 1: Lint check
    print("[generate_vip_meta] Running lint check...")
    lint_ok, lint_output = run_slang_lint(slang_path, uvm_src, src_files, inc_dirs)
    print(f"[generate_vip_meta] Lint: {'PASS' if lint_ok else 'FAIL'}")

    # Step 2: AST extraction
    print("[generate_vip_meta] Extracting AST...")
    ast, compile_ok, compile_output = run_slang_compile(
        slang_path, uvm_src, src_files, inc_dirs
    )

    # Step 3: Extract from AST
    ast_packages = []
    ast_instances = []
    ast_definitions = []
    if ast:
        print("[generate_vip_meta] Parsing AST...")
        ast_packages = extract_packages_from_ast(ast)
        ast_instances = extract_instances_from_ast(ast)
        ast_definitions = extract_definitions_from_ast(ast)
        print(f"[generate_vip_meta] AST: {len(ast_packages)} packages, "
              f"{len(ast_instances)} instances, {len(ast_definitions)} definitions")
    else:
        print("[generate_vip_meta] WARNING: AST extraction failed, using text parsing only")

    # Step 4: Text-based parsing for supplementary data
    # (interface signals, clocking blocks, tasks, etc.)
    file_manifest = []
    text_interfaces = []

    for filepath in all_sv_files:
        content = read_file_content(filepath)
        rel_path = os.path.relpath(filepath, vip_dir)
        role = classify_file_role(filepath)
        layer = classify_file_layer(rel_path)

        file_entry = {
            "file": os.path.basename(filepath),
            "path": rel_path,
            "role": role,
            "layer": layer,
            "imports": extract_imports_from_text(content),
            "includes": extract_includes_from_text(content),
            "defines": extract_defines_from_text(content),
        }
        file_manifest.append(file_entry)

        # For interface files, extract signals from text
        # (AST definitions don't include members for uninstantiated interfaces)
        if role in ("interface", "driver_bfm", "monitor_bfm"):
            signals = extract_interface_signals_from_text(content)
            clocking = extract_clocking_blocks_from_text(content)
            tasks = extract_task_names_from_text(content)
            modports = extract_modports_from_text(content)

            if signals or clocking or tasks:
                text_interfaces.append({
                    "name": os.path.splitext(os.path.basename(filepath))[0],
                    "file": os.path.basename(filepath),
                    "role": role,
                    "layer": layer,
                    "signals": signals,
                    "clocking_blocks": clocking,
                    "tasks": tasks,
                    "modports": modports,
                })

    # Step 5: Merge AST instances with text-parsed interface details
    merged_interfaces = merge_interface_data(
        ast_instances, ast_definitions, text_interfaces
    )

    # Step 6: Extract globals package details from text
    # (AST gives us typedefs and params, text gives structs/enums)
    globals_packages = enhance_packages_with_text(ast_packages, all_sv_files, vip_dir)

    # Step 7: Build architecture summary
    architecture = build_architecture_summary(
        globals_packages, merged_interfaces, file_manifest
    )

    # Build final metadata
    meta = {
        "blockify_version": "0.1.0",
        "metadata_type": "vip",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repo_url": repo_url or git_info["remote_url"],
            "repo_name": repo_name,
            "commit_sha": git_info["commit_sha"],
            "branch": git_info["branch"],
        },
        "compilation": {
            "status": "PASS" if lint_ok else "FAIL",
            "slang_version": slang_version,
            "lint_output": lint_output,
            "defines_used": [],
            "include_dirs_used": inc_dirs,
        },
        "vip_info": {
            "protocol": protocol,
            "total_files": len(all_sv_files),
            "hdl_files": sum(1 for f in file_manifest if f["layer"] == "hdl"),
            "hvl_files": sum(1 for f in file_manifest if f["layer"] == "hvl"),
            "globals_files": sum(1 for f in file_manifest if f["layer"] == "globals"),
        },
        "packages": globals_packages,
        "interfaces": merged_interfaces,
        "architecture": architecture,
        "file_manifest": file_manifest,
    }

    return meta


def merge_interface_data(ast_instances, ast_definitions, text_interfaces):
    """Merge AST instance data with text-parsed interface details."""
    merged = []

    # Build a map of text-parsed interfaces by name
    text_map = {}
    for ti in text_interfaces:
        # Use the base name without _bfm suffix for matching
        text_map[ti["name"]] = ti

    # Process AST definitions (interfaces and modules)
    def_names = set()
    for defn in ast_definitions:
        if "uvm" in defn["name"].lower():
            continue
        def_names.add(defn["name"])

    # Process AST instances - these have full port details
    for inst in ast_instances:
        entry = {
            "name": inst["name"],
            "kind": "instance",
            "ports": inst["ports"],
            "sub_instances": inst["sub_instances"],
            "interface_ports": inst["interface_ports"],
            "parameters": inst["parameters"],
        }

        # Merge with text data if available
        for text_key, text_data in text_map.items():
            if text_key in inst["name"] or inst["name"] in text_key:
                entry["signals"] = text_data.get("signals", [])
                entry["clocking_blocks"] = text_data.get("clocking_blocks", [])
                entry["tasks"] = text_data.get("tasks", [])
                entry["modports"] = text_data.get("modports", [])
                entry["role"] = text_data.get("role", "")
                entry["layer"] = text_data.get("layer", "")
                break

        merged.append(entry)

    # Add text-parsed interfaces that aren't in AST instances
    inst_names = {inst["name"] for inst in ast_instances}
    for ti in text_interfaces:
        # Check if this interface is already covered by an AST instance
        already_covered = False
        for inst_name in inst_names:
            if ti["name"] in inst_name or inst_name in ti["name"]:
                already_covered = True
                break
        if not already_covered:
            # Check if it's a known definition
            kind = "interface"
            for defn in ast_definitions:
                if defn["name"] == ti["name"]:
                    kind = defn["kind"].lower()
                    break
            merged.append({
                "name": ti["name"],
                "kind": kind,
                "signals": ti["signals"],
                "clocking_blocks": ti.get("clocking_blocks", []),
                "tasks": ti.get("tasks", []),
                "modports": ti.get("modports", []),
                "role": ti.get("role", ""),
                "layer": ti.get("layer", ""),
            })

    return merged


def enhance_packages_with_text(ast_packages, all_sv_files, vip_dir):
    """Enhance AST package data with text-parsed structs and enums."""
    enhanced = []

    for pkg in ast_packages:
        pkg_name = pkg["name"]

        # Find the source file for this package
        pkg_file = None
        for f in all_sv_files:
            content = read_file_content(f)
            if re.search(r'package\s+' + re.escape(pkg_name) + r'\s*;', content):
                pkg_file = f
                break

        structs = []
        enums = []
        if pkg_file:
            content = read_file_content(pkg_file)
            structs = extract_structs_from_text(content)
            enums = extract_enums_from_text(content)

        rel_path = os.path.relpath(pkg_file, vip_dir) if pkg_file else ""

        enhanced.append({
            "name": pkg_name,
            "file": os.path.basename(pkg_file) if pkg_file else "",
            "path": rel_path,
            "classes": pkg["classes"],
            "typedefs": pkg["typedefs"],
            "parameters": pkg["parameters"],
            "structs": structs,
            "enums": enums,
        })

    return enhanced


def build_architecture_summary(packages, interfaces, file_manifest):
    """Build a high-level architecture summary of the VIP."""
    # Collect all classes from packages
    all_classes = []
    for pkg in packages:
        for cls in pkg.get("classes", []):
            cls["package"] = pkg["name"]
            all_classes.append(cls)

    # Categorize classes by UVM type
    agents = [c for c in all_classes if c.get("uvm_component_type") == "agent"]
    drivers = [c for c in all_classes if c.get("uvm_component_type") == "driver"]
    monitors = [c for c in all_classes if c.get("uvm_component_type") == "monitor"]
    sequencers = [c for c in all_classes if c.get("uvm_component_type") == "sequencer"]
    sequences = [c for c in all_classes if c.get("uvm_component_type") == "sequence"]
    seq_items = [c for c in all_classes if c.get("uvm_component_type") == "sequence_item"]
    envs = [c for c in all_classes if c.get("uvm_component_type") == "env"]
    tests = [c for c in all_classes if c.get("uvm_component_type") == "test"]
    subscribers = [c for c in all_classes if c.get("uvm_component_type") == "subscriber"]
    objects = [c for c in all_classes
               if c.get("uvm_component_type") in ("object", "unknown", "component")]

    # Interfaces by type
    protocol_ifaces = [i for i in interfaces if i.get("role") == "interface"]
    driver_bfms = [i for i in interfaces if "driver_bfm" in i.get("role", "")]
    monitor_bfms = [i for i in interfaces if "monitor_bfm" in i.get("role", "")]

    # Collect all TLM ports
    all_tlm_ports = []
    for cls in all_classes:
        for port in cls.get("tlm_ports", []):
            all_tlm_ports.append({
                "owner_class": cls["name"],
                "port_type": port["port_type"],
                "port_name": port["name"],
            })

    # Collect all virtual interface references
    all_vifs = []
    for cls in all_classes:
        for vif in cls.get("virtual_interfaces", []):
            all_vifs.append({
                "owner_class": cls["name"],
                "interface_type": vif["interface_type"],
                "name": vif["name"],
            })

    roles = sorted(set(f["role"] for f in file_manifest if f["role"] != "other"))

    return {
        "layers": {
            "hdl": {
                "description": "Hardware-level layer with BFMs and interfaces "
                               "that directly interact with RTL signals",
                "interfaces": [i["name"] for i in protocol_ifaces],
                "driver_bfms": [i["name"] for i in driver_bfms],
                "monitor_bfms": [i["name"] for i in monitor_bfms],
            },
            "hvl": {
                "description": "High-level verification layer with UVM components "
                               "using TLM and virtual interfaces",
                "agents": [c["name"] for c in agents],
                "drivers": [c["name"] for c in drivers],
                "monitors": [c["name"] for c in monitors],
                "sequencers": [c["name"] for c in sequencers],
                "sequences": [c["name"] for c in sequences],
                "sequence_items": [c["name"] for c in seq_items],
                "envs": [c["name"] for c in envs],
                "tests": [c["name"] for c in tests],
                "subscribers": [c["name"] for c in subscribers],
                "other_components": [c["name"] for c in objects],
            },
            "globals": {
                "description": "Global package definitions with parameters, "
                               "types, enums, and structs",
                "packages": [p["name"] for p in packages],
            },
        },
        "component_roles": roles,
        "tlm_connections": all_tlm_ports,
        "virtual_interface_usage": all_vifs,
        "total_interfaces": len(interfaces),
        "total_classes": len(all_classes),
        "total_packages": len(packages),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate metadata for UVM Verification IP repository"
    )
    parser.add_argument("vip_dir", help="Path to VIP repository directory")
    parser.add_argument("--slang", required=True, help="Path to slang binary")
    parser.add_argument("--uvm-src", required=True,
                        help="Path to UVM source directory (containing uvm_pkg.sv)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for metadata")
    parser.add_argument("--repo-url", default="", help="Source repository URL")
    parser.add_argument("--repo-name", default="", help="Source repository name")
    args = parser.parse_args()

    vip_dir = Path(args.vip_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    slang_path = str(Path(args.slang).resolve())
    uvm_src = str(Path(args.uvm_src).resolve())

    if not vip_dir.exists():
        print(f"ERROR: VIP directory not found: {vip_dir}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(os.path.join(uvm_src, "uvm_pkg.sv")):
        print(f"ERROR: UVM source not found at: {uvm_src}", file=sys.stderr)
        print("Run src/scripts/setup_uvm.sh first", file=sys.stderr)
        sys.exit(1)

    repo_name = args.repo_name or vip_dir.name

    print(f"[generate_vip_meta] Processing VIP: {repo_name}")
    print(f"[generate_vip_meta] Directory: {vip_dir}")
    print(f"[generate_vip_meta] UVM source: {uvm_src}")

    meta = generate_vip_metadata(
        str(vip_dir), slang_path, uvm_src, args.repo_url, repo_name
    )

    if meta is None:
        print("[generate_vip_meta] ERROR: No metadata generated")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{repo_name}_vip.json"
    meta_file = output_dir / filename
    meta_file.write_text(json.dumps(meta, indent=2))

    print(f"[generate_vip_meta] Generated: {meta_file}")
    print(f"[generate_vip_meta] Protocol: {meta['vip_info']['protocol']}")
    print(f"[generate_vip_meta] Files: {meta['vip_info']['total_files']}")
    print(f"[generate_vip_meta] Interfaces: {meta['architecture']['total_interfaces']}")
    print(f"[generate_vip_meta] Classes: {meta['architecture']['total_classes']}")
    print(f"[generate_vip_meta] Packages: {meta['architecture']['total_packages']}")


if __name__ == "__main__":
    main()
