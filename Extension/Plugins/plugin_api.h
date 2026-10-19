#pragma once
#include "plugin_vm.h"

// Inside the plugin host only: the reskate API a plugin's VM gets.
namespace dingosdk::plugins {
struct Api {
    // The `reskate` table, `print` and `require`, before the VM is sandboxed.
    static void install(Plugin& plugin, lua_State* L);
};
// The overlay, menu, sound, mods, session and message parts of the API (plugin_api_ui.cpp), into
// the reskate table on top of the stack.
void install_ui_api(Plugin& plugin, lua_State* L);
// A player as plugins get one: {id = "<SteamID64>", name, me, host}.
void push_player(lua_State* L, const SessionPlayer& player);
// A file's bytes when it is a regular file no larger than `most`; otherwise empty, and `error`
// says why ("not found" when there is none).
std::string read_package_file(const std::filesystem::path& file, std::size_t most, std::string& error);
// JSON and Lua values, both ways. Throws std::runtime_error on a value JSON cannot hold.
Json lua_to_json(lua_State* L, int index);
void push_json(lua_State* L, const Json& value);
}
