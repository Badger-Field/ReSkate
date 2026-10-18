"""Edits on read EBX documents: clone a document or an instance under new GUIDs, find objects by
type, append to arrays. Only what adding a level needs (FORMAT-NOTES 6.2).

New GUIDs are derived from text (an MD5 of it), so building the same mod twice gives the same
GUIDs and a dry run prints the GUIDs the real run writes.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any, Iterator

from .bundle import Guid
from .ebx import (BoxedReference, Document, ImportReference, InstanceRecord, Object, PointerKind, PointerReference,
                  array_element, pointer_target)


def derived_guid(*parts: str) -> Guid:
    return Guid(hashlib.md5("|".join(parts).encode("utf-8")).digest())


def type_name(doc: Document, obj: Object) -> str:
    return doc.types[obj.descriptor].name if 0 <= obj.descriptor < len(doc.types) else ""


@dataclass
class Found:
    """An object of the wanted type and where it sits: an instance (`instance` is its index), an
    element of an array (`array` is the list) or a struct field."""
    object: Object
    path: str
    instance: int = -1
    array: list | None = None


def find_objects(doc: Document, wanted: str) -> list[Found]:
    """Every instance and nested struct whose type is named `wanted` (ASCII case ignored)."""
    key = wanted.lower()
    out: list[Found] = []

    def walk(value: Any, path: str, array: list | None) -> None:
        if isinstance(value, Object):
            if type_name(doc, value).lower() == key:
                out.append(Found(value, path, array=array))
            for f in value.fields:
                walk(f.value, f"{path}.{f.name}", None)
        elif isinstance(value, list):
            for i, element in enumerate(value):
                walk(element, f"{path}[{i}]", value)

    for index, instance in enumerate(doc.instances):
        if instance.object is None:
            continue
        if type_name(doc, instance.object).lower() == key:
            out.append(Found(instance.object, f"#{index}", instance=index))
        for f in instance.object.fields:
            walk(f.value, f"#{index}.{f.name}", None)
    return out


def strings(doc: Document) -> Iterator[tuple[str, str]]:
    """(path, text) for every string value in the document."""
    def walk(value: Any, path: str):
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, Object):
            for f in value.fields:
                yield from walk(f.value, f"{path}.{f.name}")
        elif isinstance(value, list):
            for i, element in enumerate(value):
                yield from walk(element, f"{path}[{i}]")
    for index, instance in enumerate(doc.instances):
        if instance.object is not None:
            yield from walk(instance.object, f"#{index}")


def _remap(value: Any, shift) -> Any:
    if isinstance(value, PointerReference) and value.kind == PointerKind.internal:
        return PointerReference(PointerKind.internal, shift(value.index))
    if isinstance(value, Object):
        for f in value.fields:
            f.value = _remap(f.value, shift)
    elif isinstance(value, list):
        value[:] = [_remap(v, shift) for v in value]
    return value


def insert_instance(doc: Document, record: InstanceRecord, at: int | None = None) -> int:
    """Adds an instance at `at` (default: exported ones after the last exported, others at the
    end) and renumbers the internal pointers that the insertion moves. Returns its index."""
    exported = sum(1 for i in doc.instances if i.exported)
    if at is None:
        at = exported if record.exported else len(doc.instances)
    if not (0 < at <= exported if record.exported else exported <= at <= len(doc.instances)):
        raise ValueError("exported EBX instances must precede internal ones (and the root stays first)")
    for instance in doc.instances + [record]:
        if instance.object is not None:
            _remap(instance.object, lambda index: index + 1 if index >= at else index)
    doc.instances.insert(at, record)
    return at


def clone_instance(doc: Document, index: int, guid: Guid | None = None, at: int | None = None) -> int:
    """A copy of instance `index` (its values and raw image) added to the same document, at `at`
    as insert_instance places it. An exported copy needs its own GUID."""
    source = doc.instances[index]
    if source.exported and guid is None:
        raise ValueError("an exported instance needs a new GUID")
    record = copy.deepcopy(source)
    record.instance_guid = guid if source.exported else source.instance_guid
    # The writer finds what a boxed value's pointers reach by the read offsets of the instances;
    # a copy sharing its source's offset would take those pointers over.
    record.data_offset = -1
    return insert_instance(doc, record, at)


def after_last_of_type(doc: Document, index: int) -> int | None:
    """The position just after the last instance of instance `index`'s type, where the 333 map
    puts its added sub-world reference; None when an instance of that kind cannot go there."""
    kind = doc.instances[index].descriptor
    last = max(i for i, instance in enumerate(doc.instances) if instance.descriptor == kind)
    exported = sum(1 for i in doc.instances if i.exported)
    at = last + 1
    ok = 0 < at <= exported if doc.instances[index].exported else exported <= at <= len(doc.instances)
    return at if ok else None


def guid_flags(guid: Guid, template_flags: int) -> int:
    """Flags of a data-bus object: the low 24 bits of its GUID's first group as printed, under the
    template's bits 24-31 (bit 24 is set on some, the 333 map's added one among them)."""
    return (template_flags & 0xFF000000) | (int.from_bytes(guid.bytes[:4], "little") & 0xFFFFFF)


def pointer_arrays(doc: Document, index: int) -> list[tuple[str, list]]:
    """(path, list) for every array holding an internal pointer to instance `index`."""
    out = []

    def walk(value: Any, path: str):
        if isinstance(value, Object):
            for f in value.fields:
                walk(f.value, f"{path}.{f.name}")
        elif isinstance(value, list):
            if any(isinstance(v, PointerReference) and v.kind == PointerKind.internal and v.index == index
                   for v in value):
                out.append((path, value))
            for i, element in enumerate(value):
                walk(element, f"{path}[{i}]")
    for i, instance in enumerate(doc.instances):
        if instance.object is not None:
            walk(instance.object, f"#{i}")
    return out


def clone_document(doc: Document, file_guid: Guid, salt: str) -> Document:
    """A deep copy under a new file GUID; every exported instance gets a GUID derived from `salt`
    and its old GUID. Imports and internal pointers are kept."""
    out = copy.deepcopy(doc)
    out.file_guid = file_guid
    for instance in out.instances:
        if instance.exported:
            instance.instance_guid = derived_guid(salt, instance.instance_guid.string())
    return out


def set_fields(obj: Object, values: dict[str, Any]) -> list[str]:
    """Sets the fields the object has; returns the names it lacks."""
    missing = []
    for name, value in values.items():
        if obj.find(name) is None:
            missing.append(name)
        else:
            obj.set(name, value)
    return missing


def describe(doc: Document, limit: int = 20) -> Iterator[str]:
    """A readable dump: every instance with its fields, arrays cut at `limit` elements."""
    def value_text(value: Any) -> str:
        if isinstance(value, PointerReference):
            if value.kind == PointerKind.internal:
                target = doc.instances[value.index].object if 0 <= value.index < len(doc.instances) else None
                return f"-> #{value.index} {type_name(doc, target) if target else '?'}"
            if value.kind == PointerKind.external:
                i = doc.imports[value.index]
                return f"-> import {i.file_guid.string()} / {i.class_guid.string()}"
            return "null"
        if isinstance(value, Guid):
            return value.string()
        if isinstance(value, BoxedReference):
            # By the boxed value it reaches, not its offset, which moves on every rewrite.
            index = next((i for i, b in enumerate(boxed) if b.offset == value.data_offset), None)
            return f"boxed {value.encoded_type:#x} -> #{index}" if index is not None else f"boxed {value.encoded_type:#x} -> ?"
        return repr(value)

    def walk(value: Any, label: str, depth: int):
        pad = "  " * depth
        if isinstance(value, Object):
            yield f"{pad}{label}: {type_name(doc, value)}"
            for f in value.fields:
                yield from walk(f.value, f.name, depth + 1)
        elif isinstance(value, list):
            yield f"{pad}{label}: [{len(value)}]"
            for i, element in enumerate(value[:limit]):
                yield from walk(element, f"[{i}]", depth + 1)
            if len(value) > limit:
                yield f"{pad}  ... {len(value) - limit} more"
        else:
            yield f"{pad}{label}: {value_text(value)}"

    boxed = sorted(doc.boxed_values, key=lambda b: b.offset)
    yield f"file guid {doc.file_guid.string()}, root {doc.root_type}, {len(doc.instances)} instances, {len(doc.imports)} imports"
    for index, instance in enumerate(doc.instances):
        guid = f" guid {instance.instance_guid.string()}" if instance.exported else ""
        if instance.object is not None:
            yield from walk(instance.object, f"#{index}{guid}", 0)


# ---- Removing instances and imports (a pruned copy of a document, FORMAT-NOTES 6.3) -------------

def boxed_reach(doc: Document) -> set[int]:
    """Instances a boxed value's bytes point at, directly or through the arrays they reach (the
    writer carries those bytes as they are, so such an instance must stay)."""
    if not doc.payload or not doc.boxed_values:
        return set()
    arrays = {record.offset: record for record in doc.arrays}
    instances = {instance.data_offset: index for index, instance in enumerate(doc.instances)}
    slots = sorted(set(doc.pointer_offsets))
    ranges = [(b.offset, len(b.raw_bytes)) for b in doc.boxed_values]
    seen_arrays: set[int] = set()
    out: set[int] = set()
    while ranges:
        start, length = ranges.pop()
        for slot in (s for s in slots if start <= s < start + length):
            kind, key = pointer_target(doc, slot, arrays, instances)
            if kind == "instance":
                out.add(key)
            elif kind == "array" and key not in seen_arrays:
                seen_arrays.add(key)
                stride, _ = array_element(doc, arrays[key])
                ranges.append((key, stride * arrays[key].count))
    return out


def _internal(value: Any) -> Iterator[int]:
    if isinstance(value, PointerReference) and value.kind == PointerKind.internal:
        yield value.index
    elif isinstance(value, Object):
        for f in value.fields:
            yield from _internal(f.value)
    elif isinstance(value, list):
        for v in value:
            yield from _internal(v)


def reachable(doc: Document) -> set[int]:
    """Instances reached from the root (instance 0) by internal pointers, plus what boxed values reach."""
    seen = set(boxed_reach(doc)) | ({0} if doc.instances else set())
    queue = list(seen)
    while queue:
        instance = doc.instances[queue.pop()]
        for target in _internal(instance.object):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def remove_instances(doc: Document, removed: set[int]) -> list[str]:
    """Removes instances and every pointer to them that is an element of a pointer array; any
    other pointer to them (a field, a pointer inside a struct, a boxed value) is refused. The rest
    are renumbered. Returns the paths of the array elements taken out."""
    if not removed:
        return []
    if 0 in removed:
        raise ValueError("the root instance cannot be removed")
    blocked = boxed_reach(doc) & removed
    if blocked:
        raise ValueError(f"instances {sorted(blocked)} are reached from boxed values; not removed")
    taken: list[str] = []

    def strip(value: Any, path: str) -> None:
        if isinstance(value, Object):
            for f in value.fields:
                strip(f.value, f"{path}.{f.name}")
        elif isinstance(value, list):
            kept = []
            for i, v in enumerate(value):
                if isinstance(v, PointerReference) and v.kind == PointerKind.internal and v.index in removed:
                    taken.append(f"{path}[{i}] -> #{v.index}")
                else:
                    kept.append(v)
                    strip(v, f"{path}[{i}]")
            value[:] = kept

    kept = [i for i in range(len(doc.instances)) if i not in removed]
    for i in kept:
        if doc.instances[i].object is not None:
            strip(doc.instances[i].object, f"#{i}")
    for i in kept:
        left = sorted(set(_internal(doc.instances[i].object)) & removed)
        if left:
            raise ValueError(f"#{i} still points at {left} outside a pointer array; not removed")
    new_index = {old: new for new, old in enumerate(kept)}
    for i in kept:
        if doc.instances[i].object is not None:
            _remap(doc.instances[i].object, lambda index: new_index[index])
    doc.instances[:] = [doc.instances[i] for i in kept]
    return taken


def prune_unreachable(doc: Document) -> list[int]:
    """Removes every instance the root does not reach; returns their (old) indices."""
    gone = sorted(set(range(len(doc.instances))) - reachable(doc))
    remove_instances(doc, set(gone))
    return gone


def _externals(value: Any) -> Iterator[PointerReference]:
    if isinstance(value, PointerReference) and value.kind == PointerKind.external:
        yield value
    elif isinstance(value, Object):
        for f in value.fields:
            yield from _externals(f.value)
    elif isinstance(value, list):
        for v in value:
            yield from _externals(v)


def used_imports(doc: Document) -> list[ImportReference]:
    """The imports some field points at, in import order."""
    used = {p.index for i in doc.instances if i.object is not None for p in _externals(i.object)}
    return [imp for index, imp in enumerate(doc.imports) if index in used]


def prune_imports(doc: Document) -> int | None:
    """Drops imports no field points at and renumbers the rest. Returns how many were dropped,
    or None when the document has boxed values (whose bytes may hold import indices; left as is)."""
    if doc.boxed_values:
        return None
    pointers = [p for i in doc.instances if i.object is not None for p in _externals(i.object)]
    used = sorted({p.index for p in pointers})
    new_index = {old: new for new, old in enumerate(used)}
    for p in pointers:
        p.index = new_index[p.index]
    dropped = len(doc.imports) - len(used)
    doc.imports[:] = [doc.imports[i] for i in used]
    return dropped


def add_import(doc: Document, file_guid: Guid, instance_guid: Guid) -> PointerReference:
    """An external pointer to (file, instance), reusing an equal import."""
    for index, imp in enumerate(doc.imports):
        if imp.file_guid == file_guid and imp.class_guid == instance_guid:
            return PointerReference(PointerKind.external, index)
    doc.imports.append(ImportReference(file_guid, instance_guid))
    return PointerReference(PointerKind.external, len(doc.imports) - 1)


def path_to(doc: Document, target: int) -> list[tuple[int, list | None, int]] | None:
    """The chain of internal pointers from the root to instance `target`: per step (holder
    instance, the pointer array holding the step or None for a field, the element's index)."""
    parents: dict[int, tuple[int, list | None, int]] = {}
    seen, queue = {0}, [0]

    def steps(value: Any, array: list | None) -> Iterator[tuple[int, list | None, int]]:
        if isinstance(value, PointerReference) and value.kind == PointerKind.internal:
            yield value.index, None, -1
        elif isinstance(value, Object):
            for f in value.fields:
                yield from steps(f.value, None)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                if isinstance(v, PointerReference) and v.kind == PointerKind.internal:
                    yield v.index, value, i
                else:
                    yield from steps(v, None)

    while queue:
        at = queue.pop(0)
        if at == target:
            chain = []
            while at != 0:
                holder, array, i = parents[at]
                chain.append((holder, array, i))
                at = holder
            return chain[::-1]
        if doc.instances[at].object is None:
            continue
        for nxt, array, i in steps(doc.instances[at].object, None):
            if nxt not in seen:
                seen.add(nxt)
                parents[nxt] = (at, array, i)
                queue.append(nxt)
    return None


def refresh_flags(source: Document, clone: Document) -> int:
    """Gives each exported instance of a clone the Flags its new GUID makes, where the source
    instance's Flags follow the GUID rule (guid_flags). Returns how many were changed."""
    changed = 0
    for old, new in zip(source.instances, clone.instances):
        flags = new.object.get("Flags") if new.object is not None else None
        if not (old.exported and isinstance(flags, int)) or guid_flags(old.instance_guid, flags) != flags:
            continue
        if new.instance_guid != old.instance_guid:
            new.object.set("Flags", guid_flags(new.instance_guid, flags))
            changed += 1
    return changed


def field_referrers(doc: Document, index: int) -> list[str]:
    """Paths of the pointers to instance `index` that are not elements of a pointer array (a
    field, or a pointer inside a struct); remove_instances refuses those."""
    out: list[str] = []

    def walk(value: Any, path: str, in_array: bool) -> None:
        if isinstance(value, PointerReference):
            if value.kind == PointerKind.internal and value.index == index and not in_array:
                out.append(path)
        elif isinstance(value, Object):
            for f in value.fields:
                walk(f.value, f"{path}.{f.name}", False)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]", True)

    for i, instance in enumerate(doc.instances):
        if instance.object is not None:
            walk(instance.object, f"#{i}", False)
    if index in boxed_reach(doc):
        out.append("a boxed value")
    return out
