echo "Building Wrapper..."
RESTORE="$(pwd)"
mkdir -p "$ASSASSYN_HOME/testbench/simulator"
cd "$ASSASSYN_HOME/testbench/simulator"
mkdir -p build
cd build
cmake  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 ..
make -j 
if [ $? -ne 0 ]; then
  echo "Failed to build Wrapper."
  return 1
fi
echo "Wrapper build completed."
cd "$RESTORE"
return 0
