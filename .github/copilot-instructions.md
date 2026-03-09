# Blockify - Copilot Instructions

## Project Overview

Blockify is an RTL (Register Transfer Level) block analysis tool that uses [slang](https://github.com/MikePopoloski/slang) to parse Verilog/SystemVerilog files. It generates compile-check testbenches and comprehensive JSON metadata for AI tools and design verification workflows.

## Key Conventions

### Directory Structure
- `src/scripts/` - All scripts (bash and python)
- `src/example_tbs/<repo_name>/<file_stem>/` - Testbenches with compile scripts
- `src/example_meta/<repo_name>_<file_stem>.json` - Metadata files
- `import/` - Cloned source repos (gitignored, not committed)
- `out/` - Run logs and results (gitignored, not committed)
- `tools/` - slang binary (gitignored, not committed)

### Running the Pipeline
```bash
# Setup slang first
bash src/scripts/setup_slang.sh

# Process a repository
bash src/scripts/run_blockify.sh <repo_url> [--subdir <dir>] [-I <include_dir>] [-D <define>]
```

### Adding a New Repository
1. Run `run_blockify.sh` with the repo URL
2. If the repo has include directories, pass them with `-I`
3. If the repo needs preprocessor defines, pass them with `-D`
4. Check compilation results - all files should PASS
5. If files fail, report clearly what failed and why - never hack around issues

### Slang Flags
- `--allow-use-before-declare` - Always used, required for legacy Verilog
- `--timescale 1ns/1ps` - Used for testbench compilation when source lacks timescale
- `--lint-only` - Used for compile checks (no elaboration)
- `--ast-json` - Used for metadata extraction
- `-I` - Include directories for `include directives
- `-D` - Preprocessor defines

### Metadata Design
Metadata JSON files are designed for consumption by downstream tools:
- **Lint tools**: Use `compilation.status`, `preprocessor.defines`
- **Diagram generators**: Use `interface.ports`, `hierarchy.instances`
- **Signal tracers**: Use `internals.variables`, `internals.nets`, `logic_analysis`
- **FSM analysis**: Use `fsm_analysis.candidates`
- **Debug tools**: Use all fields for comprehensive module understanding

### Important Rules
1. **Never modify source RTL files** - Only generate testbenches and metadata
2. **No hacks** - If compilation fails, report failure clearly
3. **No defines by default** - Must be explicitly requested
4. **Track provenance** - Metadata includes repo URL, commit SHA, timestamp
5. **AI-first format** - JSON metadata is structured for machine consumption
6. **Per-file testbenches** - Each RTL file gets its own testbench and compile script

### Seed Repos for Testing
- `https://github.com/freecores/round_robin_arbiter` - Simple, 3 files
- `https://github.com/freecores/dma_axi` - Complex, ~100 files with includes
  - `--subdir src/dma_axi32 -I src/dma_axi32` for 32-bit variant
  - `--subdir src/dma_axi64 -I src/dma_axi64` for 64-bit variant

### Import Workflow
When users import this repo into their own project:
- Metadata stored under `src/blockify/<meta_file>` in the importing repo
- The `import/` directory contains cloned source repositories
- Use the precompiled slang from [r987r/Hdl-tool-compiles](https://github.com/r987r/Hdl-tool-compiles)

### When Extending Scripts
- Python scripts use argparse for CLI arguments
- Bash scripts use `set -euo pipefail` for safety
- All paths should be relative to `$ROOT_DIR` (repo root)
- Use `$SLANG` variable for slang binary path
- Test with both simple (round_robin_arbiter) and complex (dma_axi) repos
