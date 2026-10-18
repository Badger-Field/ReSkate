import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reskate_mapbuilder.cas import decode_raw, encode_raw  # noqa: E402


def test_raw_round_trip_and_block_header():
    for size in (0, 1, 0xFFFF, 0x10000, 0x10001, 300000):
        data = bytes(i * 7 & 0xFF for i in range(size))
        encoded = encode_raw(data)
        assert decode_raw(encoded) == data
    # One 5-byte block: decoded size, compression 0 with flags 0x70, encoded size.
    assert encode_raw(b"abcde")[:8] == bytes([0, 0, 0, 5, 0x00, 0x70, 0, 5])
