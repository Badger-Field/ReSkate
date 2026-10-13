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

void register_mod_install_verb(const fs::path &launcher) {
    // Under SystemFileAssociations: an extra entry in .zip's menu, whatever program opens .zip.
    HKEY verb{};
    if (RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Classes\\SystemFileAssociations\\.zip\\shell\\ReSkateInstallMod", 0,
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
