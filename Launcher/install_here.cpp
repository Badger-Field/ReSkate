#include "install_here.h"
#include "steam_library.h"
#include "Engine/Core/Log/logging.h"

#include <Windows.h>
#include <shlobj.h>
#include <shobjidl.h>

#include <array>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <system_error>

namespace fs = std::filesystem;

namespace dingosdk::launcher_app {
namespace {
constexpr wchar_t skate_app[] = L"3354750";
// What an install carries: the launcher and runtime always, the networking library for playing
// without Steam when it came with them.
constexpr std::array<const wchar_t *, 3> files{L"ReSkateLauncher.exe", L"ReSkate.dll", L"GameNetworkingSockets.dll"};

std::wstring widen(std::string_view text) {
    if (text.empty()) return {};
    const auto n = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    std::wstring out(static_cast<std::size_t>(std::max(n, 0)), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), n);
    return out;
}
std::string read_text(const fs::path &path) {
    std::ifstream in(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}
std::optional<fs::path> steam_folder() {
    std::array<wchar_t, MAX_PATH * 2> value{};
    DWORD size = static_cast<DWORD>(value.size() * sizeof(wchar_t));
    if (RegGetValueW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", L"SteamPath", RRF_RT_REG_SZ, nullptr, value.data(), &size) ==
        ERROR_SUCCESS)
        return fs::path(value.data());
    size = static_cast<DWORD>(value.size() * sizeof(wchar_t));
    if (RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\WOW6432Node\\Valve\\Steam", L"InstallPath", RRF_RT_REG_SZ, nullptr,
                     value.data(), &size) == ERROR_SUCCESS)
        return fs::path(value.data());
    return std::nullopt;
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
void set_value(HKEY key, const wchar_t *name, const std::wstring &value) {
    RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE *>(value.c_str()),
                   static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
}
} // namespace

std::optional<fs::path> find_steam_skate() {
    const auto steam = steam_folder();
    if (!steam) return std::nullopt;
    std::vector<fs::path> libraries{*steam};
    for (const auto &folder : steam_library::library_folders(read_text(*steam / L"steamapps" / L"libraryfolders.vdf")))
        libraries.emplace_back(widen(folder));
    std::error_code error;
    for (const auto &library : libraries) {
        const auto manifest = library / L"steamapps" / (std::wstring(L"appmanifest_") + skate_app + L".acf");
        const auto folder = steam_library::manifest_value(read_text(manifest), "installdir");
        if (!folder || !steam_library::plain_folder_name(*folder)) continue;
        const auto game = library / L"steamapps" / L"common" / widen(*folder);
        if (fs::is_regular_file(game / L"Skate.exe", error)) return game;
    }
    return std::nullopt;
}

fs::path install_into(const fs::path &game, const fs::path &from) {
    std::error_code error;
    if (!fs::is_regular_file(game / L"Skate.exe", error)) throw std::runtime_error("That folder has no Skate.exe.");
    if (!fs::is_regular_file(from / L"ReSkate.dll", error))
        throw std::runtime_error("ReSkate.dll is not beside the launcher. Unzip the whole ReSkate download first.");
    if (fs::equivalent(game, from, error)) return game / L"ReSkateLauncher.exe";
    for (const auto *name : files) {
        const auto source = from / name;
        if (!fs::is_regular_file(source, error)) continue;
        // Copied beside, then moved over, so a failed copy never leaves half a file in the game.
        const auto target = game / name;
        auto staged = target;
        staged += L".new";
        fs::copy_file(source, staged, fs::copy_options::overwrite_existing, error);
        if (error || !MoveFileExW(staged.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
            fs::remove(staged, error);
            throw std::runtime_error("Could not copy " + fs::path(name).string() +
                                     " into the game folder. Close skate. and try again.");
        }
    }
    const auto launcher = game / L"ReSkateLauncher.exe";
    // Shortcuts: best effort, the install stands without them.
    const bool com = SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    for (const auto &folder : {known_folder(FOLDERID_Desktop), known_folder(FOLDERID_Programs)})
        if (!folder.empty()) shortcut(folder / L"ReSkate.lnk", launcher);
    if (com) CoUninitialize();
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

void uninstall_from(const fs::path &game) {
    std::error_code error;
    const auto launcher = game / L"ReSkateLauncher.exe";
    if (!fs::is_regular_file(game / L"Skate.exe", error) || !fs::is_regular_file(launcher, error))
        throw std::runtime_error("ReSkate is not installed in a skate. folder here.");
    // The runtime first: while skate. runs it cannot be removed, and nothing else is touched yet.
    for (const auto *name : {L"ReSkate.dll", L"GameNetworkingSockets.dll"}) {
        const auto file = game / name;
        if (fs::exists(file, error) && !fs::remove(file, error))
            throw std::runtime_error("Close skate. first: " + fs::path(name).string() + " is in use.");
    }
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
    // The running launcher cannot delete itself: a hidden command does, once it has exited.
    std::wstring line = L"cmd.exe /c timeout /t 3 /nobreak >nul & del /f /q \"" + launcher.wstring() + L"\"";
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
