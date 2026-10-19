# Test plugins

Two plugins that check ReSkate's plugin host (`Extension/Plugins`). To try one in game, copy its
folder into the game's `Mods` folder and open the console.

- **reskate-test** calls every plugin API and says what worked. `ptest all` runs every check,
  `ptest <module>` one module, and `ptest list` names them. It prints only the failures and a
  summary line. Each API ReSkate adds gets a module in `plugin/checks/`. It's also the example to
  copy when making a plugin.
- **reskate-hostile** attacks the sandbox on purpose, one attack per command. `hostile list` names
  them with what should happen. An attack that gets it stopped is expected: `plugin on
  reskate-hostile` starts it again for the next one. Any line starting with `ESCAPED` is a hole in
  the sandbox.

Both also run without the game, in `dingosdk_plugin_tests` (`DINGOSDK_BUILD_PLUGIN_TESTS`).

## Ports

- **road-rash** is Road Rash (`Extension/RoadRash`) as a mod: bails leave marks that build up,
  worn over the outfit with `reskate.outfit`. It needs the Road Rash items mod, and the built-in
  Road Rash switched off (`roadrash 0`), since ReSkate refuses outfit changes while that one is on.
  While the built-in one exists it owns the `roadrash` command, so this one answers to `road rash`
  (`road rash heal`, `road rash bails 20`, `road rash blood 0`, `road rash tattoos 1`,
  `road rash test` for its own checks). Its switches are on its panel in the PLUGINS tab too.
