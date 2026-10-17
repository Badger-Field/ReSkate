# The Windows check CI runs, run here: the same commands .github/workflows/build.yml runs on
# windows-2022, because the workflow calls this script. Needs Visual Studio 2022 (C++ desktop
# workload) and CMake.
#
#   powershell -ExecutionPolicy Bypass -File ci\local.ps1          Visual Studio build
#   powershell -ExecutionPolicy Bypass -File ci\local.ps1 -Ninja   what CI runs: Ninja, cached
#
# The Visual Studio build goes to build\vs2022-x64\Release. The Ninja build (build\ninja-x64)
# loads the compiler from Visual Studio itself, and when sccache is on the PATH it reuses compiled
# files from earlier runs, so a change to a few files rebuilds only those. Either way the launcher
# tests are built and every test runs. Nothing is uploaded.
param([switch]$Ninja)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step([string]$what, [scriptblock]$run) {
    Write-Host "== $what" -ForegroundColor Cyan
    & $run
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

# The x64 compiler's environment, as a Developer PowerShell has it, when this one does not.
function Use-VisualStudio {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) { return }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vs) { throw "Visual Studio 2022 with the C++ desktop workload was not found" }
    $devcmd = Join-Path $vs "Common7\Tools\VsDevCmd.bat"
    foreach ($line in & cmd.exe /c "`"$devcmd`" -arch=amd64 -host_arch=amd64 -no_logo && set") {
        if ($line -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
    }
}

# No crash uploads from CI builds: built with no endpoint, and every process the tests start is
# told not to upload (RESKATE_NO_CRASH_UPLOAD, Engine/Core/Debug/protocol.h) whatever it was built with.
$env:RESKATE_NO_CRASH_UPLOAD = "1"
$options = @("-DDINGOSDK_CRASH_REPORT_URL=", "-DDINGOSDK_BUILD_LAUNCHER_TESTS=ON")
if ($Ninja) {
    Use-VisualStudio
    if (Get-Command sccache.exe -ErrorAction SilentlyContinue) {
        $options += @("-DCMAKE_C_COMPILER_LAUNCHER=sccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=sccache")
    }
    Step "Configure" { cmake --preset ninja-x64 @options }
    Step "Build" { cmake --build build/ninja-x64 --parallel }
    Step "Test" { ctest --test-dir build/ninja-x64 --output-on-failure }
    if (Get-Command sccache.exe -ErrorAction SilentlyContinue) { sccache --show-stats }
} else {
    Step "Configure" { cmake --preset vs2022-x64 @options }
    Step "Build" { cmake --build --preset release --parallel }
    Step "Test" { ctest --test-dir build/vs2022-x64 -C Release --output-on-failure }
}
Write-Host "Windows checks passed." -ForegroundColor Green
