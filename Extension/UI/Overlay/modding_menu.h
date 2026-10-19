#pragma once
#include "Engine/Vfs/builtin_packs.h"
#include "Engine/Vfs/mod_list.h"
#include <filesystem>
#include <memory>
#include <vector>
namespace dingosdk::overlay {
struct SkateMenu;
struct Model;
struct CallbacksV3;
// The MODS page: tab 0 lists installed mods and, below them, mods to get from Thunderstore; tab 1
// custom Lua scripts.
void draw_modding_menu(SkateMenu& menu, int tab, const Model& model, const CallbacksV3& callbacks);
// The Mods folder and the official mods in place, as the MODS page last read them (each on a thread
// of its own, every 1.5 s while asked; null before the first read ends): for the `mods` console
// commands (Extension/Assets/mod_browser_commands.cpp), which must not read the disk themselves.
std::shared_ptr<const mods::ModList> mods_folder();
std::shared_ptr<const std::vector<mods::BuiltinPack>> official_mods(const std::filesystem::path& mods_root);
}
