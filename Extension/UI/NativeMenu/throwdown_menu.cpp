#include "throwdown_menu.h"
#include "native_menu_data.h"
#include "native_menu_lifetime.h"
#include "native_menu_presenters.h"
#include "Extension/Throwdowns/custom_modes.h"
#include "Extension/Throwdowns/custom_placement.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Game/Build/addresses.h"
#include "Engine/Game/Build/20260929/engine.h"
#include "Engine/Game/Build/20260929/native_menu.h"
#include <Windows.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <utility>

// The Throwdowns page's native picker and setup, after SmokeyDawg58's
// one_up_menu.cpp and throwdown_modes_ui.inl (reskate-custom-mods): every view is
// a native Rime model, built from the game's own records or the mode's mod.
// The page is his logo grid, whose widgets and records ReSkate carries (the
// throwdown_menu built-in pack, tools/builtin/build_throwdown_menu_pack.py).
// Should they not load, the modes' cards follow the three stock cards.
namespace dingosdk::multiplayer {
namespace {
using namespace menu_data;
namespace native = addr::native_menu;
namespace engine = addr::engine;
constexpr Schema tile_schema{0xdeb20e0f, 1200}, category_schema{0x628aa99c, 320}, anchor_schema{0x0e3be640, 192},
    label_schema{0xbfe23948, 104}, panel_schema{0xd29bbf63, 576}, header_schema{0x4efe5e19, 312},
    setting_schema{0x94803e85, 176}, selector_schema{0xf45cb4c0, 1344}, selector_item_schema{0x6d9d5106, 312},
    notice_schema{0x7290e514, 256}, texture_schema{0x370c9975, 144}, content_tile_schema{0xcbe47f59, 1184};
constexpr std::uint32_t content = 0x716496c8, items = 0x61742cb4, widget = 0x214d4984, rows_field = 0x67223da7,
    text_field = 0x4d8e01b9;
constexpr std::uint32_t slider_hash = 0xcdc749b2;
// Fields a private copy borrows rather than copies: styles, transitions and
// backend expression contexts are read-only.
constexpr std::array<std::uint32_t, 9> borrowed_fields{0x8cc042ef, 0xa1e84eb1, 0x25189231, 0xf94d8cc6, 0x8a3e2159,
                                                       0x54c9abee, 0xd78d240b, 0x85b8c3e6, 655725074U};
bool borrowed(std::uint32_t key) { return std::find(borrowed_fields.begin(), borrowed_fields.end(), key) != borrowed_fields.end(); }
using Action = MenuAction;
struct Descriptor { alignas(8) std::array<std::byte, 0x70> bytes{}; Address info{}; Action action; };
struct Original {
    Value tile, category, description;
    std::array<float, 2> tile_size{}, description_size{};
    float icon_width{}, icon_height{};
};
struct ModeCard { Value tile, category, description, label, logo; };
struct SetupRows {
    Value list;
    std::vector<Ref> records;
    std::vector<Handle> handles;
    // A typed native owner for the original inline rows: raw saved bytes
    // cannot keep their nested arrays once the live list changes count.
    Value retained_rows;
    bool stack{}, owned_tiles{};
    Value timer_row;
    unsigned timer_index{};
    std::map<std::string, Value> extra;
    struct Choice { Value selector; std::vector<Handle> options; int shown = -1, pending = -1; std::uint64_t pending_at{}; };
    std::map<std::string, Choice> choices;
};
struct State {
    Address manager{}, base{}, anchor_asset{};
    Value page, cards, source_label, original_body;
    std::vector<Original> originals;
    std::map<std::string, ModeCard> mode_cards;
    Value picker_grid, picker_anchor;
    std::array<Value, 3> picker_stock, picker_stock_logos;
    bool grid_failed{}, cards_scaled{};
    std::map<Handle, std::pair<Value, Value>> official_originals;
    std::set<Handle> official_panels;
    std::map<Handle, Value> official_headers, official_contents, official_sliders, official_badges;
    std::map<Handle, Handle> official_slot_panels;
    std::map<Handle, SetupRows> official_setup_rows;
    // Each native slider presentation's row: players, timer or a row's id.
    std::map<Handle, std::string> slider_rows;
    std::string official_mode;
    std::map<std::string, Address, std::less<>> assets;
    std::set<std::string> missing_art;
    OwnedMenuModels<Value> owned;
    std::set<std::pair<Handle, Handle>> generations;
    std::array<Descriptor, 128> descriptors;
    unsigned used{};
    std::mutex mutex;
    std::vector<Action> pending;
    std::atomic<std::uint64_t> owner{};
    std::uint64_t next_id{}, next_tick{}, pass{}, entry_guard_until{};
};
State& state() { static auto* s = new State; return *s; }

Value reference(const Context& c, Ref ref) { return {ref.handle, c.type_of(ref.handle)}; }
Value primary(const Context& c, Value tile) {
    return c.path(tile, {widget, 0x0fb0d794, 0xa704272a, 0xc52416ef, 0xa704272a, 0xa2b71c93});
}
std::optional<Value> mounted_body(const Context& c, Value p) {
    if (!p.handle || c.type_of(p.handle) != p.type || !game::native_data().models.value(c.manager, p.handle, 0, 1)) return {};
    const auto children = c.path(p, {content, items});
    const auto pointer = read<Address>(c.address(children));
    // Native page navigation empties the content array while the next view is
    // being installed: an ordinary transition, not a failed build.
    if (!pointer || !(read<std::uint32_t>(pointer - 4) & 0x7fffffff)) return {};
    return c.field(c.element(children, 0), content);
}
Value make(const Context& c, Schema schema, Address authored_type = 0) {
    auto& s = state();
    const auto id = 0x3155400000000000ULL + ++s.next_id;
    Value value;
    if (authored_type) {
        require((kind(authored_type) == 2 || kind(authored_type) == 4) && size(authored_type) == schema.size &&
                    read<std::uint32_t>(read<Address>(authored_type)) == schema.hash,
                "Throwdown authored model schema differs.");
        value = {game::native_data().models.create(c.manager, authored_type, id, game::native_name_hash("ReSkate.NativeMultiplayerMenu"), false, 2),
                 authored_type};
        c.address(value);
        c.initialize_text(value);
    } else value = c.create(schema, id);
    s.owned.track(value);
    return value;
}
Value make_like(const Context& c, Value like) {
    return make(c, {read<std::uint32_t>(read<Address>(like.type)), static_cast<std::uint16_t>(size(like.type))}, like.type);
}
template<std::size_t I> void activate() noexcept {
    auto& s = state();
    try {
        std::lock_guard lock(s.mutex);
        const auto& action = s.descriptors[I].action;
        if (I >= s.used || action.generation != s.owner.load() || s.pending.size() >= 8) return;
        if (std::any_of(s.pending.begin(), s.pending.end(),
                        [&](const Action& a) { return a.command == action.command && a.argument == action.argument; })) return;
        const auto now = GetTickCount64();
        if (action.command == "mode-open") {
            if (now < s.entry_guard_until) return;
            s.entry_guard_until = now + 350;
        }
        s.pending.push_back(action);
    } catch (...) {}
}
template<std::size_t... I> auto functions(std::index_sequence<I...>) { return std::array{&activate<I>...}; }
Address callback(const Context& c, std::string command, std::string argument = {}) {
    auto& s = state();
    std::lock_guard lock(s.mutex);
    const auto slot = action_slot(s.used, s.descriptors.size(), [&](std::size_t i) -> const Action& { return s.descriptors[i].action; },
                                  command, argument, s.pass);
    require(slot.has_value(), "Throwdown native action capacity reached.");
    static const auto f = functions(std::make_index_sequence<128>{});
    auto& d = s.descriptors[*slot];
    if (*slot == s.used) {
        const auto prototype = c.base + native::action_prototype;
        require(read<Address>(prototype + 0x40) == c.base + native::action_invoker && read<std::uint8_t>(prototype + 0x68) == 0,
                "Throwdown action ABI differs.");
        require(memory::peek_bytes(prototype, d.bytes.data(), d.bytes.size()), "Throwdown action prototype unavailable.");
        const auto invoker = reinterpret_cast<Address>(f[*slot]);
        for (const auto offset : {0x30, 0x40, 0x48}) std::memcpy(d.bytes.data() + offset, &invoker, 8);
        d.info = reinterpret_cast<Address>(d.bytes.data());
        ++s.used;
    }
    d.action = {s.owner.load(), std::move(command), std::move(argument), s.pass};
    return reinterpret_cast<Address>(&d.info);
}
Address asset(const Context& c, const char* name) {
    auto& s = state();
    if (const auto cached = s.assets.find(name); cached != s.assets.end()) return cached->second;
    std::vector<Address> anchors;
    std::set<unsigned> domains;
    const auto add_anchor = [&](Address anchor) {
        // A previous map's raw blueprint is no asset-domain lease. Skip
        // expired anchors so they cannot abort the resident-domain lookup.
        try {
            if (!anchor) return;
            const auto type = read<Address>(anchor + 8);
            if (type != c.base + native::widget_blueprint_type && type != c.base + native::texture_asset_type &&
                type != c.base + native::image_asset_type) return;
            const auto domain = read<std::uint16_t>(anchor + 0x16);
            if (domain < 0xbbf && read<Address>(c.base + engine::domain_owners + domain * 8ULL) && domains.insert(domain).second)
                anchors.push_back(anchor);
        } catch (const std::exception&) {}
    };
    add_anchor(s.anchor_asset);
    for (const auto& root : c.roots({presenter.hash})) {
        const auto blueprint = read<Widget>(root.value).blueprint;
        if (!blueprint || read<Address>(blueprint + 8) != c.base + native::widget_blueprint_type) continue;
        add_anchor(blueprint);
        if (domains.size() >= 32) break;
    }
    const auto find_loaded = [&](const char* path) {
        for (const auto anchor : anchors)
            if (const auto found = named_asset(c, anchor, path)) return found;
        // Shared widgets may belong to a sibling resident UI domain after a
        // custom map switch: only the engine's loaded-domain lookup is used.
        const auto find = game::native_data().find_asset;
        if (find)
            for (unsigned candidate = 0; candidate < 0xbbf; ++candidate) {
                if (!read<Address>(c.base + engine::domain_owners + candidate * 8ULL)) continue;
                if (const auto found = find(static_cast<std::uint16_t>(candidate), path)) return found;
            }
        return Address{};
    };
    if (const auto found = find_loaded(name)) {
        s.assets.emplace(name, found);
        return found;
    }
    // Exported data-model records are not indexed as ordinary assets: read
    // their resource list, then take the exact exported name.
    constexpr std::array containers{
        std::pair{"Activities_DetailsMenu_ContentResource/", "UI/Features/Activities/Resources/Activities_DetailsMenu_ContentResource"},
        std::pair{"Throwdown_DetailsMenu_ContentResource/", "UI/Features/Activities/Resources/Throwdown_DetailsMenu_ContentResource"},
        std::pair{"StackPanelStylesList/", "UI/Foundations/Components/StackVariations/StackPanel/StackPanelStylesList"},
        std::pair{"TD_HostParameters_ContentResource/", "UI/Features/Throwdowns/ContentResources/TD_HostParameters_ContentResource"},
        std::pair{"TD_Page_ContentResources/", "UI/Features/Throwdowns/TD_Page_ContentResources"},
        std::pair{"SettingWrapperStylesList/", "UI/Foundations/Components/Settings/Widgets/SettingWrapperStylesList"},
        std::pair{"TextStylesList/", "UI/Foundations/Styles/Lists/TextStylesList"},
        std::pair{"InteractivityColorStylesList/", "UI/Foundations/Styles/Lists/InteractivityColorStylesList"},
        std::pair{"ButtonStyleList/", "UI/Foundations/Styles/Lists/ButtonStyleList"},
        // The logo grid's records (the built-in pack) and each mode's logo (its mod).
        std::pair{"Throwdown_ModeSelect_ContentResources/", "UI/Features/Throwdowns/ContentResources/Throwdown_ModeSelect_ContentResources"}};
    for (const auto& [prefix, path] : containers)
        if (std::string_view(name).starts_with(prefix)) {
            const Address list = find_loaded(path);
            if (!list || read<Address>(list + 8) != c.base + native::asset_list_type) continue;
            const auto records = read<Address>(list + 0x20);
            const auto count = records ? read<std::uint32_t>(records - 4) & 0x7fffffff : 0;
            require(count <= 2048, "Throwdown native resource list exceeds its limit.");
            for (unsigned i = 0; i < count; ++i) {
                const auto record = read<Address>(records + i * 8ULL);
                if (!record || read<Address>(record + 8) != c.base + native::asset_record_type) continue;
                s.assets.emplace(string(read<Address>(record + 0x28), 256), record);
            }
            if (const auto found = s.assets.find(name); found != s.assets.end()) return found->second;
        }
    // Foundation widgets can live outside the Throwdowns domain: the pause
    // menu's core knows them.
    for (const auto& root : c.roots({core.hash})) {
        try {
            const auto found = blueprints(c, root.model);
            s.assets.insert(found.begin(), found.end());
            if (const auto loaded = s.assets.find(name); loaded != s.assets.end()) return loaded->second;
        } catch (const std::exception&) {}
    }
    throw std::runtime_error(std::string("Throwdown menu asset unavailable: ") + name);
}
Value category(const Context& c, Value tile) {
    const auto w = read<Widget>(c.address(c.path(tile, {widget, widget})));
    const auto model = reference(c, w.data);
    require(model.type && size(model.type) == category_schema.size && read<std::uint32_t>(read<Address>(model.type)) == category_schema.hash,
            "Throwdown category schema differs.");
    return model;
}
Value description(const Context& c, Value cat) { return reference(c, read<Widget>(c.address(c.field(cat, widget))).data); }
Address native_record_type(const Context& c, Address record) {
    // A DataRef may hold a native record or an already materialized handle.
    // Never read an arbitrary asset's fields as record TypeInfo: the model
    // factory assumes a valid one and cannot reject it safely.
    require(record && read<Address>(record + 8) == c.base + native::asset_record_type,
            "Throwdown UI reference is not a native data-model record.");
    const auto type = read<Address>(record + 0x18);
    require(type && read<Address>(record + 0x20), "Throwdown UI record has no type or data.");
    return type;
}
Value record_model(const Context& c, const char* name, Schema schema) {
    const auto record = asset(c, name);
    const auto type = native_record_type(c, record), data = read<Address>(record + 0x20);
    auto result = make(c, schema, type);
    c.copy(result, data);
    return result;
}
Value record_model(const Context& c, const char* name) {
    const auto record = asset(c, name), type = native_record_type(c, record);
    require(kind(type) == 2, "Throwdown record schema differs.");
    return record_model(c, name, {read<std::uint32_t>(read<Address>(type)), static_cast<std::uint16_t>(size(type))});
}
Ref record_ref(const Context& c, const char* name) {
    const auto record = asset(c, name);
    native_record_type(c, record);
    return {record, 0};
}
// A typed native copy keeps its DataRefs; it does not clone the models they
// reference. Copy every mutable UI edge before mounting a private page.
struct PrivateUiGraph {
    const Context& c;
    UiCloneCache<std::pair<Handle, Address>, Value> copies;
    std::set<Handle> private_roots, walked;
    unsigned budget = 2400;
    Value clone(Ref source, unsigned depth = 0) {
        const auto type = source.handle ? c.type_of(source.handle) : (source.record ? native_record_type(c, source.record) : 0);
        require(type && (kind(type) == 2 || kind(type) == 4), "Private Throwdown UI model is unavailable.");
        if (source.handle && (private_roots.contains(source.handle) || walked.contains(source.handle))) return {source.handle, type};
        const auto key = std::pair{source.handle, source.handle ? type : source.record};
        return copies.clone(key, [&] {
            require(depth < 96 && budget, "Private Throwdown UI graph exceeds its limit.");
            --budget;
            const auto data = source.handle ? c.address({source.handle, type}) : read<Address>(source.record + 0x20);
            const auto result = make(c, {read<std::uint32_t>(read<Address>(type)), static_cast<std::uint16_t>(size(type))}, type);
            c.copy(result, data);
            private_roots.insert(result.handle);
            return result;
        }, [&](Value result) { walk(result, depth + 1); });
    }
    void walk(Value value, unsigned depth) {
        if (!walked.insert(value.handle).second) return;
        require(depth < 96, "Private Throwdown UI graph depth differs.");
        const auto hash = read<std::uint32_t>(read<Address>(value.type));
        if (hash == presenter.hash) {
            auto w = read<Widget>(c.address(value));
            if (w.data.handle || w.data.record) { w.data = {0, clone(w.data, depth).handle}; c.set(value, w); }
            return;
        }
        if (hash == 0x62088281) {
            const auto ref = read<Ref>(c.address(value));
            if (ref.handle || ref.record) {
                const auto type = ref.handle ? c.type_of(ref.handle) : native_record_type(c, ref.record);
                // Scalar backend expressions stay borrowed; mutable widgets and
                // lists are private, array-valued models included.
                if (type && (kind(type) == 2 || kind(type) == 4)) c.set(value, Ref{0, clone(ref, depth).handle});
            }
            return;
        }
        if (kind(value.type) == 4) {
            unsigned count{}, stride{};
            auto bytes = c.array(value, 128, count, stride);
            for (unsigned i = 0; i < count; ++i) {
                const auto child = c.element(value, i);
                if (kind(child.type) == 2 || kind(child.type) == 4) {
                    // Publishing an inline element can retire its siblings'
                    // storage: adapt detached roots, then replace the array once.
                    const auto copy = clone({0, child.handle}, depth + 1);
                    require(memory::peek_bytes(c.address(copy), bytes.data() + i * stride, stride), "Private UI array copy is unavailable.");
                }
            }
            c.array(value, bytes, count);
        } else if (kind(value.type) == 2) {
            const auto meta = read<Address>(value.type), fields = read<Address>(meta + 0x60);
            const auto count = read<std::uint16_t>(meta + 0x2a);
            require(count <= 128, "Private Throwdown field count differs.");
            for (unsigned i = 0; i < count; ++i) {
                const auto key = read<std::uint32_t>(fields + i * 24);
                if (borrowed(key)) continue;
                const auto child = c.field(value, key);
                if (key == 0xf2c90867 && kind(child.type) == 4) {
                    unsigned n{}, stride{};
                    const auto bytes = c.array(child, 128, n, stride);
                    require(stride == sizeof(Handle), "Private UI handle array differs.");
                    std::vector<Handle> handles(n);
                    if (n) std::memcpy(handles.data(), bytes.data(), bytes.size());
                    for (auto& handle : handles)
                        if (handle) handle = clone({0, handle}, depth + 1).handle;
                    c.array(child, handles);
                } else if (kind(child.type) == 2 || kind(child.type) == 4) walk(child, depth + 1);
            }
        }
    }
};
// Every model of type `wanted` under `root`, following presenters, refs and lists.
std::vector<Value> ui_models(const Context& c, Value root, std::uint32_t wanted) {
    std::vector<Value> result;
    std::set<Handle> seen;
    unsigned budget = 2400;
    std::function<void(Value, unsigned)> visit = [&](Value value, unsigned depth) {
        if (!value.handle || !value.type || !budget || depth > 96 || !seen.insert(value.handle).second) return;
        --budget;
        const auto hash = read<std::uint32_t>(read<Address>(value.type));
        if (hash == wanted) { result.push_back(value); return; }
        if (hash == 0x62088281) {
            const auto ref = read<Ref>(c.address(value));
            if (ref.handle) visit(reference(c, ref), depth + 1);
            return;
        }
        if (hash == presenter.hash) {
            const auto w = read<Widget>(c.address(value));
            if (w.data.handle) visit(reference(c, w.data), depth + 1);
            return;
        }
        if (kind(value.type) == 4) {
            unsigned count{}, stride{};
            c.array(value, 128, count, stride);
            for (unsigned i = 0; i < count; ++i) visit(c.element(value, i), depth + 1);
        } else if (kind(value.type) == 2) {
            const auto meta = read<Address>(value.type), fields = read<Address>(meta + 0x60);
            const auto count = read<std::uint16_t>(meta + 0x2a);
            require(count <= 128, "Throwdown UI field count differs.");
            for (unsigned i = 0; i < count; ++i) {
                const auto key = read<std::uint32_t>(fields + i * 24);
                if (borrowed(key)) continue;
                const auto child = c.field(value, key);
                if (key == 0xf2c90867 && kind(child.type) == 4) {
                    unsigned n{}, stride{};
                    const auto bytes = c.array(child, 128, n, stride);
                    require(stride == sizeof(Handle), "UI handle list differs.");
                    for (unsigned j = 0; j < n; ++j) {
                        Handle handle{};
                        std::memcpy(&handle, bytes.data() + j * stride, stride);
                        if (handle) visit({handle, c.type_of(handle)}, depth + 1);
                    }
                } else if (kind(child.type) == 2 || kind(child.type) == 4) visit(child, depth + 1);
            }
        }
    };
    visit(root, 0);
    return result;
}
void clear_value(const Context& c, Value value) {
    if (kind(value.type) == 7) {
        if (!read<Address>(c.address(value)) || !c.text(value).empty()) c.text(value, "");
        return;
    }
    // Native array publication reads the count before the pointer, even for
    // an empty array: a null pointer is no empty counted array.
    if (kind(value.type) == 4) { c.array(value, std::span<const std::byte>{}, 0); return; }
    if (kind(value.type) == 2) {
        // Bindings can hold counted arrays and owned delegates: reset from a
        // natively constructed value, never zeroed storage.
        const auto defaults = make_like(c, value);
        c.copy(value, c.address(defaults));
        return;
    }
    const std::vector<std::byte> empty(size(value.type));
    std::vector<std::byte> current(empty.size());
    require(memory::peek_bytes(c.address(value), current.data(), current.size()), "Native reset value is unavailable.");
    if (current != empty) c.publish(value, empty.data());
}
void plain_label(const Context& c, Value value, const std::string& text) {
    const auto label_text = c.field(value, text_field);
    if (!read<Address>(c.address(label_text)) || c.text(label_text, 2048) != text) c.text(label_text, text);
    const auto visible = c.field(value, 0x042924a4);
    if (!read<bool>(c.address(visible))) c.set(visible, true);
}
void label_styles(const Context& c, Value label, const char* idle, const char* focused) {
    c.set_if_changed(c.field(label, 0xf94d8cc6), record_ref(c, idle));
    c.set_if_changed(c.field(label, 0xa1e84eb1), record_ref(c, focused));
    c.set_if_changed(c.field(label, 0xfc23a999), record_ref(c, idle));
    c.set_if_changed(c.field(label, 0x25189231), record_ref(c, focused));
}

// ---- the picker ----------------------------------------------------------------------------------
// The mode's logo: a texture or image asset of its mod, by path (as
// UI/ReSkate/Throwdowns/img_race_placeholder), found among the loaded assets
// whether or not it was drawn yet. A bare name is a category record in the
// game's Throwdown_ModeSelect_ContentResources, as ReSkate_Throwdown's logos
// were: every mod editing that list replaces the others' edits, so a mode's
// own texture is the way. Missing art keeps the donor's.
void mode_art(const Context& c, std::span<const Value> fields, const std::string& art) {
    auto& s = state();
    if (art.empty() || s.missing_art.contains(art)) return;
    try {
        if (art.find('/') != std::string::npos) {
            Address image{};
            try { image = asset(c, art.c_str()); } catch (const std::exception&) {}
            if (image && (read<Address>(image + 8) == c.base + native::texture_asset_type ||
                          read<Address>(image + 8) == c.base + native::image_asset_type)) {
                for (const auto field : fields)
                    if (read<Address>(c.address(field)) != image) c.set(field, image);
                return;
            }
        } else {
            // Texture registration is lazy: the exported category record
            // already holds the loaded image before its first draw.
            const auto name = "Throwdown_ModeSelect_ContentResources/" + art;
            const auto record = asset(c, name.c_str());
            const auto type = read<Address>(record + 0x18), data = read<Address>(record + 0x20);
            require(read<Address>(record + 8) == c.base + native::asset_record_type && type && data &&
                        read<std::uint32_t>(read<Address>(type)) == category_schema.hash,
                    "Throwdown logo record schema differs.");
            const auto image = read<Address>(data + c.member(type, 0x189084da).offset);
            require(image && (read<Address>(image + 8) == c.base + native::texture_asset_type ||
                              read<Address>(image + 8) == c.base + native::image_asset_type),
                    "Throwdown logo record image is not loaded.");
            for (const auto field : fields)
                if (read<Address>(c.address(field)) != image) c.set(field, image);
            return;
        }
        require(publish_texture(c, fields, art), "no loaded texture or image has that name");
    } catch (const std::exception& error) {
        // Not looked for again until the level or the page changes: a missing
        // name searches every loaded asset domain.
        s.missing_art.insert(art);
        logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown mode art {} is unavailable; keeping the stock art: {}",
                     art, error.what());
    }
}
// A card for each registered mode, copied from Spot Battle's.
void mode_cards(const Context& c, const std::vector<custom_throwdowns::Mode>& modes) {
    auto& s = state();
    if (!s.cards.handle || s.originals.size() != 3) return;
    for (const auto& mode : modes) {
        auto [it, fresh] = s.mode_cards.try_emplace(mode.key());
        auto& card = it->second;
        if (fresh) {
            const auto& donor = s.originals.at(1);
            card.tile = make(c, tile_schema, donor.tile.type);
            c.copy(card.tile, c.address(donor.tile));
            card.category = make(c, category_schema, donor.category.type);
            c.copy(card.category, c.address(donor.category));
            card.description = make(c, anchor_schema, donor.description.type);
            c.copy(card.description, c.address(donor.description));
            card.label = make(c, label_schema, s.source_label.type);
            c.copy(card.label, c.address(s.source_label));
            auto desc = read<Widget>(c.address(c.field(card.description, widget)));
            desc.data = {0, card.label.handle};
            c.set(c.field(card.description, widget), desc);
            auto cat = read<Widget>(c.address(c.field(card.category, widget)));
            cat.data = {0, card.description.handle};
            c.set(c.field(card.category, widget), cat);
            auto mounted = read<Widget>(c.address(c.path(card.tile, {widget, widget})));
            mounted.data = {0, card.category.handle};
            c.set(c.path(card.tile, {widget, widget}), mounted);
            logging::log(logging::Level::info, logging::Channel::ui, "Throwdowns: {} card for {}.", mode.name, mode.key());
        }
        plain_label(c, card.label, mode.description);
        plain_label(c, c.field(card.category, 0x77b8ed0b), mode.name);
        // Ask for the callback each pass, so reopening never recycles a live
        // card's delegate.
        c.text(c.field(primary(c, card.tile), 0xb1e14cd6), "");
        c.set(c.field(primary(c, card.tile), 0x9c76b86c), callback(c, "mode-open", mode.key()));
        mode_art(c, std::array{c.field(card.category, 0x189084da), c.field(card.category, 0xc2917efc)}, mode.art);
    }
}
// A grid tile: the logo above its name, in the grid's own widgets.
Value picker_logo(const Context& c, Value tile, Value cat, bool stock, const std::string& name) {
    // Private copies of the button style keep the native focus graph with
    // every fill empty; only the round outline changes on hover and focus.
    const auto button_style = record_model(c, "ButtonStyleList/TileButton.RoughPartial.NoDefaultBG");
    const auto empty = c.path(button_style, {0x2d393a3a, 0xb6c6aace});
    for (const auto key : {0x24996c65U, 0x69cf50caU, 0xe8f882b7U, 0x182004ffU, 0xb0db14beU, 0xfadedb6aU})
        c.copy(c.path(button_style, {0x2d393a3a, key}), c.address(empty));
    try {
        const auto ring = asset(c, "UI/ReSkate/Throwdowns/LogoHoverRing_FillStyle");
        for (const auto key : {0xf622ea66U, 0x943abf2dU, 0x287f4815U}) c.set(c.path(button_style, {0x343abac3, key}), ring);
    } catch (const std::exception& e) {
        static bool reported{};
        if (!std::exchange(reported, true))
            logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown hover ring unavailable: {}", e.what());
    }
    for (const auto flag : {0xc02028cfU, 0xa45fc55dU, 0xb811842aU, 0xe69a9d99U}) c.set(c.field(button_style, flag), false);
    c.set(c.path(tile, {widget, 0x0fb0d794, 0x8cc042ef}), Ref{0, button_style.handle});
    c.set(c.path(tile, {widget, 0xa04998ca}), false);
    const auto colors = record_ref(c, "InteractivityColorStylesList/InteractiveColors.Core.AllWhite");
    c.set(c.field(button_style, 0xcd4a53c0), colors);
    const auto logo = record_model(c, "Throwdown_ModeSelect_ContentResources/LogoTexture", texture_schema);
    if (stock) c.copy(c.field(logo, 0x6e469d2a), c.address(c.field(cat, 0x189084da)));
    c.set(c.field(logo, 0xf524c836), 320.f);
    c.set(c.field(logo, 0x6fbd254e), 320.f);
    for (const auto flag : {0x81c94d8aU, 0x190e039cU, 0xd1997d0cU}) c.set(c.field(logo, flag), false);
    c.set(c.field(logo, 0xcd4a53c0), colors);
    const auto caption = make(c, label_schema, state().source_label.type);
    c.copy(caption, c.address(state().source_label));
    plain_label(c, caption, name);
    label_styles(c, caption, "TextStylesList/H52-XBold_White_DropShadow75", "TextStylesList/H52-XBold_White_DropShadow75");
    const auto outlined = record_model(c, "TextStylesList/H52-XBold_White_DropShadow75");
    c.set(c.field(outlined, 0x51332fc1), asset(c, "UI/Foundations/Fonts/Styles/Dingo_2pxOutline_Aqua_FontEffect"));
    for (const auto key : {0xa1e84eb1U, 0x25189231U}) c.set(c.field(caption, key), Ref{0, outlined.handle});
    c.set(c.field(caption, 0x87ad624c), 2); // centred vertically
    c.set(c.field(caption, 0x3d8639d0), 1);
    c.set(c.field(caption, 0xc13ba637), false);
    c.set(c.field(caption, 0xfc53d427), false);
    c.set(c.field(caption, 0xcd71a279), true);
    c.set(c.field(caption, 0x3fa0b887), false);
    const auto contents = record_model(c, "Throwdown_ModeSelect_ContentResources/LogoContents", {0x9ade4bc7, 208});
    c.set(c.field(contents, 0xa98391bd), Widget{asset(c, "UI/ReSkate/Throwdowns/LogoTexture_Widget"), {0, logo.handle}});
    c.set(c.field(contents, 0x759c54dd), Widget{asset(c, "UI/ReSkate/Throwdowns/LogoName_Widget"), {0, caption.handle}});
    // The private blueprint owns fixed regions (logo 52,16,320,320; name
    // 52,341,360,56): neither moves the other as its font or texture loads.
    // The tile keeps its native button, focus and action.
    c.set(c.path(tile, {widget, widget}), Widget{asset(c, "UI/ReSkate/Throwdowns/LogoCaptionTile_Widget"), {0, contents.handle}});
    c.set(c.field(tile, 0x868043e1), std::array<float, 2>{424.f, 424.f});
    return logo;
}
// The stock throwdowns' places (S.K.A.T.E., Spot Battle, Skate Jam), each the
// first mode that replaces it, or none.
std::array<const custom_throwdowns::Mode*, 3> stock_places(const std::vector<custom_throwdowns::Mode>& modes) {
    std::array<const custom_throwdowns::Mode*, 3> places{};
    constexpr std::array<std::string_view, 3> names{"skate", "spot_battle", "jam"};
    for (const auto& mode : modes)
        for (unsigned i = 0; i < 3; ++i)
            if (!places[i] && mode.replaces == names[i]) places[i] = &mode;
    return places;
}
// SmokeyDawg58's 4-column logo grid in place of the three stock cards.
void logo_picker(const Context& c, const std::vector<custom_throwdowns::Mode>& modes) {
    auto& s = state();
    if (!s.picker_grid.handle) {
        const auto checkpoint = s.owned.checkpoint();
        try {
            s.picker_grid = record_model(c, "Throwdown_ModeSelect_ContentResources/LogoGrid");
            require(read<std::uint32_t>(read<Address>(s.picker_grid.type)) == 1948428891U, "Throwdown native grid schema differs.");
            c.set(c.field(s.picker_grid, 0x55ca89f4), asset(c, "UI/ReSkate/Throwdowns/LogoPickerListItem_Widget"));
            c.set(c.field(s.picker_grid, 1913319895U), 424.f); // ColumnSize
            c.set(c.field(s.picker_grid, 2171233779U), 424.f); // RowSize
            c.set(c.field(s.picker_grid, 2454275348U), 32.f);  // ColumnSpacing
            c.set(c.field(s.picker_grid, 2923226299U), 32.f);  // RowSpacing
            c.set(c.field(s.picker_grid, 3625715088U), 4);     // StaticSegmentItemCount
            c.set(c.field(s.picker_grid, 3831727006U), 0);     // a static segment count
            c.set(c.field(s.picker_grid, 1287003308U), 1);     // rows of four columns
            c.set(c.field(s.picker_grid, 369153166U), 0);      // DefaultIndex
            c.set(c.field(s.picker_grid, 3523782405U), 0);     // Inset
            for (const auto flag : {1972029470U, 133293336U, 3885835458U, 3339695017U, 235921311U}) c.set(c.field(s.picker_grid, flag), false);
            c.set(c.field(s.picker_grid, 1163163164U), true); // clip to the content area
            c.set(c.field(s.picker_grid, 3275567233U), true); // remember the selection on Back
            s.picker_anchor = record_model(c, "Throwdown_ModeSelect_ContentResources/Throwdown_ModeSelect_LinearList_AnchoredContent", anchor_schema);
            c.set(c.field(s.picker_anchor, widget), Widget{asset(c, "UI/Foundations/Components/Lists/GridList/Widget/GridList_Widget"), {0, s.picker_grid.handle}});
            c.set(c.field(s.picker_anchor, 0x1cff7243), std::array<float, 2>{1792.f, 1336.f});
            c.set(c.field(s.picker_anchor, 0x6efc1a61), false);
            c.set(c.field(s.picker_anchor, 0xbe5683d9), false);
            const auto horizontal = c.field(s.picker_anchor, 0xde7d30c7);
            c.set(c.field(horizontal, 0x99bbed5a), .5f);
            c.set(c.field(horizontal, 0x49943bed), .5f);
            c.set(c.field(horizontal, 0x40bfbff4), 0.f);
            c.set(c.field(horizontal, 0x1d233266), 0.f);
            c.set(c.field(horizontal, 0xfec5e0b1), .5f);
            c.set(c.field(horizontal, 0xd01f4a21), 1.f);
            constexpr std::array names{"S.K.A.T.E.", "Spot Battle", "Skate Jam"};
            for (unsigned i = 0; i < 3; ++i) {
                s.picker_stock[i] = make(c, tile_schema, s.originals[i].tile.type);
                c.copy(s.picker_stock[i], c.address(s.originals[i].tile));
                s.picker_stock_logos[i] = picker_logo(c, s.picker_stock[i], s.originals[i].category, true, names[i]);
            }
        } catch (...) {
            s.picker_grid = s.picker_anchor = {};
            s.picker_stock = s.picker_stock_logos = {};
            s.owned.rollback(checkpoint, [&](Value value) { c.destroy(value); });
            throw;
        }
    }
    // ContentPresenterTileListItem consumes the ContentPresenterTileViewModel,
    // not its TileButtonListItemViewModel wrapper: the wrapper adds a field to
    // the flattened table and shifts every cached input scope by one. Keep the
    // owner alive and publish the typed content-tile field for each cell.
    std::vector<Handle> refs;
    const auto append_tile = [&](Value tile) {
        const auto inner = c.field(tile, widget);
        require(read<std::uint32_t>(read<Address>(inner.type)) == content_tile_schema.hash && size(inner.type) == content_tile_schema.size,
                "Throwdown grid tile model differs.");
        refs.push_back(inner.handle);
    };
    const auto stock = stock_places(modes);
    const auto mode_tile = [&](const custom_throwdowns::Mode& mode) {
        auto& card = s.mode_cards.at(mode.key());
        if (!card.logo.handle) card.logo = picker_logo(c, card.tile, card.category, mode.art.empty(), mode.name);
        mode_art(c, std::array{c.field(card.logo, 0x6e469d2a)}, mode.art);
        append_tile(card.tile);
    };
    for (unsigned i = 0; i < 3; ++i)
        if (stock[i]) mode_tile(*stock[i]);
        else append_tile(s.picker_stock[i]);
    for (const auto& mode : modes)
        if (std::none_of(stock.begin(), stock.end(), [&](const auto* m) { return m == &mode; })) mode_tile(mode);
    unsigned count{}, stride{};
    const auto previous = c.array(c.field(s.picker_grid, 0xf2c90867), 64, count, stride);
    require(stride == sizeof(Handle), "Throwdown grid item handle schema differs.");
    if (previous != std::vector<std::byte>(std::as_bytes(std::span(refs)).begin(), std::as_bytes(std::span(refs)).end()))
        c.array(c.field(s.picker_grid, 0xf2c90867), refs);
    // Only the mode selector is replaced: native navigation may reuse this
    // page for another view, or leave its content array empty for a moment.
    const auto slot = mounted_body(c, s.page);
    if (!slot) return;
    const auto mounted = read<Widget>(c.address(*slot));
    if (mounted.data.handle != s.picker_anchor.handle)
        c.set(*slot, Widget{asset(c, "UI/Foundations/Templates/Layouts/Anchored/AnchoredContentPresenter_Widget"), {0, s.picker_anchor.handle}});
}
// Without the logo grid: the modes' cards after the stock three,
// every card a little narrower.
void append_cards(const Context& c, const std::vector<custom_throwdowns::Mode>& modes) {
    auto& s = state();
    std::vector<Ref> rows;
    const auto stock = stock_places(modes);
    for (unsigned i = 0; i < 3; ++i)
        rows.push_back({0, stock[i] ? s.mode_cards.at(stock[i]->key()).tile.handle : s.originals[i].tile.handle});
    for (const auto& mode : modes)
        if (std::none_of(stock.begin(), stock.end(), [&](const auto* m) { return m == &mode; }))
            rows.push_back({0, s.mode_cards.at(mode.key()).tile.handle});
    const bool scale = !modes.empty();
    if (scale != s.cards_scaled) {
        s.cards_scaled = scale;
        const float k = scale ? .74f : 1.f;
        const auto narrow = [&](Value tile, Value cat, Value desc, const Original& o) {
            c.set(c.field(tile, 0x868043e1), std::array<float, 2>{o.tile_size[0] * k, o.tile_size[1]});
            c.set(c.field(desc, 0x1cff7243), std::array<float, 2>{o.description_size[0] * k, o.description_size[1]});
            c.set(c.field(cat, 0x495ffd43), o.icon_width * k);
            c.set(c.field(cat, 0x24fb5ca8), o.icon_height * k);
        };
        for (const auto& o : s.originals) narrow(o.tile, o.category, o.description, o);
    }
    for (const auto& mode : modes) {
        const auto& card = s.mode_cards.at(mode.key());
        const auto& o = s.originals.at(1);
        c.set_if_changed(c.field(card.tile, 0x868043e1), std::array<float, 2>{o.tile_size[0] * .74f, o.tile_size[1]});
        c.set_if_changed(c.field(card.description, 0x1cff7243), std::array<float, 2>{o.description_size[0] * .74f, o.description_size[1]});
        c.set_if_changed(c.field(card.category, 0x495ffd43), o.icon_width * .74f);
        c.set_if_changed(c.field(card.category, 0x24fb5ca8), o.icon_height * .74f);
    }
    unsigned count{}, stride{};
    const auto live = c.array(c.field(s.cards, rows_field), 64, count, stride);
    require(stride == sizeof(Ref), "Throwdown card list schema differs.");
    if (live != std::vector<std::byte>(std::as_bytes(std::span(rows)).begin(), std::as_bytes(std::span(rows)).end()))
        c.array(c.field(s.cards, rows_field), rows);
}
// The stock page's three cards (S.K.A.T.E., Spot Battle, Skate Jam) list.
std::optional<Value> card_list(const Context& c, Value p) {
    try {
        const auto slot = mounted_body(c, p);
        if (!slot) return {};
        auto w = read<Widget>(c.address(*slot));
        // Our grid replaces the presenter, not the stock list: the list stays
        // the generation's identity while the grid is mounted.
        const auto& s = state();
        if (p.handle == s.page.handle && s.picker_anchor.handle && w.data.handle == s.picker_anchor.handle &&
            c.type_of(s.cards.handle) == s.cards.type) return s.cards;
        for (unsigned depth = 0; depth < 4; ++depth) {
            auto value = reference(c, w.data);
            if (!value.type) return {};
            const auto hash = read<std::uint32_t>(read<Address>(value.type));
            if (hash == linear_list.hash && size(value.type) == linear_list.size) {
                unsigned count{}, stride{};
                auto bytes = c.array(c.field(value, rows_field), 64, count, stride);
                if (count < 3 || stride != sizeof(Ref)) return {};
                // The live Skate Jam card navigates through ThrowdownerActive.
                const std::array<std::string_view, 3> expected{"ThrowdownSkate", "SpotBattle", "ThrowdownerActive"};
                for (unsigned i = 0; i < 3; ++i) {
                    Ref ref{};
                    std::memcpy(&ref, bytes.data() + i * stride, stride);
                    auto t = reference(c, ref);
                    if (!t.type || size(t.type) != tile_schema.size || read<std::uint32_t>(read<Address>(t.type)) != tile_schema.hash ||
                        c.text(c.field(primary(c, t), 0xb1e14cd6)) != expected[i]) return {};
                }
                // Our appended cards are ours; any other fourth card is not this page.
                if (count > 3 && (p.handle != s.page.handle || value.handle != s.cards.handle)) return {};
                return value;
            }
            if (hash != anchor_schema.hash || size(value.type) != anchor_schema.size) return {};
            w = read<Widget>(c.address(c.field(value, widget)));
        }
    } catch (...) {}
    return {};
}

// ---- the setup page ------------------------------------------------------------------------------
enum class SetupRole { other, players, timer, rounds };
// Native labels can already be localized when the page is ready.
SetupRole setup_role(std::string_view text) {
    if (text == "ID_SETTINGS_PLAYERS" || text == "Players") return SetupRole::players;
    if (text == "ID_LABEL_TURNTIMER" || text == "ID_LABEL_DURATION" || text == "Turn Timer" || text == "Timer" || text == "Duration")
        return SetupRole::timer;
    if (text == "ID_LABEL_ROUNDS" || text == "Rounds") return SetupRole::rounds;
    return SetupRole::other;
}
std::string setting_caption(const Context& c, Value setting) { return c.text(c.path(setting, {0x58c9d354, text_field})); }
bool setup_body_content_matches(const Context& c, Value live, Value saved) {
    unsigned live_count{}, live_stride{}, saved_count{}, saved_stride{};
    const auto live_items = c.array(c.field(live, items), 128, live_count, live_stride);
    const auto saved_items = c.array(c.field(saved, items), 128, saved_count, saved_stride);
    require(live_stride == stack_item.size && saved_stride == stack_item.size, "Native setup body item schema differs.");
    return matching_presenters<Widget>(live_items, saved_items, stack_item.size, c.member(c.type(stack_item), content).offset);
}
void mount_setup_body(const Context& c, Value live, Value saved) {
    // Array publication makes a new allocation: compare presenter identity,
    // never its address or the native transition and focus fields around it.
    const auto live_array = c.field(live, items), saved_array = c.field(saved, items);
    unsigned live_count{}, live_stride{}, saved_count{}, saved_stride{};
    const auto live_items = c.array(live_array, 128, live_count, live_stride);
    const auto saved_items = c.array(saved_array, 128, saved_count, saved_stride);
    require(live_stride == stack_item.size && saved_stride == stack_item.size, "Native setup body item schema differs.");
    // Same authored slots: change just their presenters, without restarting
    // the stack items' native visibility animation.
    if (const auto next = remount_presenters<Widget>(live_items, saved_items, stack_item.size, c.member(c.type(stack_item), content).offset))
        c.array(live_array, *next, saved_count);
}
void restore_official(const Context& c) {
    auto& s = state();
    std::set<Handle> owned_panels;
    for (const auto& [panel, header] : s.official_headers) {
        if (c.type_of(panel) != c.type(panel_schema) || !game::native_data().models.value(c.manager, panel, 0, 1)) continue;
        if (read<Widget>(c.address(c.field({panel, c.type(panel_schema)}, 0xc0f1b449))).data.handle == header.handle) owned_panels.insert(panel);
    }
    for (const auto& [id, pair] : s.official_originals) {
        // A retired panel can keep a typed handle after its storage is gone:
        // that must not block every later menu update.
        if (c.type_of(id) != pair.first.type || !game::native_data().models.value(c.manager, id, 0, 1)) continue;
        const auto panel = s.official_slot_panels.find(id);
        if (panel == s.official_slot_panels.end()) continue;
        if (read<std::uint32_t>(read<Address>(pair.first.type)) == 0xd71ee0a1) {
            // The body is an inline native stack: restore it only while it
            // still presents our rows, and leave its focus and transition alone.
            const auto private_body = s.official_contents.find(id);
            if (private_body != s.official_contents.end() && setup_body_content_matches(c, pair.first, private_body->second))
                mount_setup_body(c, pair.first, pair.second);
        } else if (owned_panels.contains(panel->second)) c.copy(pair.first, c.address(pair.second));
    }
    s.official_originals.clear();
    s.official_panels.clear();
    s.official_headers.clear();
    s.official_contents.clear();
    s.official_slot_panels.clear();
    s.official_sliders.clear();
    s.official_setup_rows.clear();
    s.official_badges.clear();
    s.slider_rows.clear();
    s.official_mode.clear();
}
void remember_official(const Context& c, Value value) {
    auto& originals = state().official_originals;
    if (originals.contains(value.handle)) return;
    const auto copy = make_like(c, value);
    c.copy(copy, c.address(value));
    originals.emplace(value.handle, std::pair{value, copy});
}
// Drops every setting row with one of these roles from the private copy.
void remove_rows(const Context& c, Value root, std::initializer_list<SetupRole> roles) {
    std::set<Handle> seen;
    std::function<bool(Value, unsigned)> visit = [&](Value value, unsigned depth) -> bool {
        require(depth < 96, "Throwdown settings graph depth differs.");
        if (!value.handle) return false;
        const auto hash = read<std::uint32_t>(read<Address>(value.type));
        if (hash == setting_schema.hash) {
            const auto role = setup_role(setting_caption(c, value));
            return std::find(roles.begin(), roles.end(), role) != roles.end();
        }
        if (!seen.insert(value.handle).second) return false;
        struct Unvisit { std::set<Handle>& seen; Handle handle; ~Unvisit() { seen.erase(handle); } } unvisit{seen, value.handle};
        if (hash == 0x62088281) {
            const auto ref = read<Ref>(c.address(value));
            return ref.handle && visit(reference(c, ref), depth + 1);
        }
        if (hash == presenter.hash) {
            const auto w = read<Widget>(c.address(value));
            return w.data.handle && visit(reference(c, w.data), depth + 1);
        }
        if (kind(value.type) == 4) {
            unsigned n{}, stride{};
            const auto old = c.array(value, 128, n, stride);
            std::vector<std::byte> kept;
            for (unsigned i = 0; i < n; ++i) {
                const auto child = c.element(value, i);
                if (kind(child.type) == 2 || kind(child.type) == 4) {
                    const auto copy = make_like(c, child);
                    c.copy(copy, c.address(child));
                    if (visit(copy, depth + 1)) continue;
                    const auto offset = kept.size();
                    kept.resize(offset + stride);
                    require(memory::peek_bytes(c.address(copy), kept.data() + offset, stride), "Private settings row is unavailable.");
                } else kept.insert(kept.end(), old.begin() + i * stride, old.begin() + (i + 1) * stride);
            }
            if (kept != old) c.array(value, kept, static_cast<unsigned>(kept.size() / stride));
            return false; // removed a row, not the list holding it
        }
        bool remove = false;
        if (kind(value.type) == 2) {
            const auto meta = read<Address>(value.type), fields = read<Address>(meta + 0x60);
            const auto count = read<std::uint16_t>(meta + 0x2a);
            require(count <= 128, "Throwdown settings field count differs.");
            for (unsigned i = 0; i < count; ++i) {
                const auto key = read<std::uint32_t>(fields + i * 24);
                if (borrowed(key)) continue;
                const auto child = c.field(value, key);
                if (key == 0xf2c90867 && kind(child.type) == 4) {
                    unsigned n{}, stride{};
                    const auto old = c.array(child, 128, n, stride);
                    require(stride == sizeof(Handle), "Throwdown setting handle list differs.");
                    std::vector<Handle> kept;
                    for (unsigned j = 0; j < n; ++j) {
                        Handle handle{};
                        std::memcpy(&handle, old.data() + j * stride, stride);
                        if (!handle || !visit({handle, c.type_of(handle)}, depth + 1)) kept.push_back(handle);
                    }
                    if (kept.size() != n) c.array(child, kept);
                } else if (kind(child.type) == 2 || kind(child.type) == 4) remove = visit(child, depth + 1) || remove;
            }
        }
        return remove;
    };
    visit(root, 0);
}
// The host parameters list as the native page mounted it, with its Players
// and Timer rows (the Timer row is the template for the mode's own rows).
SetupRows setup_rows(const Context& c, Value body_value) {
    // HostParameters materializes its settings into the LinearList's OwnedItems
    // tile array; ListItems/ListItemHandles are the alternatives.
    for (const auto list : ui_models(c, body_value, linear_list.hash)) {
        SetupRows result;
        result.list = list;
        const auto owned = c.field(list, 0x345b75b1);
        unsigned owned_count{}, owned_stride{};
        c.array(owned, 128, owned_count, owned_stride);
        require(owned_stride == content_tile_schema.size, "Native setup OwnedItems tile schema differs.");
        bool owned_players{};
        for (unsigned i = 0; i < owned_count; ++i) {
            const auto row = c.element(owned, i);
            for (const auto setting : ui_models(c, row, setting_schema.hash)) {
                const auto role = setup_role(setting_caption(c, setting));
                owned_players = owned_players || role == SetupRole::players;
                if (role == SetupRole::timer) { result.timer_row = row; result.timer_index = i; }
            }
        }
        if (owned_players && result.timer_row.handle) {
            result.retained_rows = make_like(c, owned);
            c.copy(result.retained_rows, c.address(owned));
            result.timer_row = c.element(result.retained_rows, result.timer_index);
            result.owned_tiles = true;
            return result;
        }
        result.timer_row = {};
        unsigned records_count{}, records_stride{}, handles_count{}, handles_stride{};
        const auto records = c.array(c.field(list, rows_field), 128, records_count, records_stride);
        const auto handles = c.array(c.field(list, 0xf2c90867), 128, handles_count, handles_stride);
        require(records_stride == sizeof(Ref) && handles_stride == sizeof(Handle), "Native setup list item types differ.");
        if (records_count && handles_count) continue;
        result.records.resize(records_count);
        result.handles.resize(handles_count);
        if (records_count) std::memcpy(result.records.data(), records.data(), records.size());
        if (handles_count) std::memcpy(result.handles.data(), handles.data(), handles.size());
        bool players{};
        const auto inspect = [&](Value row) {
            for (const auto setting : ui_models(c, row, setting_schema.hash)) {
                const auto role = setup_role(setting_caption(c, setting));
                if (role == SetupRole::players) players = true;
                if (role == SetupRole::timer) result.timer_row = row;
            }
        };
        for (const auto ref : result.records)
            if (ref.handle) inspect(reference(c, ref));
        for (const auto handle : result.handles)
            if (handle) inspect({handle, c.type_of(handle)});
        if (players && result.timer_row.handle) return result;
    }
    // StackPanel.Body is a StackViewModel (0xd71ee0a1): some generations mount
    // each setting as a stack item instead of a nested list.
    if (read<std::uint32_t>(read<Address>(body_value.type)) == 0xd71ee0a1) {
        SetupRows result;
        result.list = body_value;
        result.stack = true;
        const auto items_value = c.field(body_value, items);
        unsigned count{}, stride{};
        c.array(items_value, 128, count, stride);
        require(stride == stack_item.size, "Native HostParameters stack item schema differs.");
        bool players{};
        for (unsigned i = 0; i < count; ++i) {
            const auto row = c.element(items_value, i);
            const auto settings = ui_models(c, row, setting_schema.hash);
            if (settings.size() != 1) continue;
            const auto role = setup_role(setting_caption(c, settings.front()));
            if (role == SetupRole::players) players = true;
            if (role == SetupRole::timer) { result.timer_row = row; result.timer_index = i; }
        }
        if (players && result.timer_row.handle) {
            result.retained_rows = make_like(c, items_value);
            c.copy(result.retained_rows, c.address(items_value));
            result.timer_row = c.element(result.retained_rows, result.timer_index);
            return result;
        }
    }
    throw std::runtime_error("Native HostParameters list with Players and Timer is not mounted yet.");
}
// A private copy of the Timer row, captioned for one of the mode's rows.
Value setup_extra(const Context& c, SetupRows& rows, const std::string& key, const std::string& caption) {
    if (const auto found = rows.extra.find(key); found != rows.extra.end()) return found->second;
    PrivateUiGraph graph{c};
    const auto row = graph.clone({0, rows.timer_row.handle});
    const auto settings = ui_models(c, row, setting_schema.hash);
    require(settings.size() == 1, "Native setup template must contain one SettingWrapper.");
    plain_label(c, c.field(settings.front(), 0x58c9d354), caption);
    if (rows.stack) c.set(c.field(row, 0x1e95752c), static_cast<std::int32_t>(2000 + rows.extra.size()));
    rows.extra.emplace(key, row);
    return row;
}
// A row that is a button: its press is news for the plugin.
void setup_action_row(const Context& c, SetupRows& rows, const std::string& key, const std::string& caption,
                      const std::string& value, const std::string& command, const std::string& argument) {
    const bool fresh = !rows.extra.contains(key);
    const auto checkpoint = state().owned.checkpoint();
    try {
        const auto row = setup_extra(c, rows, key, caption);
        const auto setting = ui_models(c, row, setting_schema.hash).front();
        plain_label(c, c.field(setting, 0x58c9d354), caption);
        auto slot = c.field(setting, content);
        Value control;
        if (fresh) {
            control = make(c, button);
            // The game's setting label's own font styles, with a fresh label
            // button input model: no shared host parameter binding.
            c.copy(c.field(control, 0x58c9d354), c.address(c.field(setting, 0x58c9d354)));
            const auto donor = record_model(c, "Throwdown_DetailsMenu_ContentResource/ThrowdownDetails_HUDButton", button);
            for (const auto field : {0x8cc042efU, 0xf9515817U, 0xf343d160U}) c.copy(c.field(control, field), c.address(c.field(donor, field)));
            clear_value(c, c.field(control, 0x67212c85));
            c.set(c.path(control, {0x0fb0d794, 0x8cc042ef}), record_ref(c, "ButtonStyleList/TileButton.RoughPartial.Default"));
            c.set(slot, Widget{asset(c, "UI/Foundations/Components/Buttons/LabelButton/LabelButton_Widget"), {0, control.handle}});
        } else control = reference(c, read<Widget>(c.address(slot)).data);
        plain_label(c, c.field(control, 0x58c9d354), value);
        const auto set_action = [&](Value model) {
            const auto input_root = c.path(model, {0x0fb0d794, 0xa704272a, 0xc52416ef});
            const auto interactable = c.field(input_root, 0xa704272a);
            c.set(c.field(interactable, 0x4b3146e9), true);
            c.set(c.field(interactable, 0x38f7355f), false);
            clear_value(c, c.path(input_root, {0x66a997b9, 0x66a997b9, 0x4c09193f}));
            const auto action = c.field(interactable, 0xa2b71c93);
            c.set(c.field(action, 0x9c76b86c), callback(c, command, argument));
            c.text(c.field(action, 0xb1e14cd6), "");
            const auto primary_input = asset(c, "Configurations/Input/UI/UI_PrimaryAction");
            c.set(c.field(action, 0x3d00de27), primary_input);
            c.set(c.field(action, 0x0169c2db), primary_input);
        };
        set_action(control);
        // HostParameters items select through a ContentPresenterTile: keep its
        // template and give it this row's action.
        for (const auto tile : ui_models(c, row, content_tile_schema.hash)) {
            set_action(tile);
            c.set(c.path(tile, {0x0fb0d794, 0xa704272a, 0xc52416ef, 0x0abf7c31}), Ref{0, control.handle});
        }
    } catch (...) {
        if (fresh) {
            rows.extra.erase(key);
            state().owned.rollback(checkpoint, [&](Value model) { c.destroy(model); });
        }
        throw;
    }
}
// A row picking one of several options with the game's left/right selector
// (as Allow Join looks). The option the player settled on when it differs
// from `selected`, else -1.
int setup_choice_row(const Context& c, SetupRows& rows, const std::string& key, const std::string& caption,
                     const std::vector<std::string>& options, int selected) {
    const bool fresh = !rows.extra.contains(key);
    const auto checkpoint = state().owned.checkpoint();
    try {
        const auto row = setup_extra(c, rows, key, caption);
        const auto setting = ui_models(c, row, setting_schema.hash).front();
        plain_label(c, c.field(setting, 0x58c9d354), caption);
        auto& choice = rows.choices[key];
        if (fresh) {
            const auto control = record_model(c, "TD_HostParameters_ContentResource/TD_Timer_BasicSelector", selector_schema);
            // The authored selector's two-way bindings belong to Skate Jam: own
            // the options and only read this selector's choice.
            for (const auto field : {0x4256fd65U, 0x85b8c3e6U, 0xe88c0f54U}) clear_value(c, c.field(control, field));
            c.set(c.field(control, 0xeb92bf1c), Ref{});
            for (const auto field : {0x41b10c27U, 0xd637e1f4U}) c.set(c.field(control, field), Address{});
            const auto list = c.field(control, 0x760fc12b);
            c.set(c.field(list, 0x340cfb95), Address{});
            choice.options.clear();
            for (const auto& option : options) {
                auto item = make(c, selector_item_schema), text = make(c, label_schema);
                c.copy(text, c.address(c.field(setting, 0x58c9d354)));
                label_styles(c, text, "TextStylesList/H64-XBold_White", "TextStylesList/H64-XBold_Black");
                plain_label(c, text, option);
                c.text(c.field(item, 0x58c9d354), option);
                c.set(c.field(item, 0x34c8bcc2), Ref{0, text.handle});
                choice.options.push_back(item.handle);
            }
            c.array(c.field(list, rows_field), std::vector<Ref>{});
            c.array(c.field(list, 0xf2c90867), choice.options);
            c.set(c.field(control, 0x369babfe), true);
            c.set(c.field(setting, content), Widget{asset(c, "UI/Foundations/Components/Settings/Widgets/BasicSelector_Widget"), {0, control.handle}});
            choice.selector = control;
            choice.shown = -1;
        }
        const auto list = c.field(choice.selector, 0x760fc12b);
        // The choice is its current item's handle (0x783c4b49); the index
        // field (0x55511280) does not follow left and right.
        const auto current = read<Handle>(c.address(c.field(list, 0x783c4b49)));
        const auto found = std::find(choice.options.begin(), choice.options.end(), current);
        const int index = found == choice.options.end() ? -1 : static_cast<int>(found - choice.options.begin());
        if (choice.shown < 0 || (selected != choice.shown && index == choice.shown)) {
            const auto at = std::clamp(selected, 0, static_cast<int>(choice.options.size()) - 1);
            c.set(c.field(list, 0x55511280), at);
            c.set(c.field(list, 0x783c4b49), choice.options[static_cast<std::size_t>(at)]);
            choice.shown = at;
            return -1;
        }
        // A choice counts once it has stood a quarter second with the page
        // open. The game puts the selector back to its first item as the page
        // closes on Confirm, so the first item counts only after 2 s.
        const auto now = GetTickCount64();
        if (index < 0 || static_cast<std::size_t>(index) >= choice.options.size() || index == choice.shown) { choice.pending = -1; return -1; }
        if (index != choice.pending) { choice.pending = index; choice.pending_at = now; return -1; }
        if (now - choice.pending_at < (index == 0 ? 2000u : 250u) || custom_throwdowns::setup_mode().empty()) return -1;
        choice.shown = index;
        choice.pending = -1;
        return index != selected ? index : -1;
    } catch (...) {
        if (fresh) {
            rows.extra.erase(key);
            rows.choices.erase(key);
            state().owned.rollback(checkpoint, [&](Value model) { c.destroy(model); });
        }
        throw;
    }
}
// The mode's own rows after Players (and Timer, when it has one), in the list's own representation.
void setup_capabilities(const Context& c, Value panel, Value body_value, const custom_throwdowns::Mode& mode) {
    auto& s = state();
    auto [found, fresh] = s.official_setup_rows.try_emplace(panel.handle);
    if (fresh) {
        const auto checkpoint = s.owned.checkpoint();
        try {
            found->second = setup_rows(c, body_value);
        } catch (...) {
            s.official_setup_rows.erase(found);
            s.owned.rollback(checkpoint, [&](Value model) { c.destroy(model); });
            throw;
        }
    }
    auto& rows = found->second;
    const auto values = custom_throwdowns::settings(mode.key());
    const auto value_of = [&](const custom_throwdowns::Row& row) {
        const auto v = values.find(row.id);
        return v == values.end() ? row.value : v->second;
    };
    std::vector<Value> extra;
    for (const auto& row : mode.rows) {
        const auto key = "row-" + row.id;
        if (row.kind == custom_throwdowns::Row::Kind::slider) {
            const auto made = setup_extra(c, rows, key, row.caption);
            for (const auto setting : ui_models(c, made, setting_schema.hash)) {
                plain_label(c, c.field(setting, 0x58c9d354), row.caption);
                s.slider_rows[c.field(setting, content).handle] = row.id;
            }
            extra.push_back(made);
        } else if (row.kind == custom_throwdowns::Row::Kind::choice) {
            const auto picked = setup_choice_row(c, rows, key, row.caption, row.options, value_of(row));
            if (picked >= 0) custom_throwdowns::set_setting(mode.key(), row.id, picked);
            extra.push_back(rows.extra.at(key));
        } else {
            setup_action_row(c, rows, key, row.caption, row.text.empty() ? row.caption : row.text, "mode-button", mode.key() + "|" + row.id);
            extra.push_back(rows.extra.at(key));
        }
    }
    // Keep the native list's layout, scroll and focus state and item
    // template: publish its own row representation, minus the Timer row for a
    // mode without one.
    const bool keep_timer = mode.timer.has_value();
    if (rows.owned_tiles || rows.stack) {
        const auto stride = rows.owned_tiles ? content_tile_schema.size : stack_item.size;
        unsigned saved_count{}, saved_stride{};
        const auto saved = c.array(rows.retained_rows, 128, saved_count, saved_stride);
        require(saved_stride == stride, "Retained native setup row schema differs.");
        std::vector<std::byte> bytes;
        for (unsigned i = 0; i < saved_count; ++i)
            if (keep_timer || i != rows.timer_index) bytes.insert(bytes.end(), saved.begin() + i * stride, saved.begin() + (i + 1) * stride);
        for (const auto row : extra) {
            const auto at = bytes.size();
            bytes.resize(at + stride);
            require(memory::peek_bytes(c.address(row), bytes.data() + at, stride), "Private native setup row is unavailable.");
        }
        const auto target = rows.owned_tiles ? c.field(rows.list, 0x345b75b1) : c.field(rows.list, items);
        unsigned count{}, live_stride{};
        const auto live = c.array(target, 128, count, live_stride);
        require(live_stride == stride, "Private native setup row schema differs.");
        const auto offset = rows.owned_tiles ? c.member(c.type(content_tile_schema), widget).offset : c.member(c.type(stack_item), content).offset;
        // The native list updates focus and interaction inside these rows:
        // remount only their presenters.
        if (const auto next = remount_presenters<Widget>(live, bytes, stride, offset))
            c.array(target, *next, static_cast<unsigned>(bytes.size() / stride));
    } else if (!rows.records.empty()) {
        std::vector<Ref> records;
        for (const auto ref : rows.records)
            if (keep_timer || ref.handle != rows.timer_row.handle) records.push_back(ref);
        for (const auto row : extra) records.push_back({0, row.handle});
        unsigned count{}, stride{};
        const auto live = c.array(c.field(rows.list, rows_field), 128, count, stride);
        if (live != std::vector<std::byte>(std::as_bytes(std::span(records)).begin(), std::as_bytes(std::span(records)).end()))
            c.array(c.field(rows.list, rows_field), records);
    } else {
        std::vector<Handle> handles;
        for (const auto handle : rows.handles)
            if (keep_timer || handle != rows.timer_row.handle) handles.push_back(handle);
        for (const auto row : extra) handles.push_back(row.handle);
        unsigned count{}, stride{};
        const auto live = c.array(c.field(rows.list, 0xf2c90867), 128, count, stride);
        if (live != std::vector<std::byte>(std::as_bytes(std::span(handles)).begin(), std::as_bytes(std::span(handles)).end()))
            c.array(c.field(rows.list, 0xf2c90867), handles);
    }
}
// The game's real Spot Battle setup, opened for a plugin mode: a private copy
// of its header, body and footer, relabelled with the mode's name, logo,
// description and rows. Its stack, transitions, Confirm and the native flag
// placement after it stay the game's own.
void official_setup(const Context& c) {
    auto& s = state();
    const auto key = custom_throwdowns::setup_mode();
    const auto mode = key.empty() ? std::nullopt : custom_throwdowns::find(key);
    if (!mode) { restore_official(c); return; }
    if (s.official_mode != key) {
        restore_official(c);
        s.official_mode = key;
    }
    const auto values = custom_throwdowns::settings(key);
    for (const auto& root : c.roots({panel_schema.hash})) {
        try {
            const auto header_slot = c.field(root.model, 0xc0f1b449);
            auto header = reference(c, read<Widget>(c.address(header_slot)).data);
            if (!header.handle || read<std::uint32_t>(read<Address>(header.type)) != header_schema.hash) continue;
            auto title = c.field(header, 0x00f3b15e);
            const auto name = c.text(c.field(title, text_field));
            const bool fresh = !s.official_panels.contains(root.model.handle);
            if (fresh && name != "Spot Battle" && name != "SPOT BATTLE" && name != "ID_ACTIVITY_SPOTBATTLE_TITLE") continue;
            if (fresh) {
                // The header and footer mount before the native body: never
                // keep that empty body and then cover its populated rows.
                bool has_players{}, has_timer{};
                for (const auto setting : ui_models(c, c.field(root.model, 0x43967ece), setting_schema.hash)) {
                    const auto role = setup_role(setting_caption(c, setting));
                    has_players = has_players || role == SetupRole::players;
                    has_timer = has_timer || role == SetupRole::timer;
                }
                if (!has_players || !has_timer) continue;
                // Finish the private graph before any live presenter changes: a
                // missing native dependency leaves the stock page as it was.
                PrivateUiGraph graph{c};
                std::vector<std::pair<Value, Value>> copies;
                for (const auto slot_key : {0xc0f1b449U, 0x43967eceU, 0xbfc64535U}) {
                    const auto slot = c.field(root.model, slot_key);
                    const auto copy = graph.clone(slot_key == 0x43967eceU ? Ref{0, slot.handle} : read<Widget>(c.address(slot)).data);
                    if (slot_key == 0x43967eceU) remove_rows(c, copy, {SetupRole::rounds});
                    copies.emplace_back(slot, copy);
                    if (slot_key == 0xc0f1b449U) header = copy;
                }
                for (const auto& [slot, copy] : copies) remember_official(c, slot);
                try {
                    for (const auto& [slot, copy] : copies) {
                        if (slot.type == copy.type) mount_setup_body(c, slot, copy);
                        else {
                            auto current = read<Widget>(c.address(slot));
                            current.data = {0, copy.handle};
                            c.set(slot, current);
                        }
                        s.official_contents.emplace(slot.handle, copy);
                        s.official_slot_panels.emplace(slot.handle, root.model.handle);
                    }
                    s.official_headers.emplace(root.model.handle, header);
                    s.official_panels.insert(root.model.handle);
                } catch (...) {
                    for (const auto& [slot, copy] : copies) {
                        try {
                            const auto saved = s.official_originals.at(slot.handle).second;
                            if (slot.type == copy.type) mount_setup_body(c, slot, saved);
                            else c.copy(slot, c.address(saved));
                        } catch (...) {}
                        s.official_contents.erase(slot.handle);
                        s.official_slot_panels.erase(slot.handle);
                        s.official_originals.erase(slot.handle);
                    }
                    s.official_headers.erase(root.model.handle);
                    s.official_panels.erase(root.model.handle);
                    throw;
                }
                title = c.field(header, 0x00f3b15e);
                logging::log(logging::Level::info, logging::Channel::ui, "Throwdowns: the native setup {:#x} is {}'s.", root.model.handle, mode->name);
            } else if (const auto saved = s.official_headers.find(root.model.handle);
                       saved != s.official_headers.end() && header.handle != saved->second.handle) {
                auto mounted = read<Widget>(c.address(header_slot));
                mounted.data = {0, saved->second.handle};
                c.set(header_slot, mounted);
                header = saved->second;
                title = c.field(header, 0x00f3b15e);
            }
            // Reentering the page can republish Spot Battle's presenters: keep
            // ours, with the live stack's animation and focus.
            const auto body_slot = c.field(root.model, 0x43967ece);
            const auto private_body = s.official_contents.at(body_slot.handle);
            mount_setup_body(c, body_slot, private_body);
            {
                const auto slot = c.field(root.model, 0xbfc64535);
                const auto copy = s.official_contents.at(slot.handle);
                auto current = read<Widget>(c.address(slot));
                if (current.data.handle != copy.handle) { current.data = {0, copy.handle}; c.set(slot, current); }
            }
            plain_label(c, title, mode->name);
            // Optional art never blocks the title or the native controls. The
            // logo is a texture; ActivityDisplay's framed icon wants an image,
            // so it goes in the native Texture widget.
            if (!mode->art.empty()) try {
                    const auto blueprint = asset(c, "UI/Foundations/Components/Media/Icons/Texture_Widget");
                    auto& badge = s.official_badges[root.model.handle];
                    if (!badge.handle) {
                        badge = make(c, texture_schema);
                        c.set(c.field(badge, 0xf524c836), 288.f);
                        c.set(c.field(badge, 0x6fbd254e), 288.f);
                        for (const auto flag : {0x81c94d8aU, 0xd1997d0cU, 0x190e039cU}) c.set(c.field(badge, flag), false);
                    }
                    mode_art(c, std::array{c.field(badge, 0x6e469d2a)}, mode->art);
                    const auto icon = c.field(header, content);
                    if (read<Widget>(c.address(icon)).data.handle != badge.handle) c.set(icon, Widget{blueprint, {0, badge.handle}});
                } catch (const std::exception& e) {
                    if (fresh) logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown setup badge: {}", e.what());
                }
            setup_capabilities(c, root.model, private_body, *mode);
            mount_setup_body(c, body_slot, private_body);
            const auto settings = ui_models(c, c.field(root.model, 0x43967ece), setting_schema.hash);
            for (const auto setting : settings) {
                const auto label = c.field(setting, 0x58c9d354);
                const auto presentation = c.field(setting, content);
                auto row_id = [&]() -> std::string {
                    if (const auto known = s.slider_rows.find(presentation.handle); known != s.slider_rows.end()) return known->second;
                    const auto role = setup_role(c.text(c.field(label, text_field)));
                    return role == SetupRole::players ? "players" : role == SetupRole::timer ? "timer" : "";
                }();
                if (row_id.empty()) continue;
                s.slider_rows[presentation.handle] = row_id;
                int minimum{}, maximum{}, step{1}, fallback{};
                std::string caption;
                if (row_id == "players") {
                    minimum = mode->min_players; maximum = mode->max_players; fallback = mode->players; caption = "Players";
                } else if (row_id == "timer") {
                    if (!mode->timer) continue;
                    minimum = mode->timer->minimum; maximum = mode->timer->maximum; step = mode->timer->step;
                    fallback = mode->timer->value; caption = mode->timer->caption;
                } else {
                    const auto row = std::find_if(mode->rows.begin(), mode->rows.end(), [&](const auto& r) { return r.id == row_id; });
                    if (row == mode->rows.end() || row->kind != custom_throwdowns::Row::Kind::slider) continue;
                    minimum = row->minimum; maximum = row->maximum; step = row->step; fallback = row->value; caption = row->caption;
                }
                plain_label(c, label, caption);
                auto slider = reference(c, read<Widget>(c.address(presentation)).data);
                if (!slider.handle || read<std::uint32_t>(read<Address>(slider.type)) != slider_hash) continue;
                const bool first = !s.official_sliders.contains(presentation.handle);
                if (first) {
                    // The native widget still handles input, focus and drawing;
                    // the shared Spot Battle setting never sees the mode's values.
                    clear_value(c, c.field(slider, 2243478502U));
                    clear_value(c, c.field(slider, 655725074U));
                    s.official_sliders.emplace(presentation.handle, slider);
                } else {
                    const auto saved = s.official_sliders.at(presentation.handle);
                    if (slider.handle != saved.handle) {
                        auto mounted = read<Widget>(c.address(presentation));
                        mounted.data = {0, saved.handle};
                        c.set(presentation, mounted);
                    }
                    slider = saved;
                }
                const auto range = c.field(slider, 0x170bebf7), value = c.field(slider, 0x5a547e87), increment = c.field(slider, 0xb47e6cec);
                c.set_if_changed(range, std::array<float, 2>{static_cast<float>(minimum), static_cast<float>(maximum)});
                c.set_if_changed(increment, static_cast<float>(step));
                c.set_if_changed(c.field(slider, 264787447U), true);  // TrimWholeValues
                c.set_if_changed(c.field(slider, 3516482810U), true); // RoundToNearestWhole
                const auto stored = values.find(row_id);
                if (first) c.set(value, native_slider_position(stored == values.end() ? fallback : stored->second, minimum, maximum));
                const auto normalized = read<float>(c.address(value));
                const auto selected = native_slider_choice(normalized, minimum, maximum, step);
                const auto snapped = native_slider_position(selected, minimum, maximum);
                if (!std::isfinite(normalized) || std::abs(normalized - snapped) > .00001f) c.set(value, snapped);
                custom_throwdowns::set_setting(key, row_id, selected);
            }
            for (const auto notice : ui_models(c, c.field(root.model, 0x43967ece), notice_schema.hash))
                plain_label(c, c.field(notice, 0x94703efc), mode->description);
            // Keep the native Confirm and its location initializer: placement
            // takes over once it completes. Advanced Parameters is Back.
            for (const auto b : ui_models(c, c.field(root.model, 0xbfc64535), button.hash)) {
                const auto action = c.path(b, {0x0fb0d794, 0xa704272a, 0xc52416ef, 0xa704272a, 0xa2b71c93});
                if (c.text(c.field(action, 0xb1e14cd6)) != "AdvancedParameters") continue;
                c.text(c.field(action, 0xb1e14cd6), "ThrowdownerExit");
                plain_label(c, c.field(b, 0x58c9d354), "BACK");
            }
        } catch (const std::exception& e) {
            static std::uint64_t report{};
            const auto now = GetTickCount64();
            if (now >= report) {
                report = now + 10000;
                logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown setup {:#x}: {}", root.model.handle, e.what());
            }
        }
    }
}

// ---- generations ---------------------------------------------------------------------------------
void initialize(const Context& c, Value p, Value list) {
    auto& s = state();
    s.page = p;
    s.cards = list;
    s.owner.store(p.handle);
    unsigned count{}, stride{};
    auto bytes = c.array(c.field(list, rows_field), 3, count, stride);
    std::vector<Ref> refs(count);
    std::memcpy(refs.data(), bytes.data(), bytes.size());
    for (const auto& ref : refs) {
        auto tile = reference(c, ref), cat = category(c, tile), desc = description(c, cat);
        s.originals.push_back({tile, cat, desc, read<std::array<float, 2>>(c.address(c.field(tile, 0x868043e1))),
                               read<std::array<float, 2>>(c.address(c.field(desc, 0x1cff7243))),
                               read<float>(c.address(c.field(cat, 0x495ffd43))), read<float>(c.address(c.field(cat, 0x24fb5ca8)))});
    }
    const auto& donor = s.originals[1];
    s.anchor_asset = read<Widget>(c.address(c.path(donor.tile, {widget, widget}))).blueprint;
    const auto donor_label = reference(c, read<Widget>(c.address(c.field(donor.description, widget))).data);
    require(donor_label.type && size(donor_label.type) == label_schema.size, "Throwdown description label unavailable.");
    s.source_label = make(c, label_schema, donor_label.type);
    c.copy(s.source_label, c.address(donor_label));
    s.original_body = make(c, presenter, mounted_body(c, p)->type);
    c.copy(s.original_body, c.address(*mounted_body(c, p)));
    s.generations.emplace(p.handle, list.handle);
    logging::write(logging::Level::info, logging::Channel::ui, "Throwdowns: plugin modes registered on the native page.");
}
void restore_page(const Context& c) {
    auto& s = state();
    if (const auto slot = mounted_body(c, s.page); slot && s.original_body.handle) {
        const auto mounted = read<Widget>(c.address(*slot)).data.handle;
        if (mounted == s.picker_anchor.handle && mounted) c.copy(*slot, c.address(s.original_body));
    }
    if (s.cards.handle && c.type_of(s.cards.handle) == s.cards.type) {
        unsigned count{}, stride{};
        const auto bytes = c.array(c.field(s.cards, rows_field), 64, count, stride);
        require(stride == sizeof(Ref), "Throwdown card list changed during cleanup.");
        std::vector<Ref> retained;
        for (unsigned i = 0; i < count; ++i) {
            Ref ref{};
            std::memcpy(&ref, bytes.data() + i * stride, stride);
            if (std::none_of(s.mode_cards.begin(), s.mode_cards.end(), [&](const auto& entry) { return entry.second.tile.handle == ref.handle; }))
                retained.push_back(ref);
        }
        if (retained.size() != count) c.array(c.field(s.cards, rows_field), retained);
    }
    for (const auto& o : s.originals) {
        if (c.type_of(o.tile.handle) == o.tile.type) c.set_if_changed(c.field(o.tile, 0x868043e1), o.tile_size);
        if (c.type_of(o.category.handle) == o.category.type) {
            c.set_if_changed(c.field(o.category, 0x495ffd43), o.icon_width);
            c.set_if_changed(c.field(o.category, 0x24fb5ca8), o.icon_height);
        }
        if (c.type_of(o.description.handle) == o.description.type) c.set_if_changed(c.field(o.description, 0x1cff7243), o.description_size);
    }
}
void forget_generation() {
    auto& s = state();
    s.page = s.cards = s.source_label = s.original_body = {};
    s.picker_grid = s.picker_anchor = {};
    s.picker_stock = s.picker_stock_logos = {};
    s.mode_cards.clear();
    s.originals.clear();
    s.grid_failed = s.cards_scaled = false;
    s.anchor_asset = 0;
    s.missing_art.clear();
}
// A picker generation is not the models' lifetime: native cached pages can
// still reference them. Put the stock page back and keep every model until the
// level or the model manager goes (release).
void retire(const Context& c) {
    auto& s = state();
    s.owner.store(0);
    try {
        if (s.page.handle && c.type_of(s.page.handle) == s.page.type) restore_page(c);
    } catch (const std::exception& e) {
        logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown picker retirement: {}", e.what());
    }
    forget_generation();
}
void release(const Context& c) {
    auto& s = state();
    s.owner.store(0);
    { std::lock_guard lock(s.mutex); s.pending.clear(); }
    s.owned.release([&] {
        restore_official(c);
        restore_page(c);
    }, [&](Value value) { c.destroy(value); });
    forget_generation();
    restore_official(c);
    s.assets.clear();
}
void forget_manager() {
    auto& s = state();
    s.owned.manager_replaced();
    forget_generation();
    s.official_originals.clear();
    s.official_panels.clear();
    s.official_headers.clear();
    s.official_contents.clear();
    s.official_slot_panels.clear();
    s.official_sliders.clear();
    s.official_setup_rows.clear();
    s.official_badges.clear();
    s.slider_rows.clear();
    s.official_mode.clear();
    s.assets.clear();
    s.generations.clear();
    s.owner.store(0);
}
void run_actions(const Context& c) {
    auto& s = state();
    std::vector<Action> pending;
    {
        std::lock_guard lock(s.mutex);
        pending = std::exchange(s.pending, {});
    }
    for (const auto& a : pending) {
        if (a.generation != s.owner.load()) continue;
        if (a.command == "mode-open") {
            // The card's own navigation is cleared: the placement tick sends
            // Spot Battle's setup once any earlier flag of ours is gone.
            custom_throwdowns::retire_unconfirmed();
            if (!custom_throwdowns::open_setup(a.argument))
                logging::log(logging::Level::info, logging::Channel::ui, "Throwdowns: {} cannot open while another mode's flag is up.", a.argument);
        } else if (a.command == "mode-button") {
            const auto bar = a.argument.find('|');
            if (bar == std::string::npos || custom_throwdowns::setup_mode() != a.argument.substr(0, bar)) continue;
            custom_throwdowns::News news;
            news.action = "button";
            news.row = a.argument.substr(bar + 1);
            custom_throwdowns::post(a.argument.substr(0, bar), std::move(news));
        }
    }
    (void)c;
}
} // namespace

bool release_native_throwdown_menu(std::uintptr_t base) noexcept {
    auto& s = state();
    if (s.owned.empty()) { forget_generation(); s.generations.clear(); s.owner.store(0); return true; }
    try {
        const auto ui = read<Address>(base + engine::ui_manager);
        const auto manager = ui ? read<Address>(ui + 0x140) : 0;
        require(manager != 0, "Throwdown menu manager unavailable during cleanup.");
        if (manager != s.manager) { forget_manager(); return true; }
        const Context c(base, manager);
        game::ModelWriteLock lock(manager);
        release(c);
        s.generations.clear();
        return true;
    } catch (const std::exception& e) {
        logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown menu cleanup: {}", e.what());
        return false;
    }
}

void tick_native_throwdown_menu(std::uintptr_t base, bool loading) noexcept {
    auto& s = state();
    if (loading || !base || !game::native_data().models.create) return;
    const auto now = GetTickCount64();
    if (now < s.next_tick && !custom_throwdowns::placement_active()) return;
    s.next_tick = now + 200;
    try {
        // The logo grid is the Throwdowns page's menu whether or not a plugin
        // has a mode: every pass looks for a fresh page generation.
        const auto modes = custom_throwdowns::modes();
        const auto ui = read<Address>(base + engine::ui_manager);
        const auto manager = ui ? read<Address>(ui + 0x140) : 0;
        if (!manager) return;
        const Context c(base, manager);
        game::ModelWriteLock lock(manager);
        if (s.manager && s.manager != manager) forget_manager();
        s.manager = manager;
        s.base = base;
        custom_throwdowns::tick(c);
        run_actions(c);
        ++s.pass;
        if (s.page.handle && c.type_of(s.page.handle) != s.page.type) retire(c);
        // The game makes new page and list generations when Throwdowns
        // reopens: find the fresh one rather than staying on a retired page.
        for (const auto& p : c.roots({page.hash}))
            if (const auto list = card_list(c, p.model)) {
                if (s.page.handle && (p.model.handle != s.page.handle || list->handle != s.cards.handle) &&
                    !s.generations.contains({p.model.handle, list->handle})) retire(c);
                if (!s.page.handle && !s.generations.contains({p.model.handle, list->handle})) {
                    initialize(c, p.model, *list);
                    break;
                }
            }
        if (s.page.handle && mounted_body(c, s.page)) {
            mode_cards(c, modes);
            bool grid = false;
            if (!s.grid_failed) try {
                    logo_picker(c, modes);
                    grid = true;
                } catch (const std::exception& e) {
                    s.grid_failed = true;
                    logging::log(logging::Level::warning, logging::Channel::ui,
                                 "Throwdowns: the logo grid is unavailable ({}); the modes' cards follow the stock ones.", e.what());
                }
            if (!grid) append_cards(c, modes);
        }
        official_setup(c);
    } catch (const std::exception& e) {
        logging::log(logging::Level::warning, logging::Channel::ui, "Throwdown menu: {}", e.what());
        s.next_tick = now + 5000;
        release_native_throwdown_menu(base);
    }
}
} // namespace dingosdk::multiplayer
