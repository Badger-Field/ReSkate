"""CAS block streams (port of the raw path of Engine/Resource/cas_codec.cpp).

Only raw (uncompressed) blocks are written: the game reads them, and the map builder
then needs no compressor. Decoding handles raw blocks only; compressed game data is
read by ReSkate's C++ code, not here.
"""
from __future__ import annotations

import struct

BLOCK_SIZE = 0x10000


def encode_raw(decoded: bytes, block_size: int = BLOCK_SIZE) -> bytes:
    if not 1 <= block_size <= 0x10000:
        raise ValueError("CAS block size must be between 1 and 65536 bytes")
    out = bytearray()
    count = max(1, -(-len(decoded) // block_size))
    for index in range(count):
        block = decoded[index * block_size:(index + 1) * block_size]
        packed = ((len(block) & 0xFFFFFF) << 32) | (0x00 << 24) | (0x7 << 20) | len(block)
        out += struct.pack(">Q", packed) + block
    return bytes(out)


def decode_raw(encoded: bytes) -> bytes:
    out = bytearray()
    at = 0
    while at < len(encoded):
        if len(encoded) - at < 8:
            raise ValueError("Truncated CAS block header")
        (decoded_size,) = struct.unpack_from(">I", encoded, at)
        (compression,) = struct.unpack_from("<H", encoded, at + 4)
        (encoded_size,) = struct.unpack_from(">H", encoded, at + 6)
        encoded_size += ((compression >> 8) & 0x0F) << 16
        decoded_size &= 0x00FFFFFF
        if compression & 0x7F != 0:
            raise ValueError(f"CAS block uses compression {compression & 0x7F:#x}; only raw blocks are supported here")
        block = encoded[at + 8: at + 8 + encoded_size]
        if len(block) != decoded_size:
            raise ValueError("Raw CAS block size does not match its header")
        out += block
        at += 8 + encoded_size
    return bytes(out)
