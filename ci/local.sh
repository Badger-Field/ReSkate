#!/usr/bin/env bash
# The checks CI runs, run here: the same commands .github/workflows/build.yml runs, because the
# workflow calls this script. Nothing is uploaded and no Actions minutes are used.
#
#   ci/local.sh            every Linux job: the server and tests, the LiDAR parks tests, the map builder tests
#   ci/local.sh linux      the server and the regression tests (build/linux)
#   ci/local.sh lidar      the LiDAR parks Python tests
#   ci/local.sh mapbuilder the map builder's Python tests (checked against the C++ format code)
#
# The Windows job is ci/local.ps1, on a Windows PC with Visual Studio 2022.
set -euo pipefail
cd "$(dirname "$0")/.."

linux() {
    cmake -S . -B build/linux -DCMAKE_BUILD_TYPE=Release \
        -DDINGOSDK_BUILD_TESTS=ON -DDINGOSDK_BUILD_MULTIPLAYER_TESTS=ON \
        -DDINGOSDK_BUILD_PARK_EDITOR_TESTS=ON -DDINGOSDK_BUILD_TRAINER_TESTS=ON \
        -DDINGOSDK_BUILD_ROUTE_TESTS=ON -DDINGOSDK_BUILD_PLUGIN_TESTS=ON \
        -DDINGOSDK_BUILD_REPLAY_KEYFRAMES_TESTS=ON -DDINGOSDK_BUILD_LAUNCHER_TESTS=ON
    cmake --build build/linux --parallel
    ctest --test-dir build/linux --output-on-failure
}

lidar() {
    local dir=contrib/lidar-parks
    if [[ ! -f $dir/requirements.txt ]]; then
        echo "LiDAR parks: not on this branch, skipped."
        return
    fi
    # Its own virtual environment, so nothing is installed into the system's Python.
    local venv=build/lidar-venv
    [[ -x $venv/bin/python ]] || python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q -r "$dir/requirements.txt" pytest
    (cd "$dir" && "../../$venv/bin/python" -m pytest -q tests)
}

mapbuilder() {
    local venv=build/mapbuilder-venv
    [[ -x $venv/bin/python ]] || python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q pytest
    (cd contrib/mapbuilder && "../../$venv/bin/python" -m pytest -q tests)
}

case "${1:-all}" in
    linux) linux ;;
    lidar) lidar ;;
    mapbuilder) mapbuilder ;;
    all) linux && lidar && mapbuilder ;;
    *) echo "usage: ci/local.sh [linux|lidar|mapbuilder|all]" >&2; exit 2 ;;
esac
