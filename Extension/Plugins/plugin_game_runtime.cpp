#include "plugin_game_runtime.h"
#include "plugin_session_feed.h"
#include "Runtime/runtime_internal.h"
#include "Engine/Game/Input/controller_bindings.h"
#include "Engine/Game/Multiplayer/session_tools.h"
#include "Engine/Game/World/world_layer_catalog.h"
#include "Engine/Game/World/world_layer_types.h"
#include "Extension/Multiplayer/Hud/game_ui_state.h"
#include "Extension/Multiplayer/Session/session.h"
#include "Extension/Music/external_player.h"
#include "Extension/Objects/ParkEditor/park_editor_runtime.h"
#include "Extension/Profile/local_profile_runtime.h"
#include "Extension/Settings/named_settings.h"
#include "Extension/Skater/ai_skaters.h"
#include "Extension/Skater/no_bail.h"
#include "Extension/Throwdowns/native_throwdowns.h"
#include "Extension/Throwdowns/throwdown_debug_text.h"
#include "Extension/Trainer/trainer.h"
#include "Extension/Travel/local_location_travel.h"
#include "Extension/UI/Overlay/overlay.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <format>
#include <map>
#include <set>

namespace dingosdk::plugins {
namespace {
// A request on the queue the console's own debug commands use; its outcome shows in the console's
// model a tick later, so "" here means it was queued.
std::string queue(overlay::DebugAction action, bool enabled = false, float value = 0) {
    std::array<char, 512> reply{};
    const overlay::DebugRequest request{action, enabled, value};
    if (runtime::detail::queue_debug(nullptr, request, reply.data(), reply.size())) return {};
    reply.back() = '\0';
    return reply[0] ? std::string(reply.data()) : std::string("the game is busy; try again");
}

class RealGame final : public Game {
public:
    explicit RealGame(std::uintptr_t base) : base_(base) {}

    // ---- the local skater ----------------------------------------------------------------------
    std::optional<std::uint32_t> physics_state() override {
        const auto t = trainer::telemetry();
        if (!t.skater) return std::nullopt;
        return t.physics_state;
    }
    std::uint64_t wipeouts() override { return watched_physics_state().wipeouts; }
    std::string control(std::string_view action) override {
        using overlay::DebugAction;
        if (action == "noclip_on" || action == "noclip_off") {
            const bool on = action == "noclip_on";
            if (on && !session_noclip_allowed()) return "the host has turned noclip off";
            return queue(DebugAction::set_noclip, on);
        }
        if (!session_boosts_allowed()) return "the host has turned boosts off";
        if (action == "boost_forward") return queue(DebugAction::add_forward_velocity, true);
        if (action == "boost_up") return queue(DebugAction::add_up_velocity, true);
        if (action == "boost_offboard") return queue(DebugAction::add_offboard_up_velocity, true);
        return "there is no such control";
    }
    // The game's whole HUD, as `hideui` hides it, while any plugin asks; as it was when none does.
    void hide_hud(std::string_view plugin, bool hidden) override {
        const bool before = !hud_hiders_.empty();
        if (hidden) hud_hiders_.insert(std::string(plugin));
        else hud_hiders_.erase(std::string(plugin));
        const bool now = !hud_hiders_.empty();
        if (now == before) return;
        if (now) hud_was_hidden_ = base_ && multiplayer::sample_game_ui_state(base_).ui_hidden;
        if (!hud_was_hidden_) queue(overlay::DebugAction::set_game_ui_hidden, now);
    }

