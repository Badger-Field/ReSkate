# The Windows check CI runs, run here: the same commands .github/workflows/build.yml runs on
# windows-2022, because the workflow calls this script. Needs Visual Studio 2022 (C++ desktop
# workload) and CMake; run it from a Developer PowerShell or a plain one.
#
#   powershell -ExecutionPolicy Bypass -File ci\local.ps1
#
# Builds build\vs2022-x64 (Release) with the launcher tests, then runs every test. Nothing is
# uploaded; the built ReSkateLauncher.exe, ReSkate.dll and ReSkateServer.exe stay in
# build\vs2022-x64\Release.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step([string]$what, [scriptblock]$run) {
    Write-Host "== $what" -ForegroundColor Cyan
    & $run
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

# No crash uploads from CI builds.
Step "Configure" { cmake --preset vs2022-x64 "-DDINGOSDK_BACKTRACE_URL=" -DDINGOSDK_BUILD_LAUNCHER_TESTS=ON }
Step "Build" { cmake --build --preset release --parallel }
Step "Test" { ctest --test-dir build/vs2022-x64 -C Release --output-on-failure }
Write-Host "Windows checks passed." -ForegroundColor Green
