"""Writes THIRD-PARTY-NOTICES.txt for a release: the license of every library in External/ that ships
in ReSkate's binaries (External/README.md asks for them beside the binaries), then any extra notices
files given (GameNetworkingSockets.dll's, when it ships).

    python .github/scripts/third_party_notices.py --out release/THIRD-PARTY-NOTICES.txt [--extra FILE ...]
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Library, its license files under External/. zstd is used under BSD, so its GPLv2 COPYING is left out.
LIBRARIES = [
    ("Dear ImGui", ["imgui/LICENSE.txt"]),
    ("Microsoft Detours", ["detours/LICENSE.txt"]),
    ("Tencent RapidJSON", ["rapidjson/LICENSE.txt"]),
    ("spdlog", ["spdlog/LICENSE"]),
    ("SQLite", ["sqlite/LICENSE.txt"]),
    ("Valve GameNetworkingSockets headers", ["steam_networking/LICENSE"]),
    ("miniz", ["miniz/LICENSE.txt"]),
    ("bcdec", ["bcdec/LICENSE.txt"]),
    ("LZ4", ["lz4/LICENSE"]),
    ("Zstandard", ["zstd/LICENSE"]),
    ("Monocypher", ["monocypher/LICENCE.md"]),
    ("Luau", ["luau/LICENSE.txt", "luau/lua_LICENSE.txt"]),
    ("Montserrat font", ["fonts/Montserrat-LICENSE.txt"]),
    ("Permanent Marker font", ["fonts/PermanentMarker-LICENSE.txt"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--extra", action="append", default=[], help="another notices file to append")
    args = parser.parse_args()
    rule = "=" * 78
    parts = ["ReSkate includes the following third-party software, under these licenses.\n"
             "ReSkate itself is licensed under the GNU GPL v3 (LICENSE).\n"]
    for name, files in LIBRARIES:
        for relative in files:
            text = (ROOT / "External" / relative).read_text(encoding="utf-8").strip()
            parts.append(f"{rule}\n{name} (External/{relative})\n{rule}\n\n{text}\n")
    for extra in args.extra:
        parts.append(f"{rule}\n{Path(extra).name}\n{rule}\n\n{Path(extra).read_text(encoding='utf-8').strip()}\n")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(parts), encoding="utf-8", newline="\r\n")
    print(f"Wrote {args.out}: {len(LIBRARIES)} libraries, {len(args.extra)} extra")


if __name__ == "__main__":
    main()
