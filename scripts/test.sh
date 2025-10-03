#!/bin/zsh

# Restore the original directory
RESTORE_DIR=`pwd`

function test_case {
  eval $1
  if [ $? -ne 0 ]; then
    echo "Test failed: $1"
    cd $RESTORE_DIR
    exit 1
  fi
}

# Go to the ci-test directory
cd `dirname $0`
REPO_DIR=`git rev-parse --show-toplevel`

# Test CI tests
cd $REPO_DIR/python/ci-tests
test_case "pytest -n 8 -x"

# Test examples

# Systolic Array
cd $REPO_DIR/examples/systolic-array/
test_case "python systolic_array.py"

# A single-issue CPU
cd $REPO_DIR/examples/minor-cpu
test_case "python src/main.py"

# A Priority Queue
cd $REPO_DIR/examples/priority-queue
test_case "python main.py"

# TODO(@were): Test memory engine.

cd $RESTORE_DIR
