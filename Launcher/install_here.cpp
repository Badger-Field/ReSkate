#include "install_here.h"
#include "text_encoding.h"
#include "Engine/Core/Log/logging.h"

#include <Windows.h>
#include <shlobj.h>
#include <shobjidl.h>

#include <array>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <system_error>
#include <vector>

namespace fs = std::filesystem;

namespace dingosdk::launcher_app {
namespace {
// What an install carries: the launcher and runtime always, the networking library for playing
// without Steam when it came with them.
constexpr std::array<const wchar_t *, 3> files{L"ReSkateLauncher.exe", L"ReSkate.dll", L"GameNetworkingSockets.dll"};

std::string read_text(const fs::path &path) {
    std::ifstream in(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}
fs::path known_folder(REFKNOWNFOLDERID id) {
    PWSTR path{};
    fs::path result;
    if (SUCCEEDED(SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, nullptr, &path)) && path) result = path;
    CoTaskMemFree(path);
    return result;
}
void shortcut(const fs::path &link, const fs::path &target) {
    IShellLinkW *shell{};
    if (FAILED(CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&shell)))) return;
    shell->SetPath(target.c_str());
    shell->SetWorkingDirectory(target.parent_path().c_str());
    shell->SetDescription(L"ReSkate for skate.");
    shell->SetIconLocation(target.c_str(), 0);
    IPersistFile *file{};
    if (SUCCEEDED(shell->QueryInterface(IID_PPV_ARGS(&file)))) {
        file->Save(link.c_str(), TRUE);
        file->Release();
    }
    shell->Release();
}
void make_shortcuts(const fs::path &launcher) {
    const bool com = SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    for (const auto &folder : {known_folder(FOLDERID_Desktop), known_folder(FOLDERID_Programs)})
        if (!folder.empty()) shortcut(folder / L"ReSkate.lnk", launcher);
    if (com) CoUninitialize();
}
void set_value(HKEY key, const wchar_t *name, const std::wstring &value) {
    RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE *>(value.c_str()),
                   static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
}
} // namespace

bool in_steam_library(const fs::path &folder) {
    const auto same = [](const fs::path &part, const wchar_t *name) { return _wcsicmp(part.c_str(), name) == 0; };
    for (auto at = folder; at.has_parent_path() && at != at.parent_path(); at = at.parent_path())
        if (same(at.filename(), L"common") && same(at.parent_path().filename(), L"steamapps")) return true;
    return false;
}

bool installed_copy_matches(const fs::path &game, const fs::path &from) {
    std::error_code error;
    if (!fs::is_regular_file(from / L"ReSkate.dll", error) || !fs::is_regular_file(game / L"ReSkateLauncher.exe", error))
        return false;
    for (const auto *name : files) {
        const auto source = from / name;
        if (!fs::is_regular_file(source, error)) continue;
        const auto target = game / name;
        if (!fs::is_regular_file(target, error) || fs::file_size(source, error) != fs::file_size(target, error) || error)
            return false;
        if (read_text(source) != read_text(target)) return false;
    }
    return true;
}

