# Fit Check (plugin)

Pull request 326's Fit Check as a sandboxed Luau plugin. Press V on the Board screen to spin the
board, or on the Skaters screen (with no item grid open) to spin the skater. It turns once by itself,
hands off, and is saved as a GIF, APNG or WebM, cut out from the backdrop, with an info card beside
it: the deck, grip, trucks and wheels, or the outfit, by name. V again stops it.

How it works:

- It holds a stand-in pad's stick over with `reskate.room.turn`, as a player turns the board on the
  stick, so it works on mouse and keyboard too.
- `reskate.capture` captures every frame the game draws, before ReSkate's overlay, and cuts each
  out by the game's depth (or by colour when there is no depth).
- Once the turn has come round, the frames nearest evenly spaced points of that one turn are saved.
- While it turns, the game's screen-space contact shadows, bloom, lens flare and dirt, film grain,
  chromatic aberration and motion blur are switched off with `reskate.settings.set`. For a skater,
  screen-space reflections and ambient occlusion are switched off too. All of them are put back
  afterwards.

The files are saved in the plugin's data folder,
`Mods/.reskate_plugin_data/fit-check/fit-checks/`, as `<deck name>-<time>.<gif|png|webm>`.
OPEN FIT CHECKS FOLDER on its page opens that folder.

Files:

- `plugin/turn.lua`: the rules, with the game passed in. They cover:
  - the screen areas;
  - when a turn has come round;
  - easing the stick off when it turns too fast for the frames wanted, and speeding it up again
    when it stops;
  - which frames make the animation;
  - the card, which shows the item highlighted in the open grid in place of the one worn;
  - the file's name;
  - the settings switched off.
- `plugin/tests.lua`: checks of those rules, run by `fitcheck test`.
- `plugin/main.lua`: the wiring to `reskate`, which covers:
  - the V key;
  - the banner while it records;
  - the console command;
  - its page in the menu's PLUGINS section.

## Install

Copy `reskate-plugin.json` and `plugin/` into a folder of their own in `Mods`.

## Console

`fitcheck` takes these subcommands:

- `board` or `skater`: make one, as V does.
- `cancel`: stop the one being made.
- `open`: open the folder.
- `test`: run its own checks.
- `set <key> <value>`: change a setting. The settings are kept.

| Key | Values |
| --- | --- |
| `format` | `gif`, `apng` or `webm` |
| `background` | `clear` or `matte` |
| `matte` | `#rrggbb` |
| `parts` | `on` or `off` |
| `speed` | `5` to `100` |
| `direction` | `left` or `right` |
| `axis` | `lx`, `ly`, `rx` or `ry` |
| `seconds` | `1` to `12` |
| `fps` | `5` to `60` |
| `size` | `64` to `2048` |
