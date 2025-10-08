# Rust Ramulator2 Test

This Rust test (`tests/test_ramulator2.rs`) mirrors the functionality of the C++ test (`test.cpp`) in the `c-ramulator2-wrapper` directory. It serves as a cross-validation tool to ensure that both Rust and C++ implementations produce identical results when using the same underlying `libramulator` library (with OS-appropriate extension).

## Purpose

This test validates that:
1. Rust bindings correctly interface with the `CRamualator2Wrapper` C++ wrapper
2. Memory simulation behavior is consistent across different language implementations
3. The same request sequence produces identical output in both Rust and C++
4. Cross-platform compatibility works correctly on different operating systems

## Cross-Platform Support

The Rust implementation automatically handles different operating systems:
- **Linux**: Uses `.so` shared libraries
- **Windows**: Uses `.dll` shared libraries
- **macOS**: Uses `.dylib` shared libraries

The test includes fallback mechanisms to try alternative extensions if the primary one fails, ensuring maximum compatibility across different build configurations.

## Prerequisites

This document assumes your repository root is available (e.g., via `ASSASSYN_HOME` or your current working directory).
1. **Build the C++ wrapper library:**
   ```bash
   cd /{home}/tools/c-ramulator2-wrapper
   mkdir -p build
   cd build
   cmake ..
   make
   ```

2. **Ensure the config file exists:**
   ```bash
   ls /{home}/tools/c-ramulator2-wrapper/configs/example_config.yaml
   ```

## Running the Integration Test

The Rust test now lives under `tools/rust-sim-runtime/tests/test_ramulator2.rs` and runs as a Cargo integration test.

### Show stdout (important for cross-language comparison)
Run from the repository root or from `tools/rust-sim-runtime`:
```bash
cargo test --test test_ramulator2 -- --nocapture
```

To run the specific test by name (and still show stdout):
```bash
cargo test --test test_ramulator2 test_ramulator2_outputs_match_cpp -- --nocapture
```

List tests detected by Cargo:
```bash
cargo test --test test_ramulator2 -- --list
```

If `ASSASSYN_HOME` is not set, either set it to the repo root or run the command from the repository root so relative paths resolve correctly.

## Expected Output

The test should produce output identical to the C++ test, including:
- Write request status messages
- Request completion callbacks with cycle timing
- Same address patterns and timing calculations

## Test Logic

The test follows the same pattern as `test.cpp`:
1. Initialize memory interface with config file
2. Run 200 simulation cycles
3. Alternate between read and write requests
4. Use address patterns: `raddr = v & 0xFF`, `waddr = (v+1) & 0xFF`
5. Print write request status and read completion callbacks
6. Advance simulation with `frontend_tick()` and `memory_system_tick()`

## Cross-Validation

This test is part of a comprehensive validation suite that includes:
- **C++ Test**: `tools/c-ramulator2-wrapper/test.cpp`
- **Python Test**: `python/unit-tests/test_ramulator2.py`
- **Rust Test**: `tools/rust-sim-runtime/src/test_ramulator2.rs`

All tests must produce identical output when given the same configuration and request sequence, regardless of the operating system or shared library extension used.
