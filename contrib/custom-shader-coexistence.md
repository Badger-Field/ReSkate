# Additive custom material resources

Program lookup edits may carry their own bounded shader program graph, not only rows pointing into the base graph. Merging preserves native program data/rows, appends validated independent heaps and relocation fields, and remaps each added row to the correct program indices. Existing rows win by mod order; incompatible graphs are refused. Combined graph data is bounded to 64 MiB.

This enables private custom material programs to coexist without replacing unrelated programs. It does not resolve two mods intentionally changing the same key: exporters must still generate independent identities.

Variant displacement now distinguishes a replacement bundle from a bundle that contains every original unchanged and only adds assets. Original names/GUIDs, content hashes, types, sizes and resource metadata must all match; removal or mutation keeps the existing displacement behavior. This preserves original delivery locations for additive cosmetic content without claiming arbitrary replacements are compatible.

Tests: `shader_lookup`, `mod_variants`. No effect artwork, game-derived shader binaries or local capture data are included.