fs::path install_into(const fs::path &game, const fs::path &from) {
    std::error_code error;
    if (!fs::is_regular_file(game / L"Skate.exe", error)) throw std::runtime_error("That folder has no Skate.exe.");
    if (in_steam_library(game))
        throw std::runtime_error("ReSkate is not installed into Steam's skate. folder: it has a folder of its own.");
    if (!fs::is_regular_file(from / L"ReSkate.dll", error))
        throw std::runtime_error("ReSkate.dll is not beside the launcher. Unzip the whole ReSkate download first.");
    if (fs::equivalent(game, from, error)) return game / L"ReSkateLauncher.exe";
    // All or nothing: every file is copied in beside its target first; then each target that
    // exists is set aside and the new one moved in. If any step fails, what was moved is put
    // back, so the folder never ends up with a new launcher and an old runtime.
    struct Step {
        fs::path target, staged, kept;
        bool had{}, moved{};
    };
    std::vector<Step> steps;
    const auto undo = [&] {
        std::error_code ignored;
        for (auto it = steps.rbegin(); it != steps.rend(); ++it) {
            if (it->moved) fs::remove(it->target, ignored);
            if (it->had && fs::exists(it->kept, ignored)) MoveFileExW(it->kept.c_str(), it->target.c_str(), MOVEFILE_REPLACE_EXISTING);
            fs::remove(it->staged, ignored);
        }
    };
    for (const auto *name : files) {
        const auto source = from / name;
        if (!fs::is_regular_file(source, error)) continue;
        Step step{game / name, game / name, game / name};
        step.staged += L".new";
        step.kept += L".old";
        fs::copy_file(source, step.staged, fs::copy_options::overwrite_existing, error);
        steps.push_back(step);
        if (error) {
            undo();
            throw std::runtime_error("Could not copy " + launcher_text::utf8(fs::path(name).wstring()) + " into the game folder.");
        }
    }
    for (auto &step : steps) {
        step.had = fs::exists(step.target, error);
        if ((step.had && !MoveFileExW(step.target.c_str(), step.kept.c_str(), MOVEFILE_REPLACE_EXISTING)) ||
            !MoveFileExW(step.staged.c_str(), step.target.c_str(), MOVEFILE_WRITE_THROUGH)) {
            step.moved = false;
            undo();
            throw std::runtime_error("Could not replace " + launcher_text::utf8(step.target.filename().wstring()) +
                                     ". Close skate. and try again.");
        }
        step.moved = true;
    }
    for (const auto &step : steps) fs::remove(step.kept, error);
    const auto launcher = game / L"ReSkateLauncher.exe";
    // Shortcuts: best effort, the install stands without them.
    make_shortcuts(launcher);
    register_mod_install_verb(launcher);
    dingosdk::logging::write(dingosdk::logging::Level::info, dingosdk::logging::Channel::launcher,
                             L"Installed ReSkate into " + game.wstring());
    return launcher;
}

namespace {
// Where a shortcut points, or nothing.
fs::path shortcut_target(const fs::path &link) {
    fs::path target;
    IShellLinkW *shell{};
    if (FAILED(CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&shell)))) return target;
    IPersistFile *file{};
    if (SUCCEEDED(shell->QueryInterface(IID_PPV_ARGS(&file)))) {
        std::array<wchar_t, MAX_PATH * 2> path{};
        if (SUCCEEDED(file->Load(link.c_str(), STGM_READ)) &&
            SUCCEEDED(shell->GetPath(path.data(), static_cast<int>(path.size()), nullptr, SLGP_RAWPATH)))
            target = path.data();
        file->Release();
    }
    shell->Release();
    return target;
}
constexpr wchar_t verb_key[] = L"Software\\Classes\\SystemFileAssociations\\.zip\\shell\\ReSkateInstallMod";
} // namespace

std::optional<fs::path> find_installed_reskate(const fs::path &self_folder) {
    std::error_code error;
    std::optional<fs::path> found;
    const bool com = SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    for (const auto &folder : {known_folder(FOLDERID_Desktop), known_folder(FOLDERID_Programs)}) {
        if (folder.empty()) continue;
        const auto target = shortcut_target(folder / L"ReSkate.lnk");
        if (target.empty() || _wcsicmp(target.filename().c_str(), L"ReSkateLauncher.exe") != 0) continue;
        const auto game = target.parent_path();
        if (!fs::is_regular_file(target, error) || !fs::is_regular_file(game / L"Skate.exe", error)) continue;
        if (fs::equivalent(game, self_folder, error) || in_steam_library(game)) continue;
        found = game;
        break;
    }
    if (com) CoUninitialize();
    return found;
}

void make_shortcuts_once(const fs::path &launcher) {
    std::error_code error;
    for (const auto &folder : {known_folder(FOLDERID_Desktop), known_folder(FOLDERID_Programs)})
        if (!folder.empty() && fs::exists(folder / L"ReSkate.lnk", error)) return;
    make_shortcuts(launcher);
}

