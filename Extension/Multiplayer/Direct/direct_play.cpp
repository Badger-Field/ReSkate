#include "direct_play.h"
#include "direct_lobby.h"
#include "Extension/Multiplayer/Net/protocol.h"
#include "Engine/Core/Platform/launcher_support.h"
#include "Engine/Core/Text/word_filter.h"

#include <Windows.h>

#include <array>
#include <filesystem>
#include <fstream>
#include <mutex>

namespace dingosdk::multiplayer {
namespace {
// Public STUN servers: they only tell a player their address as the internet sees it.
constexpr char stun_servers[] = "stun.cloudflare.com:3478,stun.l.google.com:19302";

std::mutex player_mutex;

std::filesystem::path module_folder() {
    HMODULE module{};
    static const int anchor{};
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&anchor), &module))
        return {};
    std::wstring path(32768, L'\0');
    const auto length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
    if (!length || length >= path.size()) return {};
    path.resize(length);
    return std::filesystem::path(path).parent_path();
}
std::filesystem::path library_path() {
    const auto folder = module_folder();
    return folder.empty() ? folder : folder / L"GameNetworkingSockets.dll";
}
std::filesystem::path player_file() {
    std::array<wchar_t, 32768> buffer{};
    const auto n = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!n || n >= buffer.size()) return {};
    return std::filesystem::path(buffer.data()) / L"ReSkate" / L"profiles" / L"offline" / L"direct-player.txt";
}

struct Stored {
    std::optional<std::array<std::uint8_t, 32>> seed;
    std::string name;
};
Stored read_player() {
    Stored stored;
    std::ifstream in(player_file(), std::ios::binary);
    std::string seed, name;
    if (in && std::getline(in, seed)) {
        if (!seed.empty() && seed.back() == '\r') seed.pop_back();
        stored.seed = from_hex<32>(seed);
        if (std::getline(in, name)) {
            if (!name.empty() && name.back() == '\r') name.pop_back();
            if (name.size() <= max_direct_name && valid_member_name(name)) stored.name = name;
        }
    }
    return stored;
}
bool write_player(const std::array<std::uint8_t, 32> &seed, const std::string &name) {
    const auto file = player_file();
    if (file.empty()) return false;
    std::error_code ignored;
    std::filesystem::create_directories(file.parent_path(), ignored);
    // Written beside, then moved over: a crash mid-write never leaves a broken key.
    auto temporary = file;
    temporary += L".new";
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        out << to_hex(seed) << '\n' << name << '\n';
        if (!out.flush()) return false;
    }
    return MoveFileExW(temporary.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
}
// The key kept with the profile, made the first time.
std::optional<PlayerKey> kept_key(Stored &stored) {
    if (stored.seed) return player_key_from_seed(*stored.seed);
    auto key = generate_player_key();
    if (!key || !write_player(key->seed, stored.name)) return std::nullopt;
    stored.seed = key->seed;
    return key;
}
std::string default_name(std::uint64_t id) { return "Skater " + std::to_string(id % 10000); }

std::string lobby_url() {
    std::array<char, 512> value{};
    const auto n = GetEnvironmentVariableA("RESKATE_LOBBY_URL", value.data(), static_cast<DWORD>(value.size()));
    return n && n < value.size() ? std::string(value.data(), n) : std::string(default_lobby_url);
}
} // namespace

bool direct_play_installed() {
    // Asked every menu frame: the library is looked for once a start.
    static const bool installed = [] {
        if (!launcher::offline_mode()) return false;
        std::error_code ignored;
        const auto path = library_path();
        return !path.empty() && std::filesystem::is_regular_file(path, ignored);
    }();
    return installed;
}

std::optional<DirectPlay> prepare_direct_play(std::string &error) {
    if (!launcher::offline_mode()) {
        error = "Direct multiplayer is for offline mode.";
        return std::nullopt;
    }
    const auto path = library_path();
    if (path.empty() || !direct_play_installed()) {
        error = "Playing without Steam needs GameNetworkingSockets.dll beside ReSkate.dll.";
        return std::nullopt;
    }
    std::lock_guard lock(player_mutex);
    auto stored = read_player();
    const auto key = kept_key(stored);
    if (!key) {
        error = "Could not make or keep this player's key in %LOCALAPPDATA%\\ReSkate\\profiles\\offline.";
        return std::nullopt;
    }
    // From the folder ReSkate.dll is in, and Windows' own folders for what it needs.
    const auto library = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) {
        error = "GameNetworkingSockets.dll did not load (Windows error " + std::to_string(GetLastError()) +
                "). It needs the Microsoft Visual C++ 2015-2022 runtime.";
        return std::nullopt;
    }
    DirectPlay play;
    play.library = library;
    play.key = *key;
    play.id = player_id_of(key->public_key);
    play.name = stored.name.empty() ? default_name(play.id) : stored.name;
    play.lobby_url = lobby_url();
    play.routes.stun_servers = stun_servers;
    return play;
}

bool open_direct_play(SteamTransport &transport) {
    if (transport.status().ready) return transport.direct();
    std::string error;
    auto play = prepare_direct_play(error);
    if (!play) {
        transport.set_detail(error);
        return false;
    }
    if (!transport.open_direct(play->library, play->id, make_lobby_signaling(play->lobby_url, play->key), play->routes))
        return false;
    transport.on_key_taken([] { replace_direct_player_key(); });
    transport.remember_name(play->id, play->name);
    return true;
}

void replace_direct_player_key() {
    std::lock_guard lock(player_mutex);
    const auto stored = read_player();
    if (const auto key = generate_player_key()) write_player(key->seed, stored.name);
}

std::string direct_player_name() {
    std::lock_guard lock(player_mutex);
    auto stored = read_player();
    if (!stored.name.empty()) return stored.name;
    const auto key = kept_key(stored);
    return key ? default_name(player_id_of(key->public_key)) : std::string("Skater");
}

std::string set_direct_player_name(std::string_view name) {
    const auto first = name.find_first_not_of(' ');
    name = first == std::string_view::npos ? std::string_view{} : name.substr(first, name.find_last_not_of(' ') - first + 1);
    if (name.empty()) return "Enter a name.";
    if (name.size() > max_direct_name) return "Names are at most " + std::to_string(max_direct_name) + " bytes.";
    if (!valid_member_name(name)) return "Names cannot have control characters or broken text.";
    if (text::contains_bad_words(name)) return "That name contains blocked words. Choose another one.";
    std::lock_guard lock(player_mutex);
    auto stored = read_player();
    const auto key = kept_key(stored);
    if (!key || !write_player(key->seed, std::string(name))) return "Could not save the name.";
    return {};
}
} // namespace dingosdk::multiplayer
