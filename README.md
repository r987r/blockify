# blockify

RTL block analysis and metadata generation using [slang](https://github.com/MikePopoloski/slang) SystemVerilog compiler.

Blockify parses Verilog/SystemVerilog RTL files, generates compile-check testbenches, and produces comprehensive JSON metadata for use by AI tools, linters, diagram generators, signal tracers, and other design verification workflows.

## Quick Start

```bash
# 1. Setup slang (downloads precompiled binary)
bash src/scripts/setup_slang.sh

# 2. Run blockify on a repository
bash src/scripts/run_blockify.sh https://github.com/freecores/round_robin_arbiter

# 3. Run on a repo with subdirectory and include paths
bash src/scripts/run_blockify.sh https://github.com/freecores/dma_axi \
    --subdir src/dma_axi32 \
    -I src/dma_axi32
```

## Repository Structure

```
blockify/
├── src/
│   ├── scripts/                    # Core scripts
│   │   ├── setup_slang.sh          # Downloads precompiled slang
│   │   ├── clone_repo.sh           # Clones repos into import/
│   │   ├── generate_tb.py          # Generates testbenches per RTL file
│   │   ├── generate_meta.py        # Generates metadata JSON per RTL file
│   │   └── run_blockify.sh         # Main orchestration script
│   ├── example_tbs/                # Generated testbenches
│   │   └── <repo_name>/
│   │       └── <file_stem>/
│   │           ├── tb_<file_stem>.v
│   │           ├── compile_cmd.sh
│   │           └── compile_result.txt
│   └── example_meta/               # Generated metadata
│       └── <repo_name>_<file_stem>.json
├── import/                         # Cloned source repos (gitignored)
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

## Importing into Your Repo

When importing blockify into your own repository, metadata files will be stored under:
```
src/blockify/<meta_file>
```

## Seed Repositories

Currently tested with:
- [freecores/round_robin_arbiter](https://github.com/freecores/round_robin_arbiter) - 3 RTL files, all PASS
- [freecores/dma_axi](https://github.com/freecores/dma_axi) - 99 RTL files (32-bit + 64-bit), all PASS

## Tools

- **slang v10.0** - SystemVerilog compiler from [MikePopoloski/slang](https://github.com/MikePopoloski/slang)
- Precompiled binaries from [r987r/Hdl-tool-compiles](https://github.com/r987r/Hdl-tool-compiles)

## Design Decisions

- **No hacks**: If a file can't compile, report the failure clearly. Never modify source RTL.
- **No defines by default**: Defines must be explicitly passed. Metadata records which defines were used.
- **Slang `--allow-use-before-declare`**: Required for some legacy Verilog codebases.
- **Slang `--timescale 1ns/1ps`**: Used as default when testbench defines `timescale` but source doesn't.
- **Per-file testbenches**: Each RTL file gets its own testbench directory with compile script.
- **AI-first metadata**: JSON format designed for consumption by AI tools and automation scripts.