void uninstall_from(const fs::path &game) {
    std::error_code error;
    const auto launcher = game / L"ReSkateLauncher.exe";
    if (!fs::is_regular_file(launcher, error) || !fs::is_regular_file(game / L"ReSkate.dll", error))
        throw std::runtime_error("ReSkate is not installed in this folder.");
    // Nothing is removed while skate. (or anything else) still has ReSkate's files open: each is
    // opened for exclusive access first, and only when all of them can be are they deleted.
    const std::array<fs::path, 2> runtime{game / L"ReSkate.dll", game / L"GameNetworkingSockets.dll"};
    for (const auto &file : runtime) {
        if (!fs::exists(file, error)) continue;
        const auto handle = CreateFileW(file.c_str(), GENERIC_READ, 0, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            throw std::runtime_error("Close skate. first: " + launcher_text::utf8(file.filename().wstring()) + " is in use.");
        CloseHandle(handle);
    }
    for (const auto &file : runtime)
        if (fs::exists(file, error) && !fs::remove(file, error))
            throw std::runtime_error("Close skate. first: " + launcher_text::utf8(file.filename().wstring()) + " is in use.");
    fs::remove(game / L"ReSkateLauncher.settings.json", error);
    // Shortcuts and the right-click entry: only ours, pointing at this install.
    const bool com = SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    for (const auto &folder : {known_folder(FOLDERID_Desktop), known_folder(FOLDERID_Programs)}) {
        if (folder.empty()) continue;
        const auto link = folder / L"ReSkate.lnk";
        const auto target = shortcut_target(link);
        if (!target.empty() && fs::equivalent(target, launcher, error)) fs::remove(link, error);
    }
    if (com) CoUninitialize();
    std::array<wchar_t, 2048> command{};
    DWORD size = static_cast<DWORD>(command.size() * sizeof(wchar_t));
    const auto command_key = std::wstring(verb_key) + L"\\command";
    if (RegGetValueW(HKEY_CURRENT_USER, command_key.c_str(), nullptr, RRF_RT_REG_SZ, nullptr, command.data(), &size) ==
            ERROR_SUCCESS &&
        std::wstring_view(command.data()).find(launcher.wstring()) != std::wstring_view::npos)
        RegDeleteTreeW(HKEY_CURRENT_USER, verb_key);
    // The running launcher cannot delete itself: a hidden command does, once it has exited,
    // trying each second for half a minute (shutdown, or a virus scanner, can hold it a while).
    const auto quoted = L"\"" + launcher.wstring() + L"\"";
    std::wstring line = L"cmd.exe /c for /l %i in (1,1,30) do @(del /f /q " + quoted + L" >nul 2>&1 & if not exist " + quoted +
                        L" (exit /b 0) else (timeout /t 1 /nobreak >nul))";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (CreateProcessW(nullptr, line.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, game.c_str(), &startup,
                       &process)) {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
    dingosdk::logging::write(dingosdk::logging::Level::info, dingosdk::logging::Channel::launcher,
                             L"Removed ReSkate from " + game.wstring());
}

void register_mod_install_verb(const fs::path &launcher) {
    // Under SystemFileAssociations: an extra entry in .zip's menu, whatever program opens .zip.
    HKEY verb{};
    if (RegCreateKeyExW(HKEY_CURRENT_USER, verb_key, 0,
                        nullptr, 0, KEY_WRITE, nullptr, &verb, nullptr) != ERROR_SUCCESS)
        return;
    set_value(verb, L"MUIVerb", L"Install as ReSkate mod");
    set_value(verb, L"Icon", launcher.wstring());
    HKEY command{};
    if (RegCreateKeyExW(verb, L"command", 0, nullptr, 0, KEY_WRITE, nullptr, &command, nullptr) == ERROR_SUCCESS) {
        set_value(command, nullptr, L"\"" + launcher.wstring() + L"\" --install-mod \"%1\"");
        RegCloseKey(command);
    }
    RegCloseKey(verb);
}
} // namespace dingosdk::launcher_app
