"""A `city --mesh` GLB as a .blend laid out for ReSkate Studio's Blender add-on.

    blender -b -P studio_prep.py -- place.glb place.blend
    python studio_prep.py place.glb place.blend      (with Blender's `bpy` module installed)

Then open place.blend in ReSkate Studio, or run `reskate_cli compile-map` on it (CUSTOM-MAPS.md).

What it does:
- imports the GLB; Blender turns each node's extras into the object's custom properties, so every
  object keeps `sk8_collision_mode` and the `reskate_*` hints from `city`;
- puts the meshes in a `Map` collection, the `spawn` empty in `Markers`, and the grind lines in
  `Grind curves`, each converted to a curve with `sk8_grind_curve` set (the layout public tools
  hand to Studio);
- if Studio's add-on is loaded, also sets its own properties where their names are known
  (`sk8_object.collision_mode`, `sk8_object.round_rail`), and leaves the rest for Studio;
- saves the .blend.

The property names come from public tools that feed Studio, not from Studio's documentation: open
the result in Studio and check the collision modes, grind curves and spawn before building."""
from __future__ import annotations

import os
import sys

import bpy

COLLECTIONS = ("Map", "Markers", "Grind curves")


def arguments() -> list[str]:
    argv = sys.argv
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return argv[1:]


def collection(name: str):
    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(found)
    return found


def move(obj, target) -> None:
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)


def to_curve(obj):
    """A mesh of loose edges (a glTF line) as a curve, keeping its custom properties."""
    properties = {key: obj[key] for key in obj.keys()}
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target="CURVE")
    curve = bpy.context.view_layer.objects.active
    for key, value in properties.items():
        curve[key] = value
    return curve


def studio_properties(obj) -> None:
    """Studio's own add-on properties, where it is loaded and the names are known."""
    group = getattr(obj, "sk8_object", None)
    if group is None:
        return
    mode = obj.get("sk8_collision_mode")
    for value in (mode, {"triangle_mesh": "TRIANGLE_MESH", "none": "NONE", "hull": "HULL"}.get(mode)):
        try:
            group.collision_mode = value
            break
        except (TypeError, ValueError, AttributeError):
            continue
    if obj.get("reskate_part") == "rail" and hasattr(group, "round_rail"):
        group.round_rail = True


def main() -> int:
    args = arguments()
    if len(args) != 2:
        print(__doc__)
        return 2
    source, target = args
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source)
    groups = {name: collection(name) for name in COLLECTIONS}
    counts = dict.fromkeys(COLLECTIONS, 0)
    for obj in list(bpy.data.objects):
        if obj.get("sk8_grind_curve"):
            if obj.type == "MESH":
                obj = to_curve(obj)
            move(obj, groups["Grind curves"])
            counts["Grind curves"] += 1
        elif obj.name.startswith("spawn") or obj.get("reskate_part") == "spawn":
            obj.name = "spawn"
            move(obj, groups["Markers"])
            counts["Markers"] += 1
        elif obj.type == "MESH":
            obj["sk8_collision_mode"] = obj.get("sk8_collision_mode", "triangle_mesh")
            move(obj, groups["Map"])
            counts["Map"] += 1
        studio_properties(obj)
    bpy.ops.wm.save_as_mainfile(filepath=target)
    print(f"Wrote {target}: " + ", ".join(f"{n} in {name}" for name, n in counts.items()))
    return 0


if __name__ == "__main__":
    code = main()
    if bpy.app.background:
        # Headless, the work is saved: leave now. Blender's Python module can crash while it
        # tears itself down at interpreter exit, which would turn a good run into a failed one.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    sys.exit(code)
