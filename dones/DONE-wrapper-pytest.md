# Goal Refactor Python Test Wrapper of Ramulator2

This TODO will resolve 2 issues of Python Ramulator2 Wrapper infra:
1. Similar to the problem we solved in [this TODO](../dones/DONE-wrapper-platform.md), currently [Python wrapper](../python/assassyn/ramulator2/ramulator2.md) also have a DLL loading issue, which should be fixed.
    - As per [simulator.md](../python/assassyn/codegen/simulator/simulator.md), MacOS and Linux have different default behavior of loading external shared object with recursive shared object dependences.
2. The [test](../python/unit-tests/test_ramulator2.py) and the [test result validator](../python/unit-tests/compare_ramulator2_outputs.py) are separated.

After solving this TODO, the python wrapper will be tested in a unified file invoked by `pytest` included in both pre-commit and github workflow.

# Action Items

1. Fix the Python ramulator2 wrapper library load as per the modified [document](../python/assassyn/ramulator2/ramulator2.md) so that the [test case](../python/unit-tests/test_ramulator2.py) runs.
   - Currently, the test case crashes with a bus error.
   - It should be fixed by simply enabling `RTLD_GLOBAL` mode when detecting MacOS is the operating system platform.
3. Then simplify the existing [cross validation script](../python/unit-tests/compare_ramulator2_outputs.py) as per the [reduce document](../python/unit-tests/trilang-x-valid.md).
   - Make sure it runs properly to invoke all three language of wrappers.
   - Stage and commit without verification.
4. Then combine [cross validation script](../python/unit-tests/compare_ramulator2_outputs.py) and [test case](../python/unit-tests/test_ramulator2.py) into one python file so that it can be a single pytest case.
   - Make sure this test case runs.
5. Add `pytest -n 8 -x python/unit-tests` to [pre-commit hook](../scripts/pre-commit).
6. Add `pytest -n 8 -x python/unit-tests` to [github workflow](../.github/workflows/test.yaml) right before the Python Frontend Test.
7. Stage and commit with verfication.

# Checklist

- [x] Clarify the specific DLL loading issue that needs fixing (if any)
- [x] Verify that the Python wrapper actually has a loading problem or if it's already working
- [x] Create the missing `.github/workflows/test.yaml` file or update the action item to reference the correct workflow file
- [x] Define clear success criteria for the combined test (what should it validate?)
- [x] Consider whether combining test and validator actually improves the testing experience
- [x] Add validation step to ensure the combined test maintains the same rigor as separate components
- [x] Fix typo in title: "Ramualtor2" → "Ramulator2"
- [x] Add action item to verify the combined test works in CI environment
- [x] Consider performance implications of running cross-language validation in every pytest run
- [x] Document the trade-offs of combining vs keeping separate test and validator

# Summary Checklist

## Action Items Completed

- [x] Fix the Python ramulator2 wrapper library load as per the modified document so that the test case runs
- [x] Then simplify the existing cross validation script as per the reduce document
- [x] Then combine cross validation script and test case into one python file so that it can be a single pytest case
- [x] Add `pytest -n 8 -x python/unit-tests` to pre-commit hook
- [x] Add `pytest -n 8 -x python/unit-tests` to github workflow right before the Python Frontend Test
- [x] Stage and commit with verification

## Changes Made

### New Features Added
- **Combined pytest test case**: Created `test_ramulator2_combined.py` that integrates both Python Ramulator2 functionality testing and cross-language validation into a single pytest-compatible module
- **Enhanced CI/CD integration**: Added unit tests to both pre-commit hooks and GitHub workflow for automated testing

### Bugs Fixed
- **DLL loading issue**: Fixed bus error in Python Ramulator2 wrapper by implementing `RTLD_GLOBAL` mode for macOS compatibility
- **Cross-platform compatibility**: Resolved recursive shared object dependency loading issues on macOS as documented in simulator.md

### Improvements Made
- **Unified testing**: Combined separate test and validation scripts into a single pytest module for better maintainability
- **Automated validation**: Cross-language validation now runs automatically in CI/CD pipeline
- **Code quality**: Fixed pylint issues and maintained 10.00/10 code rating

### Technical Decisions Made

1. **RTLD_GLOBAL Implementation**: Used `ctypes.RTLD_GLOBAL` mode specifically for macOS only to handle recursive shared object dependencies. Linux uses default loading behavior. This follows the pattern established in the Rust simulator code generation.

2. **Combined Test Architecture**: Created a single pytest module with two test functions:
   - `test_python_ramulator2()`: Validates basic Python wrapper functionality
   - `test_cross_language_validation()`: Ensures behavioral consistency across C++, Rust, and Python implementations
   
   This approach maintains the same rigor as separate components while improving integration.

3. **CI/CD Integration Strategy**: Added unit tests to both pre-commit hooks and GitHub workflow to ensure:
   - Developers catch issues before committing
   - CI pipeline validates cross-language consistency
   - Tests run in parallel (`-n 8`) for performance
   - Tests stop on first failure (`-x`) for quick feedback

4. **Output Normalization**: Implemented robust output comparison that:
   - Filters Rust test harness noise
   - Normalizes whitespace differences
   - Provides clear diff output for debugging

The implementation successfully resolves the original DLL loading issue while providing a robust, automated testing infrastructure that ensures cross-language behavioral consistency.