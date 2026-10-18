"""Bundle regions and binary bundle manifests (port of Engine/Resource/binary_bundle.cpp)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

_SALT = 0x7065636E
_STANDARD_MAGIC = 0xED1CEDB8


@dataclass(frozen=True)
class Guid:
    """16 bytes in the native little-endian field order (as stored in bundles)."""
    bytes: bytes = b"\0" * 16

    def __post_init__(self):
        if len(self.bytes) != 16:
            raise ValueError("GUID must be 16 bytes")

    @staticmethod
    def parse(text: str) -> "Guid":
        """The inverse of string()."""
        b = bytes.fromhex(text.replace("-", ""))
        if len(b) != 16:
            raise ValueError("GUID text must hold 32 hex digits")
        return Guid(b[3::-1] + b[5:3:-1] + b[7:5:-1] + b[8:])

    def string(self) -> str:
        b = self.bytes
        canonical = b[3::-1] + b[5:3:-1] + b[7:5:-1] + b[8:]
        h = canonical.hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


@dataclass(frozen=True)
class CasIdentifier:
    patch: bool = False
    install_chunk: int = 0
    archive: int = 0

    def encoded(self) -> int:
        return ((1 if self.patch else 0) << 48) | (self.install_chunk << 16) | self.archive

    @staticmethod
    def decoded(value: int) -> "CasIdentifier":
        return CasIdentifier((value & (0xFF << 48)) != 0, (value >> 16) & 0xFFFFFFFF, value & 0xFFFF)


@dataclass
class BundleFileInfo:
    location: CasIdentifier
    offset: int
    size: int


@dataclass
class BundleRegion:
    files: list[BundleFileInfo] = field(default_factory=list)
    inline_manifest: bytes = b""


EBX, RESOURCE, CHUNK = "ebx", "res", "chunk"


@dataclass
class BundleAsset:
    kind: str
    name: str = ""
    sha1: bytes = b"\0" * 20
    guid: Guid = field(default_factory=Guid)
    resource_id: int = 0
    resource_type: int = 0
    resource_meta: bytes = b"\0" * 16
    original_size: int = 0
    logical_offset: int = 0
    logical_size: int = 0


@dataclass
class BinaryBundle:
    ebx: list[BundleAsset] = field(default_factory=list)
    resources: list[BundleAsset] = field(default_factory=list)
    chunks: list[BundleAsset] = field(default_factory=list)
    chunk_metadata: bytes = b""


def read_bundle_region(data: bytes, offset: int = 0) -> BundleRegion:
    if offset > len(data) or len(data) - offset < 0x24:
        raise ValueError("Bundle region header is truncated")
    (metadata_offset, metadata_size, location_offset, total, data_offset, resource_offset,
     chunk_offset, _, repeated) = struct.unpack_from(">iiIiIIIIi", data, offset)
    if (metadata_offset < 0 or metadata_size < 0 or total < 0 or total != repeated
            or resource_offset != data_offset or chunk_offset != data_offset):
        raise ValueError("Bundle region header is invalid")
    if (metadata_offset == 0) != (metadata_size == 0):
        raise ValueError("Bundle region inline metadata range is invalid")
    region = BundleRegion()
    if metadata_size:
        region.inline_manifest = bytes(data[offset + metadata_offset: offset + metadata_offset + metadata_size])
    at = offset + data_offset
    current = None
    for index in range(total):
        flag = data[offset + location_offset + index]
        if flag == 1:
            (encoded,) = struct.unpack_from(">I", data, at); at += 4
            current = CasIdentifier((encoded & 0x00FF0000) != 0, (encoded >> 8) & 0xFF, encoded & 0xFF)
        elif flag != 0:
            (encoded,) = struct.unpack_from(">Q", data, at); at += 8
            current = CasIdentifier.decoded(encoded)
        if current is None:
            raise ValueError("First bundle file has no CAS identifier")
        file_offset, file_size = struct.unpack_from(">II", data, at); at += 8
        region.files.append(BundleFileInfo(current, file_offset, file_size))
    return region


def write_bundle_region(files: list[BundleFileInfo], inline_manifest: bytes = b"") -> bytes:
    table = bytearray()
    flags = bytearray()
    previous = None
    for f in files:
        if previous is not None and previous == f.location:
            flags.append(0)
        else:
            flags.append(0x80)
            table += struct.pack(">Q", f.location.encoded())
            previous = f.location
        table += struct.pack(">II", f.offset, f.size)
    if flags:
        flags[0] |= 0x04
    data_offset = 0x24
    metadata_offset = 0 if not inline_manifest else data_offset + len(table)
    location_offset = (data_offset + len(table)) if not inline_manifest else metadata_offset + len(inline_manifest)
    header = struct.pack(">iiIiIIIIi", metadata_offset, len(inline_manifest), location_offset, len(files),
                         data_offset, data_offset, data_offset, 0, len(files))
    return header + bytes(table) + inline_manifest + bytes(flags)


def _string_at(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("latin-1")


def read_binary_bundle(data: bytes) -> BinaryBundle:
    if len(data) < 36:
        raise ValueError("Binary bundle header is truncated")
    (declared,) = struct.unpack_from(">I", data, 0)
    if declared != len(data) - 4:
        raise ValueError("Binary bundle size does not match its stream")
    endian = ">"
    (magic,) = struct.unpack_from(">I", data, 4)
    if magic ^ _SALT != _STANDARD_MAGIC:
        (magic,) = struct.unpack_from("<I", data, 4)
        endian = "<"
        if magic ^ _SALT != _STANDARD_MAGIC:
            raise ValueError("Binary bundle magic is unsupported")
    total, n_ebx, n_res, n_chunk, strings, metadata_offset, metadata_size = struct.unpack_from(endian + "7I", data, 8)
    if n_ebx + n_res + n_chunk != total:
        raise ValueError("Binary bundle asset counts do not add up")
    strings += 4
    at = 36
    hashes = [bytes(data[at + i * 20: at + i * 20 + 20]) for i in range(total)]
    at += total * 20
    bundle = BinaryBundle()
    for i in range(n_ebx):
        name_offset, size = struct.unpack_from(endian + "II", data, at); at += 8
        bundle.ebx.append(BundleAsset(EBX, _string_at(data, strings + name_offset), hashes[i], original_size=size))
    for i in range(n_res):
        name_offset, size = struct.unpack_from(endian + "II", data, at); at += 8
        bundle.resources.append(BundleAsset(RESOURCE, _string_at(data, strings + name_offset), hashes[n_ebx + i],
                                            original_size=size))
    for asset in bundle.resources:
        (asset.resource_type,) = struct.unpack_from(endian + "I", data, at); at += 4
    for asset in bundle.resources:
        asset.resource_meta = bytes(data[at:at + 16]); at += 16
    for asset in bundle.resources:
        (asset.resource_id,) = struct.unpack_from(endian + "Q", data, at); at += 8
    for i in range(n_chunk):
        raw = bytes(data[at:at + 16])
        guid = Guid(raw if endian == "<" else raw[::-1])
        logical_offset, logical_size = struct.unpack_from(endian + "II", data, at + 16); at += 24
        bundle.chunks.append(BundleAsset(CHUNK, guid.string(), hashes[n_ebx + n_res + i], guid=guid,
                                         original_size=(logical_offset & 0xFFFF) | logical_size,
                                         logical_offset=logical_offset, logical_size=logical_size))
    if metadata_size:
        start = 4 + metadata_offset
        bundle.chunk_metadata = bytes(data[start:start + metadata_size])
    return bundle


def write_binary_bundle(bundle: BinaryBundle) -> bytes:
    """Little-endian manifest, laid out exactly as ReSkate's writer does."""
    total = len(bundle.ebx) + len(bundle.resources) + len(bundle.chunks)
    strings = bytearray()
    name_offsets = []
    for asset in bundle.ebx + bundle.resources:
        name_offsets.append(len(strings))
        strings += asset.name.encode("latin-1") + b"\0"
    sha_start = 36
    ebx_start = sha_start + total * 20
    res_start = ebx_start + len(bundle.ebx) * 8
    res_type_start = res_start + len(bundle.resources) * 8
    res_meta_start = res_type_start + len(bundle.resources) * 4
    res_id_start = res_meta_start + len(bundle.resources) * 16
    chunk_start = res_id_start + len(bundle.resources) * 8
    string_start = chunk_start + len(bundle.chunks) * 24
    metadata_start = string_start + len(strings)
    length = metadata_start + len(bundle.chunk_metadata)
    out = bytearray()
    out += struct.pack(">I", length - 4)
    out += struct.pack("<IIIIIIII", _STANDARD_MAGIC ^ _SALT, total, len(bundle.ebx), len(bundle.resources),
                       len(bundle.chunks), string_start - 4,
                       0 if not bundle.chunk_metadata else metadata_start - 4, len(bundle.chunk_metadata))
    for asset in bundle.ebx + bundle.resources + bundle.chunks:
        if len(asset.sha1) != 20:
            raise ValueError("SHA-1 must be 20 bytes")
        out += asset.sha1
    for i, asset in enumerate(bundle.ebx):
        out += struct.pack("<II", name_offsets[i], asset.original_size & 0xFFFFFFFF)
    for i, asset in enumerate(bundle.resources):
        out += struct.pack("<II", name_offsets[len(bundle.ebx) + i], asset.original_size & 0xFFFFFFFF)
    for asset in bundle.resources:
        out += struct.pack("<I", asset.resource_type)
    for asset in bundle.resources:
        if len(asset.resource_meta) != 16:
            raise ValueError("Binary bundle resource metadata must be 16 bytes")
        out += asset.resource_meta
    for asset in bundle.resources:
        out += struct.pack("<Q", asset.resource_id)
    for asset in bundle.chunks:
        out += asset.guid.bytes + struct.pack("<II", asset.logical_offset, asset.logical_size)
    out += strings + bundle.chunk_metadata
    assert len(out) == length
    return bytes(out)
