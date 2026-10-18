import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reskate_mapbuilder.toc import bundle_hash  # noqa: E402

# OnDemandBundles entries read from levels/game/dingolevel_root/dingolevel_root/description.
KNOWN = {
    "levels/game/BAM_LevelRoot/BAM_LevelRoot": 1572312801,
    "levels/game/DingoLevel_FTUE_Island/DingoLevel_FTUE_Island": 424972353,
    "levels/game/DingoLevel_Isle_Of_Grom/DingoLevel_Isle_Of_Grom": 488422113,
    "Levels/Game/DingoLevel_MPR/DingoLevel_MPR": 797113697,
    "levels/game/dingolevel_reskate_skate3improved/dingolevel_reskate_skate3improved": 982491233,
}


def test_bundle_hash_matches_the_game():
    for path, expected in KNOWN.items():
        assert bundle_hash(path) == expected, path


def test_bundle_hash_ignores_case():
    assert bundle_hash("Levels/Game/X/X") == bundle_hash("levels/game/x/x")
