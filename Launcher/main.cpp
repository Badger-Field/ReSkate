#include "Engine/Core/Log/logging.h"
#include "Engine/Core/Debug/trace.h"
#include "crash_reporter.h"
#include "install_here.h"
#include "gui.h"
#include "launch.h"
#include "mod_manager.h"

#include <Windows.h>
#include <shellapi.h>

#include <algorithm>
#include <atomic>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace fs = std::filesystem;

namespace {

namespace app = dingosdk::launcher_app;
namespace update = dingosdk::launcher_update;
using dingosdk::logging::Channel;
using dingosdk::logging::Level;

std::wstring widen(std::string_view value) {
    if (value.empty()) return {};
    auto length = MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (length <= 0) return L"Unknown error";
    std::wstring output(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), output.data(), length);
    return output;
}

// Removes a launcher-only flag so it never reaches Skate.exe.
bool take_flag(std::vector<std::wstring>& arguments, std::wstring_view flag) {
    const auto found = std::find(arguments.begin(), arguments.end(), flag);
    if (found == arguments.end()) return false;
    arguments.erase(found);
    return true;
}

// Scripted launches (`--no-gui`): update, validate and start Skate without a window.
// Returns true when the launcher replaced itself and restarted.
bool apply_headless_updates(const app::Session& session, const update::Config& config,
                            const std::vector<std::wstring>& original, bool relaunched) {
    if (!update::binary_updates_enabled()) return false;
    if (!config.runtime.url.empty() && !update::file_matches(session.paths.dll, config.runtime)) {
        try {
            update::replace_file(session.paths.dll, config.runtime);
            dingosdk::logging::log(Level::info, Channel::launcher, "ReSkate.dll updated to {}.", config.runtime.version);
        } catch (const std::exception& exception) {
            dingosdk::logging::log(Level::warning, Channel::launcher, "ReSkate.dll update failed: {}", exception.what());
        }
    }
    if (relaunched || config.launcher.url.empty() || update::file_matches(session.self, config.launcher)) return false;
    try {
        update::replace_running_launcher(session.self, config.launcher);
        dingosdk::logging::log(Level::info, Channel::launcher, "Launcher updated to {}; restarting.", config.launcher.version);
        auto arguments = original;
        arguments.emplace_back(L"--reskate-updated");
        app::relaunch(session.self, arguments);
        return true;
    } catch (const std::exception& exception) {
        dingosdk::logging::log(Level::warning, Channel::launcher, "Launcher update failed: {}", exception.what());
        return false;
    }
}

