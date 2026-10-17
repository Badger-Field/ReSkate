// "Use the game on this PC" (local_game.h): what a game folder is, which of its files are copied,
// what is left alone when copying again, finding skate. in Steam's libraries, and a cancelled copy.
#include "Launcher/local_game.h"

#include <algorithm>
#include <cstdio>
#include <fstream>
#include <random>
#include <string>

using namespace dingosdk::launcher_app::local_game;

namespace {
void write(const fs::path& file, const std::string& text) {
    fs::create_directories(file.parent_path());
    std::ofstream(file, std::ios::binary) << text;
}
std::string read(const fs::path& file) {
    std::ifstream in(file, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}
bool listed(const CopyPlan& plan, const fs::path& relative) {
    return std::find(plan.files.begin(), plan.files.end(), relative) != plan.files.end();
}
} // namespace

int main() {
    int failures = 0;
    const auto check = [&](bool ok, const char* what) {
        if (!ok) {
            ++failures;
            std::fprintf(stderr, "FAIL: %s\n", what);
        }
    };

    // Which files are the game's.
    check(game_file("Skate.exe") && game_file("steam_api64.dll") && game_file("Data/layout.toc") &&
              game_file("Data/Win32/a.cas") && game_file("Patch/layout.toc") && game_file("__Installer/x.dat"),
          "the game's own files are copied");
    check(!game_file("ReSkateLauncher.exe") && !game_file("reskatelauncher.exe.old") && !game_file("ReSkate.dll") &&
              !game_file("ReSkate.log") && !game_file("ReSkateLauncher.settings.json") &&
              !game_file("ReSkate.favorites.json") && !game_file("GameNetworkingSockets.dll") &&
              !game_file("api-ms-win-core-debug-minidump-l1-1-0.dll") && !game_file("texture_limit_stock.txt"),
          "ReSkate's own files beside the game are not");
    check(!game_file("Mods/Some/layout.toc") && !game_file("mods/mods.json") && !game_file("ModData/Default/x") &&
              !game_file("logs/ReSkate.log") && !game_file(".DepotDownloader/x.manifest") &&
              !game_file("Data/Win32/a.cas.reskate-copy") && !game_file("Data/.reskate-pack"),
          "mods, logs and half-copied files are not");
    check(game_file("scripts/a.lua") && !game_file("scripts/a.lua", true) && !game_file("config/x.ini", true),
          "scripts and config are left out only when they are ReSkate's export");

    const auto root = fs::temp_directory_path() / ("reskate-local-game-" + std::to_string(std::random_device{}()));
    std::error_code ignored;
    fs::remove_all(root, ignored);

    // A game folder, and one that is not.
    const auto source = root / "Steam" / "steamapps" / "common" / "skate";
    write(source / "Skate.exe", "exe");
    write(source / "steam_api64.dll", "api");
    write(source / "Data" / "layout.toc", "toc");
    write(source / "Data" / "Win32" / "big.cas", std::string(5u << 20, 'x'));
    write(source / "ReSkate.dll", "ours");
    write(source / "Mods" / "Park" / "layout.toc", "mod");
    write(source / "logs" / "ReSkate.log", "log");
    write(source / "scripts" / ".initfs-export.json", "{}");
    write(source / "scripts" / "a.lua", "edit");
    check(looks_like_game(source), "a skate. folder");
    check(!looks_like_game(root) && !looks_like_game(source / "Data"), "a folder without Skate.exe and Data\\layout.toc");

    // What is copied, into a ReSkate folder that has nothing yet.
    const auto target = root / "ReSkate";
    write(target / "ReSkateLauncher.exe", "launcher");
    check(!overlapping(source, target) && overlapping(root, target) && overlapping(target, target / "x"),
          "the game is never copied into itself");
    auto plan = plan_copy(source, target);
    check(plan.files.size() == 4 && listed(plan, "Skate.exe") && listed(plan, "steam_api64.dll") &&
              listed(plan, fs::path("Data") / "layout.toc") && listed(plan, fs::path("Data") / "Win32" / "big.cas"),
          "the game's files, and not ReSkate's, mods, logs or the scripts export");
    check(plan.bytes == 3 + 3 + 3 + (5u << 20) && plan.in_place == 0, "their size");

    // Copying, then planning again: nothing left to do, and the source untouched.
    std::atomic<bool> cancel{false};
    std::uintmax_t copied = 0;
    for (const auto& file : plan.files)
        check(copy_one(source / file, target / file, [&](std::uintmax_t n) { copied += n; }, cancel), "a copy");
    check(copied == plan.bytes && read(target / "Data" / "Win32" / "big.cas").size() == (5u << 20) &&
              read(target / "Skate.exe") == "exe",
          "every byte arrives");
    plan = plan_copy(source, target);
    check(plan.files.empty() && plan.in_place == 4, "copying again skips what is already copied");
    check(read(source / "ReSkate.dll") == "ours" && fs::exists(source / "Mods" / "Park" / "layout.toc") &&
              !fs::exists(target / "Mods") && !fs::exists(target / "ReSkate.dll"),
          "the source is left as it was, and its ReSkate files stay there");

    // A changed file is copied again.
    write(source / "Data" / "layout.toc", "toc2");
    plan = plan_copy(source, target);
    check(plan.files.size() == 1 && plan.files[0] == fs::path("Data") / "layout.toc", "a file that differs is copied again");

    // Cancelled part-way: neither the target nor a partial file is left.
    const auto fresh = root / "Fresh";
    cancel = true;
    check(!copy_one(source / "Data" / "Win32" / "big.cas", fresh / "big.cas", {}, cancel), "a cancelled copy says so");
    check(!fs::exists(fresh / "big.cas") && !fs::exists(fresh / "big.cas.reskate-copy"), "and leaves no file behind");

    // Finding skate. in Steam's libraries, from its text files only.
    const auto steam = root / "Steam";
    const auto other = root / "Library2";
    write(steam / "steamapps" / "libraryfolders.vdf",
          "\"libraryfolders\"\n{\n \"0\" { \"path\" \"" + steam.generic_string() + "\" }\n \"1\" { \"path\" \"" +
              other.generic_string() + "\" }\n}\n");
    write(steam / "steamapps" / "appmanifest_3354750.acf", "\"AppState\" { \"appid\" \"3354750\" \"installdir\" \"skate\" }");
    write(other / "steamapps" / "appmanifest_3354750.acf", "\"AppState\" { \"installdir\" \"..\\\\..\" }");
    auto found = steam_game_folders(steam);
    check(found.size() == 1 && fs::equivalent(found[0], source), "skate. in Steam's own library, listed once");
    write(other / "steamapps" / "appmanifest_3354750.acf", "\"AppState\" { \"installdir\" \"skate.\" }");
    check(steam_game_folders(steam).size() == 1, "a manifest naming a folder that is not the game finds nothing there");
    check(steam_game_folders(root / "nowhere").empty(), "no Steam, nothing found");

    fs::remove_all(root, ignored);
    std::printf("%s\n", failures ? "FAILED" : "Local game: files to copy, resume, cancel, Steam libraries passed.");
    return failures ? 1 : 0;
}
