#include "board_material.h"
#include "worn_items.h"
#include "Engine/Game/Build/supported_build.h"
#include "local_customization_runtime.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Core/Platform/memory.h"
#include "Engine/Vfs/content_catalogs.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Extension/Multiplayer/Remote/native_pose_layout.h"
#include "Extension/Multiplayer/Remote/native_skater.h"
#include <Windows.h>
#include <format>
#include <fstream>
#include <iterator>
#include <memory>
#include <mutex>
#include <thread>

namespace dingosdk::board_material {
namespace {
struct State {
    std::uintptr_t base{}, client{};
    std::vector<detail::Hold> holds;
    std::vector<detail::Saved> saved;
    std::optional<bool> functions; // the material publisher's bytes, once checked
    std::optional<bool> setter;    // the typed setter's bytes and the ColorRgb descriptor, once checked
    std::uint16_t colour_type{};   // the ColorRgb descriptor's type index
    std::uint64_t reported{};
    std::uint64_t apply_reported{};
    // The board's items as last read (worn_items::worn_board), and when.
    std::vector<worn_items::Worn> worn;
    std::uint64_t worn_at{};
    std::array<detail::Gate, part_names.size()> gates;
    // The item each part was last reported as wearing: once per change, so a mod's maker can read
    // the key their reskate-materials.json has to name from the log.
    std::array<std::string, part_names.size()> reported_items;
    std::array<std::size_t, part_names.size()> reported_counts{};
};
State& state() {
    static auto* value = new State; // what is held may still be out at exit
    return *value;
}
const auto peek = [](std::uintptr_t address, void* out, std::size_t size) { return memory::peek_bytes(address, out, size); };

bool write_value(std::uintptr_t material, std::uintptr_t node, const float* value, std::size_t bytes) noexcept {
    __try {
        std::memcpy(reinterpret_cast<void*>(node + addr::native_cosmetics::parameter_value), value, bytes);
        *reinterpret_cast<std::uint8_t*>(material + addr::native_cosmetics::material_changed) = 1;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool publish_materials(std::uintptr_t base, std::uintptr_t item) noexcept {
    __try {
        reinterpret_cast<void (*)(std::uintptr_t, int)>(base + addr::native_cosmetics::publish_materials)(item, 0);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
// A declared colour the material does not hold yet, added by the engine's own typed setter
// (MaterialInstance:addEsVector's, as developer_board.cpp adds the board's colours): it allocates,
// hashes and links the node itself. Priority zero, a native preset's, so a later preset replaces it.
bool insert_colour(std::uintptr_t base, std::uint16_t type_index, std::uintptr_t material, const detail::Declared& declared,
                   const Colour& value) noexcept {
    const developer_hoodie_detail::ParameterKey key{declared.identity, sizeof(Colour), type_index, 1, declared.key};
    const developer_board_detail::NativeColor native{value, 0, base + addr::native_cosmetics::color_rgb_type};
    __try {
        return reinterpret_cast<std::uint64_t (*)(std::uintptr_t, const developer_hoodie_detail::ParameterKey*,
                                                  const developer_board_detail::NativeColor*, std::uint8_t)>(
                   base + addr::native_cosmetics::set_shader_parameter)(material, &key, &native, 0) != 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool functions_match(State& s) {
    if (!s.functions) {
        std::array<unsigned char, addr::native_cosmetics::publish_materials_prefix.size()> bytes{};
        s.functions = peek(s.base + addr::native_cosmetics::publish_materials, bytes.data(), bytes.size()) &&
                      bytes == addr::native_cosmetics::publish_materials_prefix;
        if (!*s.functions)
            logging::log(logging::Level::warning, logging::Channel::customization,
                         "Board parameters: the material publisher differs from the supported build; plugins cannot set them.");
    }
    return *s.functions;
}
// The typed setter, and the ColorRgb type it is given, as the supported build has them (checked as
// developer_board_detail::materials checks the descriptor). Without them nothing declared is added.
bool setter_matches(State& s) {
    if (!s.setter) {
        namespace n = addr::native_cosmetics;
        std::array<unsigned char, n::set_shader_parameter_prefix.size()> bytes{};
        std::uintptr_t info{};
        std::uint32_t hash{};
        std::uint16_t size{}, index{};
        const auto type = s.base + n::color_rgb_type;
        s.setter = peek(s.base + n::set_shader_parameter, bytes.data(), bytes.size()) && bytes == n::set_shader_parameter_prefix &&
                   peek(type, &info, sizeof(info)) && info >= s.base && info < s.base + supported_build::game_image_size && peek(info, &hash, sizeof(hash)) &&
                   hash == n::color_rgb_type_hash && peek(info + 6, &size, sizeof(size)) && size == sizeof(Colour) &&
                   peek(type + 8, &index, sizeof(index));
        s.colour_type = index;
        if (!*s.setter)
            logging::log(logging::Level::warning, logging::Channel::customization,
                         "Board parameters: the material setter differs from the supported build; declared colours cannot be added.");
    }
    return *s.setter;
}

// ---- declarations: every enabled mod's reskate-materials.json -------------------------------------------
// Read on a thread of their own and again only when a file changes, as the mods' music is
// (local_music_assets.cpp): the game's frame never waits on the disk.
struct Declarations {
    std::mutex mutex;
    std::shared_ptr<const std::vector<mods::MaterialDeclaration>> ready;
    std::vector<std::filesystem::file_time_type> stamps;
    bool reading{};
    std::uint64_t looked{};
};
Declarations& declarations() {
    static auto* value = new Declarations;
    return *value;
}
std::vector<std::filesystem::file_time_type> declaration_stamps() {
    std::vector<std::filesystem::file_time_type> stamps;
    for (const auto& mod : mods::catalog().mods) {
        std::error_code error;
        const auto stamp = std::filesystem::last_write_time(mod.directory / L"reskate-materials.json", error);
        stamps.push_back(error ? std::filesystem::file_time_type::min() : stamp);
    }
    return stamps;
}
std::vector<mods::MaterialDeclaration> read_declarations() {
    std::vector<mods::MaterialDeclaration> result;
    for (const auto& mod : mods::catalog().mods) {
        const auto path = mod.directory / L"reskate-materials.json";
        std::error_code error;
        if (!std::filesystem::is_regular_file(path, error) || std::filesystem::file_size(path, error) > 64 * 1024) continue;
        try {
            std::ifstream in(path, std::ios::binary);
            auto read = mods::parse_material_declarations(mod.name, std::string((std::istreambuf_iterator<char>(in)), {}));
            std::size_t count{};
            for (const auto& d : read) count += d.parameters.size();
            logging::log(logging::Level::info, logging::Channel::customization,
                         "Board parameters: {} declares {} shader input{} for its items.", mod.name, count, count == 1 ? "" : "s");
            std::move(read.begin(), read.end(), std::back_inserter(result));
        } catch (const std::exception& failure) {
            logging::log(logging::Level::warning, logging::Channel::customization,
                         "Board parameters: {}'s reskate-materials.json is left out: {}", mod.name, failure.what());
        }
    }
    return result;
}
void read_declarations_now() noexcept {
    auto& d = declarations();
    std::shared_ptr<const std::vector<mods::MaterialDeclaration>> read;
    std::vector<std::filesystem::file_time_type> stamps;
    try {
        stamps = declaration_stamps();
        bool same{};
        {
            std::lock_guard lock(d.mutex);
            same = d.ready && stamps == d.stamps;
        }
        if (!same) read = std::make_shared<const std::vector<mods::MaterialDeclaration>>(read_declarations());
    } catch (...) {}
    std::lock_guard lock(d.mutex);
    if (read) {
        d.ready = std::move(read);
        d.stamps = std::move(stamps);
    } else if (!d.ready) {
        d.ready = std::make_shared<const std::vector<mods::MaterialDeclaration>>();
    }
    d.reading = false;
}
// What was last read, and a read started when the last began two seconds ago or more.
std::shared_ptr<const std::vector<mods::MaterialDeclaration>> current_declarations() {
    auto& d = declarations();
    std::lock_guard lock(d.mutex);
    const auto now = GetTickCount64();
    if (!d.reading && (!d.ready || now - d.looked >= 2000)) {
        d.reading = true;
        d.looked = now;
        try {
            std::thread(read_declarations_now).detach();
        } catch (...) {
            d.reading = false;
        }
    }
    return d.ready;
}
// Whether the game sells an item, or cannot tell yet (no content pack read): either way its
// declarations do not apply. Only a mod's own items take them.
bool sold(std::string_view item) {
    return !content_cache::catalogs().available || profile_runtime::reserved_cosmetic(item);
}
constexpr std::array<std::string_view, part_names.size()> worn_slots{"Deck", "Grip tape", "Trucks", "Wheels"};

// The equipped items' declarations, on the board found: a part's apply once its item and material
// have settled (detail::settled).
void declare(State& s, detail::Found& found) {
    const auto all = current_declarations();
    if (!all || all->empty()) return;
    const auto now = GetTickCount64();
    if (now - s.worn_at >= 250 || s.worn.empty()) {
        s.worn = worn_items::worn_board(false);
        s.worn_at = now;
    }
    for (std::size_t part = 0; part < part_names.size(); ++part) {
        std::string_view item;
        for (const auto& w : s.worn)
            if (w.slot == worn_slots[part]) item = w.item;
        std::uintptr_t material{};
        for (const auto& m : found.materials)
            if (static_cast<std::size_t>(m.part) == part) { material = m.address; break; }
        if (!detail::settled(s.gates[part], item, material, now) || found.pending) continue;
        auto is_sold = [](std::string_view i) { return sold(i); };
        auto add = detail::declared_for(*all, static_cast<Part>(part), item, is_sold);
        // Report a hot-reloaded declaration even when the worn item did not change.
        if (s.reported_items[part] != item || s.reported_counts[part] != add.size()) {
            s.reported_items[part] = std::string(item);
            s.reported_counts[part] = add.size();
            logging::log(logging::Level::info, logging::Channel::customization,
                         "Board parameters: the {} is {}; {} declared shader input{} apply.", part_names[part], item,
                         add.size(), add.size() == 1 ? "" : "s");
        }
        found.declared.insert(found.declared.end(), add.begin(), add.end());
    }
}
// The local player's board as it is now (a change of cosmetics can replace it); none out of play.
std::optional<detail::Found> board(State& s) {
    if (!s.base || !s.client) return std::nullopt;
    try {
        const auto local = multiplayer::capture_local(s.base, s.client, false);
        if (!local.ready) return std::nullopt;
        const auto entity = multiplayer::read_native_board(peek, s.base, local.entity).entity;
        if (!entity) return std::nullopt;
        auto found = detail::walk(peek, s.base, entity);
        declare(s, found);
        return found;
    } catch (const std::exception& error) {
        // (Once in a while: a board being built reads as unfinished for a few ticks.)
        if (const auto now = GetTickCount64(); now - s.reported > 10000) {
            s.reported = now;
            logging::log(logging::Level::debug, logging::Channel::customization, "Board parameters: {}", error.what());
        }
        return std::nullopt;
    }
}
} // namespace

std::optional<std::vector<Parameter>> read(Part part) {
    auto& s = state();
    const auto found = board(s);
    if (!found) return std::nullopt;
    return detail::listed(*found, part);
}

std::string set(std::string_view owner, Part part, std::uint64_t key, const Colour& value, bool colour) {
    auto& s = state();
    const auto found = board(s);
    if (!found) return "there is no board in play";
    Kind kind{};
    if (auto why = detail::refusal(*found, part, key, value, colour, kind); !why.empty()) return why;
    if (!functions_match(s)) return "board parameters are unavailable with this game build";
    if (detail::declared(*found, part, key) && !detail::live(*found, part, key) && !setter_matches(s))
        return "declared colours cannot be added with this game build";
    return detail::take(s.holds, *found, owner, part, key, value, colour);
}

void clear(std::string_view owner, std::optional<Part> part, std::optional<std::uint64_t> key) {
    detail::release(state().holds, owner, part, key);
}

void tick(std::uintptr_t base, std::uintptr_t client) noexcept {
    try {
        auto& s = state();
        s.base = base, s.client = client;
        if (s.holds.empty() && s.saved.empty()) return;
        if (!functions_match(s)) return;
        const auto found = board(s);
        if (!found) return;
        if (const auto dropped = detail::drop_undeclared(s.holds, *found))
            logging::log(logging::Level::info, logging::Channel::customization,
                         "Board parameters: {} declared value{} let go: the item that declared {} is no longer on the board.", dropped,
                         dropped == 1 ? "" : "s", dropped == 1 ? "it" : "them");
        auto write = [](std::uintptr_t material, std::uintptr_t node, Kind kind, const Colour& value) {
            return write_value(material, node, value.data(), kind == Kind::colour ? sizeof(Colour) : sizeof(float));
        };
        std::size_t inserted{}, refused{}, published{}, publish_failed{};
        auto publish = [&](std::uintptr_t item) {
            const bool ok = publish_materials(s.base, item);
            if (ok) ++published;
            else ++publish_failed;
        };
        auto insert = [&](std::uintptr_t material, const detail::Declared& declared, const Colour& value) {
            const bool added = setter_matches(s) && insert_colour(s.base, s.colour_type, material, declared, value);
            if (added) ++inserted;
            else ++refused;
            return added;
        };
        detail::apply(s.saved, s.holds, *found, write, publish, insert);
        // Lua accepts a hold before this tick applies it; report native failures separately.
        if ((inserted || refused || publish_failed) && GetTickCount64() - s.apply_reported >= 5000) {
            s.apply_reported = GetTickCount64();
            logging::log(logging::Level::info, logging::Channel::customization,
                         "Board parameter apply: {} inserted, {} refused, {} published, {} publication failures.",
                         inserted, refused, published, publish_failed);
        }
    } catch (...) {}
}
} // namespace dingosdk::board_material
