echo "Building Wrapper..."
RESTORE="$(pwd)"
mkdir -p "$ASSASSYN_HOME/tools/c-ramulator2-wrapper"
cd "$ASSASSYN_HOME/tools/c-ramulator2-wrapper"
mkdir -p build
cd build
cmake ..
make -j
if [ $? -ne 0 ]; then
  echo "Failed to build Wrapper."
  return 1
fi
echo "Wrapper build completed."
cd "$RESTORE"
return 0