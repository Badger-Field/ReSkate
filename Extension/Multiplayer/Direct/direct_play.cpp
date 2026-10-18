#include "direct_play.h"
#include "direct_lobby.h"
#include "Extension/Multiplayer/Net/protocol.h"
#include "Engine/Core/Platform/launcher_support.h"
#include "Engine/Core/Text/word_filter.h"

#include <Windows.h>
#include <bcrypt.h>
#include <dpapi.h>

#include <array>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <span>
#include <vector>

namespace dingosdk::multiplayer {
namespace {
// Public STUN servers: they only tell a player their address as the internet sees it.
constexpr char stun_servers[] = "stun.cloudflare.com:3478,stun.l.google.com:19302";

// The GameNetworkingSockets.dll this ReSkate was tested with (d534c19, static CRT, ICE): any
// other file of that name is refused rather than loaded into the game.
constexpr std::string_view library_sha256 = "af89ad2f99915d6bba320a1fa5f36c9ac0813236b73ea034bce35df4866172b9";

std::mutex player_mutex;
std::string known_name; // direct_player_name's, once read

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

std::string file_sha256(const std::filesystem::path &path) {
    std::ifstream in(path, std::ios::binary);
    const std::vector<char> bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (!in.eof() && in.fail()) return {};
    std::array<std::uint8_t, 32> digest{};
    if (!BCRYPT_SUCCESS(BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0,
                                   reinterpret_cast<PUCHAR>(const_cast<char *>(bytes.data())),
                                   static_cast<ULONG>(bytes.size()), digest.data(), static_cast<ULONG>(digest.size()))))
        return {};
    return to_hex(digest);
}

// The seed is kept encrypted for this Windows user (DPAPI): "dpapi:<hex>". A seed from before
// that, in plain hex, is read once and written back encrypted.
std::string protect(const std::array<std::uint8_t, 32> &seed) {
    DATA_BLOB in{static_cast<DWORD>(seed.size()), const_cast<BYTE *>(seed.data())}, out{};
    if (!CryptProtectData(&in, L"ReSkate player key", nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &out)) return {};
    std::string text = "dpapi:" + to_hex(std::span<const std::uint8_t>(out.pbData, out.cbData));
    LocalFree(out.pbData);
    return text;
}
std::optional<std::array<std::uint8_t, 32>> unprotect(std::string_view text) {
    if (!text.starts_with("dpapi:")) return from_hex<32>(text);
    text.remove_prefix(6);
    if (text.size() % 2 || text.size() > 8192) return std::nullopt;
    std::vector<std::uint8_t> blob(text.size() / 2);
    for (std::size_t i = 0; i < blob.size(); ++i) {
        const auto byte = from_hex<1>(text.substr(i * 2, 2));
        if (!byte) return std::nullopt;
        blob[i] = (*byte)[0];
    }
    DATA_BLOB in{static_cast<DWORD>(blob.size()), blob.data()}, out{};
    if (!CryptUnprotectData(&in, nullptr, nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &out)) return std::nullopt;
    std::optional<std::array<std::uint8_t, 32>> seed;
    if (out.cbData == 32) {
        seed.emplace();
        std::copy_n(out.pbData, 32, seed->begin());
    }
    SecureZeroMemory(out.pbData, out.cbData);
    LocalFree(out.pbData);
    return seed;
}

struct Stored {
    std::optional<std::array<std::uint8_t, 32>> seed;
    std::string name;
    bool plain{}; // the seed was not yet encrypted
};
Stored read_player() {
    Stored stored;
    std::ifstream in(player_file(), std::ios::binary);
    std::string seed, name;
    if (in && std::getline(in, seed)) {
        if (!seed.empty() && seed.back() == '\r') seed.pop_back();
        stored.plain = !seed.starts_with("dpapi:");
        stored.seed = unprotect(seed);
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
    const auto protected_seed = protect(seed);
    if (protected_seed.empty()) return false;
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        out << protected_seed << '\n' << name << '\n';
        if (!out.flush()) return false;
    }
    return MoveFileExW(temporary.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
}
// The key kept with the profile, made the first time.
std::optional<PlayerKey> kept_key(Stored &stored) {
    if (stored.seed) {
        if (stored.plain && write_player(*stored.seed, stored.name)) stored.plain = false;
        return player_key_from_seed(*stored.seed);
    }
    auto key = generate_player_key();
    if (!key || !write_player(key->seed, stored.name)) return std::nullopt;
    stored.seed = key->seed;
    return key;
}
std::string default_name(std::uint64_t id) { return "Skater " + std::to_string(id % 10000); }

// RESKATE_LOBBY_URL may point at another lobby service, over https only (a service on this PC,
// for tests, may use http).
std::string lobby_url() {
    std::array<char, 512> value{};
    const auto n = GetEnvironmentVariableA("RESKATE_LOBBY_URL", value.data(), static_cast<DWORD>(value.size()));
    if (!n || n >= value.size()) return default_lobby_url;
    const std::string url(value.data(), n);
    const bool local = url.starts_with("http://127.0.0.1:") || url.starts_with("http://localhost:");
    return url.starts_with("https://") || local ? url : std::string(default_lobby_url);
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
    if (file_sha256(path) != library_sha256) {
        error = "GameNetworkingSockets.dll is not the one this ReSkate was built with. Use the copy from the "
                "same ReSkate release.";
        return std::nullopt;
    }
    // From the folder ReSkate.dll is in, and Windows' own folders for what it needs.
    const auto library = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) {
        error = "GameNetworkingSockets.dll did not load (Windows error " + std::to_string(GetLastError()) + ").";
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
    // Shown every menu frame: read from the file once, then kept here.
    if (!known_name.empty()) return known_name;
    auto stored = read_player();
    if (!stored.name.empty()) return known_name = stored.name;
    const auto key = kept_key(stored);
    return known_name = key ? default_name(player_id_of(key->public_key)) : std::string("Skater");
}

std::string set_direct_player_name(std::string_view name) {
    const auto first = name.find_first_not_of(' ');
    name = first == std::string_view::npos ? std::string_view{} : name.substr(first, name.find_last_not_of(' ') - first + 1);
    if (name.empty()) return "Enter a name.";
    if (name.size() > max_direct_name) return "Names are at most " + std::to_string(max_direct_name) + " bytes.";
    if (!valid_member_name(name)) return "Names cannot have control characters or broken text.";
    if (text::contains_bad_words(name)) return "That name contains blocked words. Choose another one.";
    if (reserved_name(name)) return "That name is the game's own. Choose another one.";
    std::lock_guard lock(player_mutex);
    auto stored = read_player();
    const auto key = kept_key(stored);
    if (!key || !write_player(key->seed, std::string(name))) return "Could not save the name.";
    known_name = name;
    return {};
}
} // namespace dingosdk::multiplayer
