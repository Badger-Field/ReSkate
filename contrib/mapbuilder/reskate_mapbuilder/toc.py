"""Superbundle TOC reader and patch writer (port of Engine/Resource/toc.cpp).

The writer rebuilds the bundle and chunk perfect hashes, which is what makes a TOC
resolvable by the engine. Names are written uncompressed (flag 4 is refused).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .bundle import CasIdentifier, Guid

TOC_ENVELOPE_SIZE = 0x22C
_FNV_PRIME = 0x01000193
_COMPRESSED_NAMES = 4


@dataclass
class TocBundle:
    name: str
    region: bytes
    load_flag: int = 1


@dataclass
class TocChunk:
    guid: Guid
    location: CasIdentifier = field(default_factory=CasIdentifier)
    offset: int = 0
    size: int = 0
    removed: bool = False


@dataclass
class TocDocument:
    flags: int = 0
    bundles: list[TocBundle] = field(default_factory=list)
    chunks: list[TocChunk] = field(default_factory=list)
    bundle_hash_map: list[int] = field(default_factory=list)
    chunk_hash_map: list[int] = field(default_factory=list)


def toc_hash(key: bytes, seed: int = 0x811C9DC5) -> int:
    """FNV-1 over the key's bytes taken as signed chars, then modulo the prime."""
    for byte in key:
        signed = byte - 256 if byte >= 128 else byte
        seed = ((seed * _FNV_PRIME) & 0xFFFFFFFF) ^ (signed & 0xFFFFFFFF)
    return seed % _FNV_PRIME


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _lower(name: str) -> bytes:
    # std::tolower in the C locale: ASCII only.
    return bytes(b + 32 if 65 <= b <= 90 else b for b in name.encode("latin-1"))


def bundle_hash(superbundle: str) -> int:
    """The BundleHash an OnDemandBundle entry carries for a superbundle path.

    djb2 with xor (h = h * 33 ^ c, seed 5381) over the lower-cased path,
    matched against every entry in dingolevel_root's description."""
    value = 5381
    for byte in _lower(superbundle):
        value = ((value * 33) & 0xFFFFFFFF) ^ byte
    return value


def perfect_hash(keys: list[bytes]) -> tuple[list[int], list[int]]:
    """Returns (map, order): slot i of the table holds keys[order[i]]."""
    size = len(keys)
    hash_map = [-1] * size
    order = [-1] * size
    if size == 0:
        return hash_map, order
    if len(set(keys)) != size:
        raise ValueError("TOC perfect-hash keys must be unique")
    buckets: list[list[int]] = [[] for _ in range(size)]
    for index, key in enumerate(keys):
        buckets[toc_hash(key) % size].append(index)
    buckets.sort(key=len, reverse=True)  # stable, like std::stable_sort with ">"
    used = [False] * size
    bucket_index = 0
    while bucket_index < len(buckets) and len(buckets[bucket_index]) > 1:
        bucket = buckets[bucket_index]
        seed = 1
        while True:
            indices: list[int] = []
            collision = False
            for member in bucket:
                slot = toc_hash(keys[member], seed) % size
                if used[slot] or slot in indices:
                    collision = True
                    break
                indices.append(slot)
            if not collision:
                break
            if seed == 0xFFFFFFFF:
                raise RuntimeError("could not build TOC perfect hash")
            seed += 1
        hash_map[toc_hash(keys[bucket[0]]) % size] = seed
        for member, slot in zip(bucket, indices):
            order[slot] = member
            used[slot] = True
        bucket_index += 1
    free = 0
    while bucket_index < len(buckets) and buckets[bucket_index]:
        while used[free]:
            free = (free + 1) % size
        member = buckets[bucket_index][0]
        hash_map[toc_hash(keys[member]) % size] = -free - 1
        order[free] = member
        used[free] = True
        free = (free + 1) % size
        bucket_index += 1
    return hash_map, order


def hash_lookup(hash_map: list[int], ordered_keys: list[bytes], key: bytes) -> int | None:
    if not hash_map or len(hash_map) != len(ordered_keys):
        return None
    value = hash_map[toc_hash(key) % len(hash_map)]
    index = -value - 1 if value < 0 else toc_hash(key, value) % len(hash_map)
    if index >= len(ordered_keys) or ordered_keys[index] != key:
        return None
    return index