// "Install as ReSkate mod" on a .zip's right-click menu (install_here.h): `--install-mod <file>`.
// Asks first, installs into the Mods folder this launcher's game reads, and says what happened.
int install_mod(const app::Session& session, const fs::path& file) {
    const auto title = L"ReSkate";
    std::wstring ask = L"Install \"" + file.filename().wstring() + L"\" as a ReSkate mod?\n\n"
                       L"Only install mods from people you trust: a mod changes the game's data.";
    if (MessageBoxW(nullptr, ask.c_str(), title, MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND) != IDYES) return 0;
    const auto root = dingosdk::launcher_mods::mods_root(session.paths.directory);
    std::atomic<bool> cancel{};
    dingosdk::launcher_mods::InstallOptions options;
    options.require_content = true; // a zip with nothing the game loads is not installed
    std::string name;
    try {
        try {
            name = dingosdk::launcher_mods::install(root, file, false, {}, cancel, options);
        } catch (const dingosdk::launcher_mods::AlreadyInstalled& installed) {
            const auto again = L"\"" + widen(installed.name) + L"\" is already installed. Replace it with this one?";
            if (MessageBoxW(nullptr, again.c_str(), title, MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND) != IDYES) return 0;
            name = dingosdk::launcher_mods::install(root, file, true, {}, cancel, options);
        }
    } catch (const std::exception& exception) {
        const auto text = L"The mod was not installed.\n\n" + widen(exception.what());
        MessageBoxW(nullptr, text.c_str(), title, MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
        return 1;
    }
    const auto done = L"Installed \"" + widen(name) + L"\".\n\nIt is on the next time you play, and in the game's "
                      L"Mods tab (Insert) you can switch it on right away.";
    MessageBoxW(nullptr, done.c_str(), title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND);
    return 0;
}

void run_headless(const app::Session& session, std::vector<std::wstring> arguments,
                  const std::vector<std::wstring>& original) {
    const bool relaunched = take_flag(arguments, L"--reskate-updated");
    const bool updates = !take_flag(arguments, L"--no-update") && dingosdk::launcher_gui::updates_enabled(session);
    // With no options of its own, the launch is the one the launcher window's Play would make.
    // A map to start on is not one of those: "--no-gui -map <name>" is that launch, on that map.
    auto map = dingosdk::launcher::take_map_option(arguments);
    auto options = arguments.empty() ? dingosdk::launcher_gui::saved_launch_options(session)
                                     : dingosdk::launcher::parse_launch_options(arguments);
    if (!map.empty()) options.map = std::move(map);
    update::remove_previous_launcher(session.self);
    if (updates) {
        const auto config = update::fetch_config(dingosdk::launcher_gui::prereleases_enabled(session));
        if (config && apply_headless_updates(session, *config, original, relaunched)) return;
    }
    if (!app::game_files_supported(session.paths))
        throw std::runtime_error("Skate.exe is missing or is not the supported build. "
            "Open ReSkateLauncher.exe without --no-gui to download it from Steam.");
    app::start_game(session, options);
}

// Whether the launcher was put in a game's folder of a Steam library (...\steamapps\common\<game>),
// which is where Steam keeps skate. and updates it: ReSkate has a folder of its own, with its
// own copy of the game. The same under Wine: on Proton, Steam is given the launcher's place in
// that folder of its own (README, "Linux (Proton)").
bool in_steam_library() {
    std::wstring self(32768, L' ');
    self.resize(GetModuleFileNameW(nullptr, self.data(), static_cast<DWORD>(self.size())));
    if (self.empty()) return false;
    const auto same = [](const fs::path& part, const wchar_t* name) { return _wcsicmp(part.c_str(), name) == 0; };
    for (auto folder = fs::path(self).parent_path(); folder.has_parent_path() && folder != folder.parent_path(); folder = folder.parent_path())
        if (same(folder.filename(), L"common") && same(folder.parent_path().filename(), L"steamapps")) return true;
    return false;
}

} // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc{};
    auto** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) {
        MessageBoxW(nullptr, L"Could not read the launcher command line.",
            L"ReSkate Launcher", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
        return 1;
    }
    if (argc > 1 && std::wstring_view(argv[1]) == L"--reskate-crash-helper") {
        const auto result = dingosdk::crash_report::run_reporter(argc, argv);
        LocalFree(argv);
        return result;
    }
    const std::vector<std::wstring> original(argv + 1, argv + argc);
    LocalFree(argv);
    // (Before anything is read or written there: the only thing to do is to close.)
    if (in_steam_library()) {
        // An earlier ReSkate's one-click INSTALL put it here (install_here.h): offer to take it
        // back out, so the old shortcuts and right-click entry stop pointing at a launcher that
        // can no longer start. skate. itself, mods and the player's folders stay.
        std::wstring self(32768, L' ');
        self.resize(GetModuleFileNameW(nullptr, self.data(), static_cast<DWORD>(self.size())));
        const auto folder = fs::path(self).parent_path();
        std::error_code error;
        const bool installed_here = !self.empty() && fs::is_regular_file(folder / L"Skate.exe", error) &&
                                    fs::is_regular_file(folder / L"ReSkate.dll", error);
        const std::wstring move =
            L"ReSkate cannot run from Steam's skate. folder.\n\n"
            L"Put ReSkateLauncher.exe, ReSkate.dll and GameNetworkingSockets.dll (unzip the whole ReSkate download) "
            L"in a new, empty folder of their own (for example C:\\Games\\ReSkate) and run the launcher from there. "
            L"It installs its own copy of the game into that folder, so a Steam update cannot break it.";
        if (installed_here &&
            MessageBoxW(nullptr, (move + L"\n\nRemove ReSkate from Steam's skate. folder now? Its files there, its shortcuts and "
                                         L"the \"Install as ReSkate mod\" right-click entry go; skate., your Mods folder and "
                                         L"ReSkate's config, scripts and logs folders stay.").c_str(),
                        L"ReSkate Launcher", MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND) == IDYES) {
            try {
                app::uninstall_from(folder);
                MessageBoxW(nullptr, L"ReSkate was removed from Steam's skate. folder. Unzip ReSkate into a folder of its own "
                                     L"and run the launcher from there.",
                            L"ReSkate Launcher", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND);
            } catch (const std::exception& exception) {
                MessageBoxW(nullptr, (L"ReSkate was not removed.\n\n" + widen(exception.what())).c_str(), L"ReSkate Launcher",
                            MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
            }
            return 1;
        }
        if (!installed_here)
            MessageBoxW(nullptr, (move + L"\n\nThe launcher will now close.").c_str(), L"ReSkate Launcher",
                        MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
        return 1;
    }
    auto arguments = original;
    const bool headless = take_flag(arguments, L"--no-gui");

    int result = 0;
    fs::path logs;
    try {
        std::string log_level = "info";
        if (headless) log_level = dingosdk::launcher::parse_launch_options(
            [&] { auto copy = arguments; take_flag(copy, L"--no-update"); take_flag(copy, L"--reskate-updated"); return copy; }()).log_level;
        dingosdk::launcher_gui::apply_crash_report_setting();
        const auto mod = std::find(arguments.begin(), arguments.end(), L"--install-mod");
        const bool installing_mod = mod != arguments.end() && mod + 1 != arguments.end();
        const auto session = app::open_session(log_level, !installing_mod);
        logs = session.paths.logs;
        if (installing_mod)
            result = install_mod(session, fs::path(*(mod + 1)));
        else if (headless) run_headless(session, arguments, original);
        else if (app::relaunch_without_mangohud(session.self, original)) dingosdk::logging::flush();
        else result = dingosdk::launcher_gui::run(session, arguments);
    } catch (const std::exception& exception) {
        const auto message = widen(exception.what());
        dingosdk::logging::write(Level::critical, Channel::launcher, message);
        dingosdk::logging::flush();
        std::wstring dialog = L"Could not start Skate.\n\n" + message;
        if (!logs.empty()) dialog += L"\n\nDetails: " + (logs / L"ReSkate.log").wstring();
        MessageBoxW(nullptr, dialog.c_str(), L"ReSkate Launcher", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
        result = 1;
    }
    dingosdk::trace::flush(std::chrono::seconds(3));
    dingosdk::logging::shutdown();
    return result;
}
