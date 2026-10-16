"""The native DB container used by layout.toc and InitFS (port of Engine/Vfs/native_db.cpp)."""
from __future__ import annotations

from dataclasses import dataclass, field

ENVELOPE_SIZE = 0x22C
MAGIC = b"\x00\xd1\xce\x01"
_FIXED = {6: 1, 8: 4, 11: 4, 9: 8, 12: 8, 15: 16, 16: 20}


@dataclass
class Node:
    type: int = 0
    named: bool = False
    terminated: bool = False
    name: str = ""
    payload: bytes = b""
    children: list["Node"] = field(default_factory=list)

    @property
    def text(self) -> str:
        raw = self.payload[:-1] if self.payload.endswith(b"\0") else self.payload
        return raw.decode("latin-1")

    def field(self, key: str) -> "Node | None":
        for child in self.children:
            if child.named and child.name == key:
                return child
        return None


class _Reader:
    def __init__(self, data: bytes, unique_fields: bool, max_entries: int):
        self.data, self.pos, self.nodes = data, 0, 0
        self.unique, self.max = unique_fields, max_entries

    def take(self, n):
        if n > len(self.data) - self.pos:
            raise ValueError("Truncated DB value")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def variable(self):
        value, shift = 0, 0
        while shift < 63:
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
            shift += 7
        raise ValueError("Invalid DB variable length")

    def read(self, depth=0) -> Node:
        self.nodes += 1
        if depth > 32 or self.nodes > self.max:
            raise ValueError("DB nesting/entry limit exceeded")
        tag = self.take(1)[0]
        node = Node(type=tag & 31)
        if not node.type:
            return node
        node.named = not tag & 128
        if node.named:
            end = self.data.index(b"\0", self.pos)
            node.name = self.data[self.pos:end].decode("latin-1")
            self.pos = end + 1
        if node.type in (1, 2):
            size = self.variable()
            end = self.pos + size
            if end > len(self.data):
                raise ValueError("Truncated DB container")
            while self.pos < end:
                child = self.read(depth + 1)
                if not child.type:
                    node.terminated = True
                    break
                if self.unique and node.type == 2 and child.named and node.field(child.name):
                    raise ValueError("Duplicate DB field")
                node.children.append(child)
            if self.pos != end:
                raise ValueError("DB container length mismatch")
        elif node.type in (7, 19):
            node.payload = bytes(self.take(self.variable()))
        elif node.type in _FIXED:
            node.payload = bytes(self.take(_FIXED[node.type]))
        else:
            raise ValueError(f"Unsupported DB value type {node.type}")
        return node


def read(data: bytes, unique_fields: bool = True, max_entries: int = 1 << 20) -> Node:
    return _Reader(data, unique_fields, max_entries).read()


def _variable(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        if not value:
            out.append(byte)
            return bytes(out)
        out.append(byte | 128)


def write(node: Node) -> bytes:
    if not node.type:
        return b"\0"
    out = bytearray([node.type | (0 if node.named else 128)])
    if node.named:
        out += node.name.encode("latin-1") + b"\0"
    if node.type in (1, 2):
        body = b"".join(write(c) for c in node.children) + (b"\0" if node.terminated else b"")
        return bytes(out) + _variable(len(body)) + body
    if node.type in (7, 19):
        out += _variable(len(node.payload))
    return bytes(out) + node.payload


def read_layout(data: bytes) -> Node:
    if len(data) <= ENVELOPE_SIZE or data[:4] != MAGIC:
        raise ValueError("Unrecognized native envelope in layout.toc")
    return read(data[ENVELOPE_SIZE:], unique_fields=False)


DEFAULT_INSTALL_CHUNK = "configurations/layout/defaultinstallpackage"


def _ascii_lower(text: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _string(text: str, name: str | None = None) -> Node:
    return Node(7, name is not None, False, name or "", text.encode("latin-1") + b"\0")


def _insert_sorted(nodes: list[Node], added: Node, text_of) -> None:
    # The engine searches these lists by name, so an entry goes where the order puts it
    # (the C++ merge inserts the same way: before the first name that sorts after it).
    key = _ascii_lower(text_of(added))
    at = next((i for i, n in enumerate(nodes) if text_of(n) is not None and _ascii_lower(text_of(n)) > key), len(nodes))
    nodes.insert(at, added)


def layout_superbundles(root: Node) -> list[str]:
    listing = root.field("superBundles")
    names = [row.field("name") for row in (listing.children if listing else [])]
    return [n.text for n in names if n is not None and n.type == 7]


def install_chunk(root: Node, name: str) -> Node:
    manifest = root.field("installManifest")
    chunks = manifest.field("installChunks") if manifest else None
    for chunk in (chunks.children if chunks else []):
        found = chunk.field("name")
        if found is not None and found.type == 7 and _ascii_lower(found.text) == _ascii_lower(name):
            return chunk
    raise KeyError(f"layout has no install chunk {name}")


def add_superbundle(layout_bytes: bytes, name: str, chunk_name: str = DEFAULT_INSTALL_CHUNK) -> bytes:
    """layout.toc with a superbundle added to the top-level superBundles list and to the
    superbundles list of an install chunk (FORMAT-NOTES 6.1); the rest stays byte for byte."""
    root = read_layout(layout_bytes)
    listing = root.field("superBundles")
    if listing is None or listing.type != 1:
        raise ValueError("layout.toc has no superBundles list")
    if _ascii_lower(name) in {_ascii_lower(n) for n in layout_superbundles(root)}:
        raise ValueError(f"layout.toc already lists {name}")
    record = Node(2, False, True, "", b"", [_string(name, "name")])   # as native_db::make_named_record

    def row_name(row):
        found = row.field("name")
        return found.text if found is not None and found.type == 7 else None
    _insert_sorted(listing.children, record, row_name)
    held = install_chunk(root, chunk_name).field("superbundles")
    if held is None or held.type != 1:
        raise ValueError(f"install chunk {chunk_name} has no superbundles list")
    _insert_sorted(held.children, _string(name), lambda n: n.text if n.type == 7 else None)
    return layout_bytes[:ENVELOPE_SIZE] + write(root)
