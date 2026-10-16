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
from .ebx import Document, InstanceRecord, Object, PointerKind, PointerReference


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


def insert_instance(doc: Document, record: InstanceRecord) -> int:
    """Adds an instance (exported ones after the last exported, others at the end) and renumbers
    the internal pointers that the insertion moves. Returns its index."""
    at = sum(1 for i in doc.instances if i.exported) if record.exported else len(doc.instances)
    for instance in doc.instances:
        if instance.object is not None:
            _remap(instance.object, lambda index: index + 1 if index >= at else index)
    doc.instances.insert(at, record)
    return at


def clone_instance(doc: Document, index: int, guid: Guid | None = None) -> int:
    """A copy of instance `index` (its values and raw image) added to the same document. An
    exported copy needs its own GUID."""
    source = doc.instances[index]
    if source.exported and guid is None:
        raise ValueError("an exported instance needs a new GUID")
    record = copy.deepcopy(source)
    record.instance_guid = guid if source.exported else source.instance_guid
    return insert_instance(doc, record)


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

    yield f"file guid {doc.file_guid.string()}, root {doc.root_type}, {len(doc.instances)} instances, {len(doc.imports)} imports"
    for index, instance in enumerate(doc.instances):
        guid = f" guid {instance.instance_guid.string()}" if instance.exported else ""
        if instance.object is not None:
            yield from walk(instance.object, f"#{index}{guid}", 0)
