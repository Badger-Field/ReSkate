# Releasing

Releases are built by GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml)),
not on anyone's PC, so every release comes from a known commit.

## Every change: the Build workflow

[build.yml](.github/workflows/build.yml) runs on every pull request and every push to `main`:

| Job | What it does |
|---|---|
| **Windows (MSVC)** | Builds the launcher, `ReSkate.dll` and the server the way releases are built, runs the launcher tests, and keeps the binaries for 14 days as an artifact, for testing a PR in game. |
| **Linux** | Builds the dedicated server and runs the regression tests. |
| **LiDAR parks** | Runs the Python tests in `contrib/lidar-parks` (when it is there). |

Merge only when it is green.

### Running the same checks on your own PC

Each Build job is a script in `ci/`, and the workflow only calls it, so a local run checks exactly what CI checks, uses no Actions minutes, and needs nothing pushed:

- **Linux, or WSL on Windows:** `ci/local.sh` runs the server and regression tests (`build/linux`) and then the LiDAR parks tests. `ci/local.sh linux` or `ci/local.sh lidar` runs just one of them. The Python tests get their own virtual environment, in `build/lidar-venv`.
- **Windows:** `powershell -ExecutionPolicy Bypass -File ci\local.ps1` builds `build\vs2022-x64` (Release) with the launcher tests, and runs every test. It needs Visual Studio 2022 with the C++ desktop workload.

Changes only to Markdown files or `LICENSE` start no build.

## Cutting a release

1. **Pick the version.** It must be `X.Y.Z`. This fork continues from upstream's last release (2.0.7), so
   it starts at **2.1.0**.
2. **Start the build.** Tag `main` and push the tag:

   ```
   git tag v2.1.0 && git push origin v2.1.0
   ```

   Or use **Actions → Release → Run workflow**, enter `2.1.0` and pick `main`.
3. **Check the draft.** The workflow builds and tests, then creates a **draft** release `v2.1.0` with:

   | Asset | What it is |
   |---|---|
   | `ReSkate-2.1.0.zip` | what players download: the launcher, `ReSkate.dll` and `GameNetworkingSockets.dll` |
   | `ReSkateLauncher.exe`, `ReSkate.dll` | what the launcher updates itself from |
   | `GameNetworkingSockets.dll` | direct multiplayer's library (see below) |
   | `launcher.json` | the update manifest: the files above by SHA-256, the supported game build, DepotDownloader |
   | `SHA256SUMS.txt` | hashes of everything above |
   | `ReSkate-2.1.0-symbols.zip` | PDBs, for reading crash dumps |

   Read the generated notes and add what players should know.
4. **Publish.** Press **Publish release**. Only then does it become *latest*, and the launchers out
   there update to it. A draft is never seen by the updater.

### How the build is set up for this repository

A release build sets these options:
- `DINGOSDK_RELEASE_REPO` is **this** repository (`github.repository`). Our launchers update from our releases. Without it they would follow upstream (`Dingo-Shenanigans/ReSkate`) and lose this fork's features.
- `DINGOSDK_LAUNCHER_AUTO_UPDATE=ON`. Only release builds replace themselves; local builds never do.
- `DINGOSDK_VERSION` comes from the tag.
- **Crash reports** go to the URL in the repository secret `RESKATE_BACKTRACE_URL` when it is set. That is a Backtrace *submission* URL, never an API token. With no secret they are **off**. Upstream's built-in Backtrace project is never used.

## Direct multiplayer's GameNetworkingSockets.dll

ReSkate loads only the exact `GameNetworkingSockets.dll` whose SHA-256 is pinned in
`Extension/Multiplayer/Direct/direct_play.cpp` (`library_sha256`). That build is Valve's
GameNetworkingSockets `d534c19`, static, with ICE. It is not rebuilt for each release. It is kept
once, as an asset of a release tagged `gns-<commit>`, and every release copies it from there after
checking its hash against the pin.

**One-time setup:**
1. Create a release tagged `gns-d534c19`, marked as a pre-release so the updater ignores it.
2. Attach the tested `GameNetworkingSockets.dll` to it.

When the pin changes, add a new `gns-<commit>` release with the new DLL.

## Not in releases yet

- **The dedicated server.** Its packages need Valve's Steam redistributables (`steam_api64.dll`, `steamclient64.dll`, `libsteam_api.so`, `steamclient.so`). Those are not in git and cannot be fetched anonymously in CI. Without a `server` section in `launcher.json`, servers do not self-update from this repository.
- **`GameNetworkingSockets.dll` through the launcher's self-update.** The updater only replaces the launcher and `ReSkate.dll`, so players get the DLL from the zip.