def write_patch_toc(bundles: list[TocBundle], chunks: list[TocChunk] | None = None, flags: int = 3) -> bytes:
    chunks = chunks or []
    if flags & _COMPRESSED_NAMES:
        raise ValueError("patch TOC writer uses uncompressed names")
    bundle_map, bundle_order = perfect_hash([_lower(b.name) for b in bundles])
    chunk_map, chunk_order = perfect_hash([c.guid.bytes for c in chunks])

    names = bytearray()
    name_offsets = []
    for source in bundle_order:
        name_offsets.append(len(names))
        names += bundles[source].name.encode("latin-1") + b"\0"

    chunk_words: list[int] = []
    chunk_table = bytearray()
    for source in chunk_order:
        chunk = chunks[source]
        chunk_table += chunk.guid.bytes[::-1]
        if chunk.removed:
            chunk_table += struct.pack(">i", -1)
        else:
            if len(chunk_words) > 0xFFFFFF:
                raise OverflowError("TOC chunk data index exceeds 24 bits")
            chunk_table += struct.pack(">I", (0x80 << 24) | len(chunk_words))
            ident = chunk.location.encoded()
            chunk_words += [ident >> 32, ident & 0xFFFFFFFF, chunk.offset, chunk.size]

    position = 48
    bundle_hash_offset = position
    position = _align(position + len(bundles) * 4, 8)
    bundle_data_offset = position
    position = _align(position + len(bundles) * 16, 4)
    chunk_hash_offset = position
    position = _align(position + len(chunks) * 4, 4)
    chunk_guid_offset = position
    position = _align(position + len(chunk_table), 4)
    chunk_data_offset = position
    position += len(chunk_words) * 4
    names_offset = position
    position = _align(position + len(names), 4)
    region_offsets = []
    for source in bundle_order:
        region_offsets.append(position)
        position = _align(position + len(bundles[source].region), 4)
    if position > 0xFFFFFFFF:
        raise OverflowError("TOC exceeds 4 GiB")

    out = bytearray(TOC_ENVELOPE_SIZE + position)
    out[0:4] = b"\x00\xd1\xce\x01"
    struct.pack_into(">IIiIIiIIIIiI", out, TOC_ENVELOPE_SIZE,
                     bundle_hash_offset, bundle_data_offset, len(bundles),
                     chunk_hash_offset if chunks else 0, chunk_guid_offset if chunks else 0, len(chunks),
                     chunk_data_offset, chunk_data_offset, names_offset, chunk_data_offset,
                     len(chunk_words), flags)
    base = TOC_ENVELOPE_SIZE
    for i, value in enumerate(bundle_map):
        struct.pack_into(">i", out, base + bundle_hash_offset + i * 4, value)
    for i, source in enumerate(bundle_order):
        region = bundles[source].region
        if len(region) > 0x3FFFFFFF:
            raise OverflowError("TOC bundle region exceeds 30-bit size")
        struct.pack_into(">IIQ", out, base + bundle_data_offset + i * 16, name_offsets[i],
                         (bundles[source].load_flag << 30) | len(region), region_offsets[i])
    if chunks:
        for i, value in enumerate(chunk_map):
            struct.pack_into(">i", out, base + chunk_hash_offset + i * 4, value)
        out[base + chunk_guid_offset: base + chunk_guid_offset + len(chunk_table)] = chunk_table
        for i, word in enumerate(chunk_words):
            struct.pack_into(">I", out, base + chunk_data_offset + i * 4, word)
    out[base + names_offset: base + names_offset + len(names)] = names
    for i, source in enumerate(bundle_order):
        region = bundles[source].region
        start = base + region_offsets[i]
        out[start:start + len(region)] = region
    return bytes(out)