    // ---- the world -------------------------------------------------------------------------------
    std::vector<CatalogItem> object_catalog() override {
        std::vector<CatalogItem> result;
        const auto model = local_park_editor();
        if (model.assets)
            for (const auto& asset : *model.assets) result.push_back({asset.key, asset.title, asset.category});
        return result;
    }
    std::vector<GameObject> objects() override {
        std::vector<GameObject> result;
        for (const auto& o : local_park_editor().objects) result.push_back({o.id, o.item, o.position, o.rotation, o.scale});
        return result;
    }
    // Through the park editor's own edits (park_editor_runtime.h), its limits and the session's
    // object rules included. The editor need not be open.
    std::string edit_object(std::string_view operation, const GameObject& object) override {
        const auto model = local_park_editor();
        if (!model.available) return "objects cannot be placed here now";
        if (model.busy) return "the last edit is still being applied";
        profile::PlacedObject placed;
        placed.id = object.id;
        placed.item = object.item;
        placed.position = object.position;
        placed.rotation = object.rotation;
        placed.scale = object.scale;
        const auto status = edit_local_park(operation, model.map, model.generation, model.revision, {}, placed);
        return status.starts_with("error: ") ? status.substr(7) : std::string{};
    }
    std::string set_environment(std::string_view kind, std::string_view key, std::string_view value) override {
        if (kind == "tod") return time_of_day(key);
        if (kind == "control") {
            float number{};
            try {
                number = std::stof(std::string(value));
            } catch (...) {
                return "not a number";
            }
            return set_local_world_control(key, number) ? std::string{} : "the game refused it (no map loaded, or not that control)";
        }
        if (kind == "park") {
            if (set_local_park(key, value)) return {};
            return "the host chooses the parks, or that is not a lot and layout";
        }
        return "there is no such setting";
    }
    std::vector<Destination> destinations() override {
        std::vector<Destination> result;
        for (const auto& [id, d] : profile_runtime::location_travel_runtime().policy.destinations) result.push_back({id, d.name, d.map});
        return result;
    }
    std::vector<LevelInfo> levels() override {
        std::vector<LevelInfo> result;
        auto& r = runtime::detail::runtime();
        std::lock_guard lock(r.mutex);
        for (const auto& level : r.model.levels) result.push_back({level.asset, level.display_name, level.can_load});
        return result;
    }
    std::string travel(std::string_view kind, std::string_view target) override {
        if (multiplayer_controls_level(multiplayer::model())) return "only the session's host changes where everyone is";
        if (kind == "location") {
            const auto& destinations = profile_runtime::location_travel_runtime().policy.destinations;
            const auto found = destinations.find(target);
            if (found == destinations.end()) return "there is no destination " + std::string(target);
            return runtime::detail::queue_location_travel(found->second.map.c_str()) ? std::string{} : "it cannot travel now";
        }
        std::array<char, 512> reply{};
        const std::string asset(target);
        if (runtime::detail::queue_load(nullptr, asset.c_str(), "", "", "", reply.data(), reply.size())) return {};
        reply.back() = '\0';
        return reply[0] ? std::string(reply.data()) : std::string("that level cannot be loaded now");
    }
    std::string ai(std::string_view operation, int count) override {
        if (operation == "clear") {
            ai_skaters::request_clear();
            return {};
        }
        if (multiplayer_session_active()) return "AI skaters are left out of sessions: the other players would never see them";
        if (static_cast<int>(ai_skaters::active_count()) + count > 16) return "at most 16 AI skaters";
        ai_skaters::request_spawn(static_cast<unsigned>(count), true, true);
        return {};
    }
    int ai_count() override { return static_cast<int>(ai_skaters::active_count()); }

    // ---- the session ------------------------------------------------------------------------------
    std::vector<PeerSkater> peer_skaters() override {
        std::vector<PeerSkater> result;
        for (const auto& p : plugin_feed::latest_peers()) {
            const auto& q = p.rotation;
            // Where the skater faces: the rotation's +Z about world up, as the local skater's heading.
            const float heading = std::atan2(2 * (q[0] * q[2] + q[3] * q[1]), 1 - 2 * (q[0] * q[0] + q[1] * q[1]));
            result.push_back({p.id, p.position, heading, p.age_ms});
        }
        return result;
    }
    void set_nametag(std::string_view plugin, std::uint64_t player, std::string text, std::uint32_t colour) override {
        plugin_feed::set_tag(std::string(plugin), player, std::move(text), colour);
    }
    std::string voice_volume(std::uint64_t player, float volume) override {
        const auto id = std::to_string(player);
        const auto mute = multiplayer::command("voice-mute", id + (volume <= 0 ? " on" : " off"));
        if (volume <= 0) return reply(mute);
        return reply(multiplayer::command("voice-volume", id + " " + std::format("{:.2f}", volume)));
    }
    std::vector<ChatLine> chat_since(std::uint64_t sequence) override {
        std::vector<ChatLine> result;
        const auto chat = multiplayer::chat();
        if (chat.latest <= sequence) return result;
        for (const auto& line : chat.lines)
            if (line.sequence > sequence) result.push_back({line.sequence, line.sender, line.name, line.text, line.local, line.server});
        return result;
    }
    std::string send_chat(std::string_view text) override {
        if (!multiplayer_session_active()) return "not in a session";
        const auto answer = multiplayer::command("chat", text);
        return answer == "Message sent." ? std::string{} : answer;
    }
    ThrowdownState throwdowns() override {
        ThrowdownState state{multiplayer::local_throwdown_active(), multiplayer::local_throwdown_host(), {}};
        for (const auto& p : multiplayer::skate_hud().players) state.skate.push_back({p.name, p.letters, p.up, p.out});
        return state;
    }

