#!/usr/bin/env bash
# Which of the Build workflow's jobs a change needs, as key=value lines for $GITHUB_OUTPUT:
#
#   native=true|false   the Windows and Linux builds (C++ and everything they read)
#   lidar=true|false    the LiDAR parks Python tests
#
#   ci/changes.sh BASE [HEAD]    compare HEAD (default HEAD) with BASE
#   ci/changes.sh                no base: every job runs
#
# Docs (*.md, LICENSE) need no job. A change only to a Python tool's own folder skips the native
# builds: each folder in PYTHON_ONLY is read by nothing the C++ build compiles or tests. Anything
# else, including ci/ and .github/, runs every job. Each Python tool's tests run when its folder,
# ci/ or .github/ changes.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_ONLY=(contrib/lidar-parks/)

all() { echo "native=true"; echo "lidar=true"; }

if [[ $# -eq 0 ]]; then
    all
    exit 0
fi
if ! files=$(git diff --name-only "$1" "${2:-HEAD}" 2>/dev/null); then
    echo "ci/changes.sh: can't compare with $1; running every job" >&2
    all
    exit 0
fi

native=false
lidar=false
while IFS= read -r file; do
    [[ -z $file ]] && continue
    case $file in
        ci/* | .github/*) native=true; lidar=true; continue ;;
        *.md | LICENSE) continue ;;      # docs build nothing, as in the workflow's paths-ignore
        contrib/lidar-parks/*) lidar=true ;;
    esac
    python_only=false
    for dir in "${PYTHON_ONLY[@]}"; do
        [[ $file == "$dir"* ]] && python_only=true
    done
    $python_only || native=true
done <<< "$files"

echo "native=$native"
echo "lidar=$lidar"
