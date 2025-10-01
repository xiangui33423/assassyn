NO_VERILATOR=false
for arg in "$@"; do
  if [ "$arg" = "--no-verilator" ]; then
    NO_VERILATOR=true
    break
  fi
done

if [ "$NO_VERILATOR" = true ]; then
  echo "Verilator is disabled by --no-verilator flag"
  return 0
fi

echo "Installing Verilator by building it from source..."

RESTORE="$(pwd)"

cd $ASSASSYN_HOME/3rd-party/verilator
git submodule update --init
autoconf
./configure
make -j

if [ $? -ne 0 ]; then
  echo "Failed to configure Verilator build. Please check the configuration."
  return 1
fi

cd $RESTORE
return 0