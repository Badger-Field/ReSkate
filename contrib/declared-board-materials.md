# Declared board material inputs

Plugins already read and hold live colour/number values through `reskate.board.parameters`, `set_parameter` and `clear_parameter`. A custom shader can have inputs whose defaults are compiled into it, with no live node for that API to hold. A mod may declare these in its root `reskate-materials.json`:

```json
{"schema":1,"materials":[{"part":"deck","items":["Own_Example_Deck"],"parameters":[{"guid":"1af346775c032d5b59ff5e88010069b6","type":"colour","default":[0.5,0.5,0.5]}]}]}
```

The declaration describes an existing compiled shader input; it does not install shaders, select materials, unlock items or add textures. The GUID is its 16 serialized bytes in hex. The shader and declaration must agree on identity, type and default. Only the declaring equipped mod-owned item receives the value. Store items are excluded.

A permitted plugin sees an absent live value as `inherited = true`, with the declared default. It uses the existing `board` permission and existing set/clear API. Missing declared colours are inserted with the engine typed setter at native preset priority. A number must already have a live value; the declaration does not enable missing float insertion. Integers, switches and textures cannot be declared or changed.

Held values are released on clear, unload and item changes. Existing nodes restore their prior values; inserted nodes restore declared defaults (the engine retains the node). Type/identity mismatches and values outside bounds are refused. Competing plugins cannot take over a held key. The native setter/descriptor are checked against the supported build before use.

Bounds: 32 materials, 64 item keys per declaration, 32 parameters per declaration; text at most 255 bytes; colour channels 0–1 and numbers -100–100. Malformed files are skipped as a whole.

Board observation is prepared before timed plugin execution, only for a plugin with the `board` permission. No effect-specific solver, artwork, controller or raw-memory plugin access is introduced.

Tests: `mod_materials`, `board_material`, `plugins`.
