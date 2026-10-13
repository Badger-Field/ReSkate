#!/usr/bin/env bash
# Builds park_check: the dedicated server's own park loader (Server/server_parks.cpp) behind a
# small main, so the tests can check a generated mod the way a server and players' games read it.
# Linux, with the server's build dependencies (cmake, ninja, a C++20 compiler, OpenSSL).
#
#   tests/build_park_check.sh [build directory]
#   RESKATE_PARK_CHECK=<build directory>/park_check python -m pytest tests
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
build="$(mkdir -p "${1:-$repo/build/lidar-parks}" && cd "${1:-$repo/build/lidar-parks}" && pwd)"

cmake -S "$repo" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DDINGOSDK_BUILD_MULTIPLAYER_TESTS=ON > /dev/null
ninja -C "$build" dingosdk_server_park_tests > /dev/null

# Reuse that target's compile flags and link line, with park_check.cpp's main in place of the
# test's (the test object stays linked for the stand-in transport it defines).
cd "$build"
test_object=CMakeFiles/dingosdk_server_park_tests.dir/Server/Test/server_park_tests.cpp.o
objcopy --weaken-symbol=main "$test_object" park_check_tests.o
compile="$(ninja -t commands dingosdk_server_park_tests | grep -F 'server_park_tests.cpp' | grep -F -- ' -c ' | head -1)"
compile="$(sed -E "s# -o [^ ]+ -c [^ ]+# -o park_check.o -c $here/park_check.cpp#" <<< "$compile")"
eval "$compile"
link="$(ninja -t commands dingosdk_server_park_tests | tail -1)"
link="${link//$test_object/park_check_tests.o park_check.o}"
link="$(sed -E 's# -o dingosdk_server_park_tests# -o park_check#' <<< "$link")"
eval "$link"
echo "$build/park_check"