    // ---- the rest -------------------------------------------------------------------------------------
    Track track() override {
        if (!music_started_) {
            music_started_ = true;
            external_player::start();
        }
        const auto now = external_player::now();
        return {now.available, now.playing, now.app, now.title, now.artist, now.album, now.revision};
    }
    std::string music(std::string_view command) override {
        using external_player::Command;
        if (!music_started_) {
            music_started_ = true;
            external_player::start();
        }
        if (command == "play") external_player::send(Command::play);
        else if (command == "pause") external_player::send(Command::pause);
        else if (command == "next") external_player::send(Command::next);
        else if (command == "previous") external_player::send(Command::previous);
        else return "there is no such command";
        return {};
    }
    Progress progress() override {
        const auto model = local_profile_progression();
        Progress p;
        p.available = model.available;
        for (const auto& d : model.districts) p.districts.push_back({d.id, d.name, static_cast<int>(d.rank)});
        for (const auto& c : model.challenges)
            p.challenges.push_back({c.id, c.type, c.neighborhood, static_cast<int>(c.attempts), static_cast<int>(c.completed_goals.size()),
                                    static_cast<int>(c.goal_count)});
        p.score = static_cast<double>(model.score);
        p.score_cap = static_cast<double>(model.score_cap);
        p.score_level = model.score_level;
        return p;
    }
    std::optional<std::string> setting(std::string_view name) override { return named_setting_value(name); }
    std::vector<std::string> setting_names() override { return named_setting_names(); }

    // ---- input -------------------------------------------------------------------------------------------
    std::string bind(std::string_view plugin, std::string_view action, std::uint32_t combo) override {
        if (!valid_action_binding(combo)) return "that is not a key or pad combo";
        binds_[{std::string(plugin), std::string(action)}].combo = combo;
        return {};
    }
    // Each bind's press, once, while the game itself has the input (no menu, console or text box).
    std::vector<std::pair<std::string, std::string>> take_presses() override {
        std::vector<std::pair<std::string, std::string>> presses;
        if (binds_.empty()) return presses;
        ControllerInput input;
        DingoSDKOverlayReadControllerInput(&input, false);
        for (auto& [key, bind] : binds_)
            if (bind.latch.update(bind.combo, input, false)) presses.push_back(key);
        return presses;
    }
    void release(std::string_view plugin) override {
        hide_hud(plugin, false);
        plugin_feed::release_tags(std::string(plugin));
        std::erase_if(binds_, [&](const auto& item) { return item.first.first == plugin; });
    }

private:
    static std::string reply(const std::string& answer) {
        return answer.starts_with("error") || answer.find("not") != std::string::npos ? answer : std::string{};
    }
    // As the console's `tod`: each map has seven time layers, "<map>_tod_<n>_<name>"; one is
    // forced on and the rest off, or all seven go back to the level's own choice.
    std::string time_of_day(std::string_view wanted) {
        static constexpr std::array<std::string_view, 8> times{"default", "morning", "noon", "afternoon", "evening", "night",
                                                               "weatherday", "weathernight"};
        const auto found = std::find(times.begin(), times.end(), wanted);
        if (found == times.end()) return "a time of day is default, morning, noon, afternoon, evening, night, weatherday or weathernight";
        const auto slot = static_cast<std::size_t>(found - times.begin());
        const auto world = local_profile_world_layers();
        if (world.controlled_by_host) return "the host chooses the time of day";
        if (world.map == WorldMap::none) return "no map is loaded";
        std::vector<std::size_t> layers;
        for (unsigned n = 1; n < times.size(); ++n) {
            const auto prefix = std::string(world_map_key(world.map)) + "_tod_" + std::to_string(n) + "_";
            const auto layer = std::find_if(world_layers().begin(), world_layers().end(),
                                            [&](const auto& item) { return item.key.starts_with(prefix); });
            if (layer == world_layers().end()) return "this map has no time of day";
            layers.push_back(static_cast<std::size_t>(layer - world_layers().begin()));
        }
        // The others switch off before the new one comes on, so two never load together.
        bool saved = true;
        for (std::size_t i = 0; i < layers.size(); ++i)
            if (i + 1 != slot) saved = saved && set_local_world_layer(world_layers()[layers[i]].key, slot ? "off" : "default");
        if (slot) saved = saved && set_local_world_layer(world_layers()[layers[slot - 1]].key, "on");
        return saved ? std::string{} : local_profile_world_layers().feedback;
    }

    std::uintptr_t base_;
    std::set<std::string, std::less<>> hud_hiders_;
    bool hud_was_hidden_{};
    bool music_started_{};
    struct Bind {
        std::uint32_t combo{};
        ControllerComboLatch latch;
    };
    std::map<std::pair<std::string, std::string>, Bind> binds_;
};
}

Game& real_game(std::uintptr_t image_base) {
    static auto* game = new RealGame(image_base); // the process's lifetime, as the host's
    return *game;
}
}
