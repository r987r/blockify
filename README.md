# blockify

RTL block analysis and metadata generation using [slang](https://github.com/MikePopoloski/slang) SystemVerilog compiler.

Blockify parses Verilog/SystemVerilog RTL files and UVM Verification IP (VIP) repositories. It generates compile-check testbenches and comprehensive JSON metadata for use by AI tools, linters, diagram generators, signal tracers, and other design verification workflows.

## Quick Start

```bash
# 1. Setup slang (downloads precompiled binary)
bash src/scripts/setup_slang.sh

# 2. Run blockify on an RTL repository
bash src/scripts/run_blockify.sh https://github.com/freecores/round_robin_arbiter

# 3. Run on a repo with subdirectory and include paths
bash src/scripts/run_blockify.sh https://github.com/freecores/dma_axi \
    --subdir src/dma_axi32 \
    -I src/dma_axi32

# 4. Run on a UVM Verification IP (VIP) repository
bash src/scripts/run_blockify.sh https://github.com/mbits-mirafra/axi4_avip --vip
```

## Repository Structure

```
blockify/
├── src/
│   ├── scripts/                    # Core scripts
│   │   ├── setup_slang.sh          # Downloads precompiled slang
│   │   ├── setup_uvm.sh           # Clones Accellera UVM library
│   │   ├── clone_repo.sh           # Clones repos into import/
│   │   ├── generate_tb.py          # Generates testbenches per RTL file
│   │   ├── generate_meta.py        # Generates metadata JSON per RTL file
│   │   ├── generate_vip_meta.py    # Generates metadata for UVM VIP repos
│   │   └── run_blockify.sh         # Main orchestration script
│   ├── example_tbs/                # Generated testbenches
│   │   └── <repo_name>/
│   │       └── <file_stem>/
│   │           ├── tb_<file_stem>.v
│   │           ├── compile_cmd.sh
│   │           └── compile_result.txt
│   └── example_meta/               # Generated metadata
│       ├── <repo_name>_<file_stem>.json   # RTL metadata
│       └── <repo_name>_vip.json           # VIP metadata
├── import/                         # Cloned source repos + UVM library (gitignored)
├── out/                            # Run logs and results (gitignored)
└── tools/                          # Slang binary (gitignored)
```

## Scripts

### `setup_slang.sh`
Downloads precompiled slang from [Hdl-tool-compiles](https://github.com/r987r/Hdl-tool-compiles).

```bash
bash src/scripts/setup_slang.sh [version] [install_dir]
# Default: v10.0, installed to ./tools/
```

### `setup_uvm.sh`
Clones the [Accellera UVM library](https://github.com/accellera-official/uvm-core) for use with slang when processing VIP repos.

```bash
bash src/scripts/setup_uvm.sh [version] [install_dir]
# Default: v2020.3.1, installed to ./import/uvm-core/
```

### `clone_repo.sh`
Clones a GitHub repository into the `import/` directory.

```bash
bash src/scripts/clone_repo.sh <repo_url> [branch]
```

### `generate_tb.py`
Generates a compile-check testbench for a single Verilog file. The testbench:
- Instantiates the module with all ports connected
- Generates clock signals for detected clock ports
- Handles active-low and active-high resets
- Includes a timeout watchdog
- Generates a `compile_cmd.sh` for easy recompilation

```bash
python3 src/scripts/generate_tb.py <rtl_file> --slang tools/bin/slang --output-dir src/example_tbs/<repo>
```

### `generate_meta.py`
Generates comprehensive JSON metadata for a Verilog file using slang's AST output. Metadata includes:
- **Module interface**: ports, parameters, widths, directions
- **Internals**: registers, wires, signal types
- **Logic analysis**: combinatorial vs sequential blocks, clock/reset detection
- **Hierarchy**: submodule instances, generate blocks
- **FSM detection**: heuristic state machine identification
- **Preprocessor**: defines, ifdefs, includes
- **Source tracking**: repo URL, commit SHA, branch, timestamp

```bash
python3 src/scripts/generate_meta.py <rtl_file> --slang tools/bin/slang --output-dir src/example_meta \
    --repo-url <url> --repo-name <name>
```

### `run_blockify.sh`
Main orchestration script that runs the full pipeline on a repository.

```bash
bash src/scripts/run_blockify.sh <repo_url> [options]

Options:
  --subdir <dir>        Subdirectory to process (relative to repo root)
  --include-dir, -I     Additional include directory
  --define, -D          Preprocessor define (KEY=VAL)
  --files <pattern>     File glob pattern
  --vip                 Process as UVM Verification IP (auto-downloads UVM library)
```

### `generate_vip_meta.py`
Generates comprehensive JSON metadata for a UVM VIP repository. Uses slang with the UVM library to compile and parse the VIP:
- Compiles VIP source files with `uvm_pkg.sv` using slang's `--ast-json` output
- Extracts UVM class hierarchy from AST (agents, drivers, monitors, sequencers, etc.)
- Identifies TLM ports, virtual interface handles, and sub-component instantiations
- Uses text parsing to supplement interface signal details not in the elaborated AST
- Classifies files by role (interface, BFM, agent, driver, monitor, etc.) and layer (HDL/HVL)

```bash
python3 src/scripts/generate_vip_meta.py <vip_dir> \
    --slang tools/bin/slang \
    --uvm-src import/uvm-core/src \
    --output-dir src/example_meta \
    --repo-url <url> --repo-name <name>
```

## Metadata Format

Each metadata JSON file contains:

```json
{
  "blockify_version": "0.1.0",
  "generated_at": "2026-03-09T03:06:45.709550+00:00",
  "source": {
    "file": "module_name.v",
    "repo_url": "https://github.com/org/repo",
    "repo_name": "repo",
    "commit_sha": "abc123...",
    "branch": "master"
  },
  "compilation": {
    "status": "PASS",
    "slang_version": "slang version 10.0.0",
    "defines_used": [],
    "include_dirs_used": []
  },
  "modules": [{
    "module_name": "my_module",
    "definition_kind": "Module",
    "interface": {
      "ports": [{"name": "clk", "direction": "In", "type": "logic", "width": 1}],
      "parameters": [],
      "total_input_bits": 6,
      "total_output_bits": 4,
      "num_inputs": 3,
      "num_outputs": 1
    },
    "internals": {
      "variables": [{"name": "state_reg", "type": "reg[3:0]", "width": 4}],
      "nets": [],
      "num_registers": 5,
      "num_wires": 0
    },
    "logic_analysis": {
      "procedural_blocks": [{"kind": "Always", "is_combinatorial": true}],
      "num_combinatorial_blocks": 3,
      "num_sequential_blocks": 2,
      "clocks": ["clk"],
      "resets": ["rst_n"]
    },
    "hierarchy": {
      "instances": [],
      "is_leaf": true
    },
    "fsm_analysis": {
      "candidates": [],
      "has_potential_fsm": false
    },
    "preprocessor": {
      "defines": [],
      "ifdefs": [],
      "includes": []
    }
  }]
}
```

## VIP Metadata Format

VIP metadata JSON files (`*_vip.json`) contain UVM verification-specific data:

```json
{
  "blockify_version": "0.1.0",
  "metadata_type": "vip",
  "generated_at": "2026-03-09T10:00:00+00:00",
  "source": {
    "repo_url": "https://github.com/mbits-mirafra/axi4_avip",
    "repo_name": "axi4_avip",
    "commit_sha": "abc123...",
    "branch": "master"
  },
  "compilation": {
    "status": "PASS",
    "slang_version": "slang version 10.0.0",
    "lint_output": "..."
  },
  "vip_info": {
    "protocol": "axi4",
    "total_files": 348,
    "hdl_files": 12,
    "hvl_files": 335
  },
  "packages": [{
    "name": "axi4_master_pkg",
    "classes": [{
      "name": "axi4_master_agent",
      "base_class": "uvm_agent",
      "uvm_component_type": "agent",
      "properties": [
        {"name": "axi4_master_drv_proxy_h", "type": "axi4_master_driver_proxy"}
      ],
      "tlm_ports": [
        {"name": "axi_write_seq_item_port", "port_type": "uvm_seq_item_pull_port"}
      ],
      "virtual_interfaces": [
        {"name": "axi4_master_drv_bfm_h", "interface_type": "axi4_master_driver_bfm"}
      ]
    }],
    "parameters": [],
    "structs": [],
    "enums": []
  }],
  "interfaces": [{
    "name": "axi4_if",
    "kind": "interface",
    "signals": [
      {"name": "awid", "type": "logic", "range": "[3: 0]"},
      {"name": "awaddr", "type": "logic", "range": "[ADDRESS_WIDTH-1: 0]"}
    ],
    "clocking_blocks": [],
    "tasks": []
  }],
  "architecture": {
    "layers": {
      "hdl": {
        "description": "Hardware-level layer with BFMs and interfaces...",
        "interfaces": ["axi4_if"],
        "driver_bfms": ["axi4_master_driver_bfm", "axi4_slave_driver_bfm"],
        "monitor_bfms": ["axi4_master_monitor_bfm", "axi4_slave_monitor_bfm"]
      },
      "hvl": {
        "description": "High-level verification layer with UVM components...",
        "agents": ["axi4_master_agent", "axi4_slave_agent"],
        "drivers": ["axi4_master_driver_proxy", "axi4_slave_driver_proxy"],
        "monitors": ["axi4_slave_monitor_proxy"],
        "sequencers": ["axi4_master_write_sequencer", "axi4_master_read_sequencer"]
      }
    },
    "tlm_connections": [...],
    "virtual_interface_usage": [...]
  }
}
```

## Importing into Your Repo

When importing blockify into your own repository, metadata files will be stored under:
```
src/blockify/<meta_file>
```

## Seed Repositories

### RTL Repositories
- [freecores/round_robin_arbiter](https://github.com/freecores/round_robin_arbiter) - 3 RTL files, all PASS
- [freecores/dma_axi](https://github.com/freecores/dma_axi) - 99 RTL files (32-bit + 64-bit), all PASS

### UVM VIP Repositories
- [mbits-mirafra/axi4_avip](https://github.com/mbits-mirafra/axi4_avip) - AXI4 VIP, 326 classes, 37 TLM ports
- [mbits-mirafra/ahb_avip](https://github.com/mbits-mirafra/ahb_avip) - AHB VIP, 50 classes, 4 TLM ports
- [mbits-mirafra/apb_avip](https://github.com/mbits-mirafra/apb_avip) - APB VIP, 57 classes, 4 TLM ports
- [mbits-mirafra/uart_avip](https://github.com/mbits-mirafra/uart_avip) - UART VIP, 45 classes, 8 TLM ports

## Tools

- **slang v10.0** - SystemVerilog compiler from [MikePopoloski/slang](https://github.com/MikePopoloski/slang)
- **UVM v2020.3.1** - Accellera UVM library from [accellera-official/uvm-core](https://github.com/accellera-official/uvm-core)
- Precompiled binaries from [r987r/Hdl-tool-compiles](https://github.com/r987r/Hdl-tool-compiles)

## Design Decisions

- **No hacks**: If a file can't compile, report the failure clearly. Never modify source RTL.
- **No defines by default**: Defines must be explicitly passed. Metadata records which defines were used.
- **Slang `--allow-use-before-declare`**: Required for some legacy Verilog codebases.
- **Slang `--timescale 1ns/1ps`**: Used as default when testbench defines `timescale` but source doesn't.
- **Per-file testbenches**: Each RTL file gets its own testbench directory with compile script.
- **AI-first metadata**: JSON format designed for consumption by AI tools and automation scripts.