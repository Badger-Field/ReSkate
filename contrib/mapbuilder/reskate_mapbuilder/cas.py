"""CAS block streams (port of Engine/Resource/cas_codec.cpp).

Only raw (uncompressed) blocks are written: the game reads them, and the map builder then
needs no compressor. Decoding reads raw and zlib blocks anywhere, and Oodle blocks (most of
the game's own data) on Windows through the game's own signed oo2core DLL, loaded with
ctypes, as ReSkate's C++ does.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import zlib
from pathlib import Path

_oodle = None


def _oodle_decoder(game_root):
    global _oodle
    if _oodle is None:
        if sys.platform != "win32":
            raise RuntimeError("Oodle CAS data can only be decoded on Windows (the game's oo2core DLL)")
        if not game_root:
            raise RuntimeError("Oodle CAS data needs the game directory")
        matches = sorted(p for p in Path(game_root).iterdir()
                         if p.name.lower().startswith("oo2core_") and p.name.lower().endswith("_win64.dll"))
        if not matches:
            raise RuntimeError("The game's Oodle codec was not found")
        dll = ctypes.WinDLL(str(matches[-1]))
        fn = dll.OodleLZ_Decompress
        fn.restype = ctypes.c_int64
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_int64, ctypes.c_int]
        _oodle = fn
    return _oodle


def _oodle_decode(block: bytes, size: int, game_root) -> bytes:
    fn = _oodle_decoder(game_root)
    out = ctypes.create_string_buffer(size)
    if fn(block, len(block), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3) != size:
        raise RuntimeError("Oodle CAS block failed to decode")
    return out.raw

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


def decode_cas(encoded: bytes, game_root: str | os.PathLike | None = None) -> bytes:
    """Decodes a block stream: raw, zlib, and (on Windows, with game_root) Oodle blocks."""
    return decode_raw(encoded, game_root, allow_compressed=True)


def decode_raw(encoded: bytes, game_root=None, allow_compressed: bool = False) -> bytes:
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
        kind = compression & 0x7F
        block = encoded[at + 8: at + 8 + encoded_size]
        if kind == 0x00:
            if len(block) != decoded_size:
                raise ValueError("Raw CAS block size does not match its header")
            out += block
        elif not allow_compressed:
            raise ValueError(f"CAS block uses compression {kind:#x}; use decode_cas")
        elif kind == 0x02:
            out += zlib.decompress(block)
        elif kind in (0x11, 0x15, 0x19):
            out += _oodle_decode(block, decoded_size, game_root)
        else:
            raise ValueError(f"Unsupported CAS compression type {kind:#x}")
        at += 8 + encoded_size
    return bytes(out)
