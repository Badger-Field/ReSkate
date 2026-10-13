"""Write a release's launcher.json: what the launcher and server read from the latest release to
update themselves and to fetch the supported game build (Launcher/updater.cpp, parse_config).

    python .github/scripts/launcher_json.py --version 2.1.0 \
        --launcher ReSkateLauncher.exe --runtime ReSkate.dll --out launcher.json

The game section is read from Engine/Game/Build/supported_build.h, so it always matches the build
the binaries were compiled for. Files are referenced as release assets (asset:<name>)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_BUILD = ROOT / "Engine" / "Game" / "Build" / "supported_build.h"

# DepotDownloader, which the launcher uses to fetch the supported game build. Pinned here; bump
# the version, URL, hash and size together after checking the new release.
DEPOT_DOWNLOADER = {
    "version": "3.4.0",
    "url": "https://github.com/SteamRE/DepotDownloader/releases/download/DepotDownloader_3.4.0/DepotDownloader-windows-x64.zip",
    "sha256": "41c9e9f0df54b3ad02e67a11726756e5c73283bd7c2e1b04acfa5ae4c2ed3767",
    "size": 33474005,
}
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_file(path: Path, version: str) -> dict:
    size = path.stat().st_size
    if not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise SystemExit(f"{path}: size {size} is out of range")
    return {"version": version, "url": f"asset:{path.name}", "sha256": sha256(path), "size": size}


def game_section(header: Path = SUPPORTED_BUILD) -> dict:
    text = header.read_text(encoding="utf-8")

    def constant(name: str) -> str:
        found = re.search(rf"\b{name}\s*=\s*\"?([0-9a-f]+)\"?\s*;", text)
        if not found:
            raise SystemExit(f"{header}: no {name}")
        return found.group(1)

    game = {"app_id": int(constant("steam_app_id")), "depot_id": int(constant("steam_depot_id")),
            "manifest_id": constant("steam_manifest_id"), "build_id": constant("steam_build_id"),
            "skate_sha256": re.search(r"game_sha256\s*=\s*\"([0-9a-f]{64})\"", text).group(1)}
    return game


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--launcher", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise SystemExit(f"--version {args.version!r}: use X.Y.Z")
    config = {
        "schema": 1,
        "launcher": remote_file(args.launcher, args.version),
        "runtime": remote_file(args.runtime, args.version),
        "game": game_section(),
        "depot_downloader": DEPOT_DOWNLOADER,
    }
    args.out.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    print(json.dumps(config, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