class _HuffmanNames:
    """Huffman-coded bundle names (port of HuffmanNames in toc.cpp)."""

    def __init__(self, body, names_offset, names_count, table_offset, table_count):
        self.words = struct.unpack_from(f">{names_count}I", body, names_offset)
        values = struct.unpack_from(f">{table_count}I", body, table_offset)
        self.nodes = []  # (value, left, right); left is None for a leaf
        by_value = {}
        left = None
        counter = 0
        self.root = None
        for value in values:
            node = by_value.get(value)
            if node is None:
                node = len(self.nodes)
                self.nodes.append((value, None, None))
                by_value[value] = node
            if left is None:
                left = node
                continue
            parent = len(self.nodes)
            self.nodes.append((counter, left, node))
            by_value.setdefault(counter, parent)
            self.root = parent
            counter += 1
            left = None
        if left is not None:
            raise ValueError("TOC Huffman tree has an unmatched branch")

    def decode(self, bit: int) -> str:
        if self.root is None:
            return ""
        total = len(self.words) * 32
        out = []
        while bit < total:
            node = self.root
            while self.nodes[node][1] is not None:
                if bit >= total:
                    raise ValueError("TOC Huffman name exceeds its bitstream")
                one = (self.words[bit >> 5] >> (bit & 31)) & 1
                node = self.nodes[node][2] if one else self.nodes[node][1]
                bit += 1
            char = (~self.nodes[node][0]) & 0xFFFF
            if char == 0:
                return "".join(out)
            if char > 0x7F:
                raise ValueError("TOC Huffman name is not ASCII")
            out.append(chr(char))
        raise ValueError("TOC Huffman name is unterminated")


def read_toc(data: bytes, external_bundle_data: bytes = b"") -> TocDocument:
    if len(data) < TOC_ENVELOPE_SIZE + 48 or data[:4] != b"\x00\xd1\xce\x01":
        raise ValueError("TOC envelope is invalid")
    body = data[TOC_ENVELOPE_SIZE:]
    (bundle_hash_offset, bundle_data_offset, bundle_count, chunk_hash_offset, chunk_guid_offset,
     chunk_count, _, _, names_offset, chunk_data_offset, data_count, flags) = struct.unpack_from(">IIiIIiIIIIiI", body, 0)
    if bundle_count < 0 or chunk_count < 0 or data_count < 0:
        raise ValueError("TOC has negative table counts")
    huffman = None
    if flags & _COMPRESSED_NAMES:
        names_count, table_count, table_offset = struct.unpack_from(">III", body, 48)
        huffman = _HuffmanNames(body, names_offset, names_count, table_offset, table_count)
    doc = TocDocument(flags=flags)
    doc.bundle_hash_map = list(struct.unpack_from(f">{bundle_count}i", body, bundle_hash_offset))
    for i in range(bundle_count):
        name_offset, size_and_flag, region_offset = struct.unpack_from(">IIQ", body, bundle_data_offset + i * 16)
        size = size_and_flag & 0x3FFFFFFF
        load_flag = size_and_flag >> 30
        source = body if load_flag == 1 else external_bundle_data
        if region_offset + size > len(source):
            raise ValueError("TOC bundle region exceeds the file")
        if huffman:
            name = huffman.decode(name_offset)
        else:
            start = names_offset + name_offset
            name = body[start:body.index(b"\0", start)].decode("latin-1")
        doc.bundles.append(TocBundle(name, bytes(source[region_offset:region_offset + size]), 1))
    if chunk_count:
        doc.chunk_hash_map = list(struct.unpack_from(f">{chunk_count}i", body, chunk_hash_offset))
        words = struct.unpack_from(f">{data_count}I", body, chunk_data_offset)
        for i in range(chunk_count):
            at = chunk_guid_offset + i * 20
            guid = Guid(bytes(body[at:at + 16])[::-1])
            (encoded,) = struct.unpack_from(">i", body, at + 16)
            if encoded == -1:
                doc.chunks.append(TocChunk(guid, removed=True))
                continue
            flag = (encoded & 0xFFFFFFFF) >> 24
            word = encoded & 0xFFFFFF
            if flag != 0x80 or word + 4 > len(words):
                raise ValueError("TOC chunk data reference is invalid")
            ident = (words[word] << 32) | words[word + 1]
            doc.chunks.append(TocChunk(guid, CasIdentifier.decoded(ident), words[word + 2], words[word + 3]))
    return doc


def verify_toc(doc: TocDocument) -> None:
    keys = [_lower(b.name) for b in doc.bundles]
    for index, key in enumerate(keys):
        if hash_lookup(doc.bundle_hash_map, keys, key) != index:
            raise RuntimeError("TOC bundle perfect hash does not resolve")
    chunk_keys = [c.guid.bytes for c in doc.chunks]
    for index, key in enumerate(chunk_keys):
        if hash_lookup(doc.chunk_hash_map, chunk_keys, key) != index:
            raise RuntimeError("TOC chunk perfect hash does not resolve")
