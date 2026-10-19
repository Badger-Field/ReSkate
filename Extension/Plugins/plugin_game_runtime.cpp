#include "plugin_game_runtime.h"
#include "plugin_session_feed.h"
#include "Runtime/runtime_internal.h"
#include "plugin_ui.h"
#include "Engine/Game/World/park_rotation.h"
#include "Engine/Core/Json/json.h"
#include "Engine/Game/Input/controller_bindings.h"
#include "Engine/Game/Multiplayer/session_tools.h"
#include "Engine/Game/World/world_layer_catalog.h"
#include "Engine/Game/World/world_layer_types.h"
#include "Extension/Multiplayer/Hud/game_ui_state.h"
#include "Extension/Multiplayer/Hud/native_party.h"
#include "Extension/Skater/client_source_spawn.h"
#include "Extension/Skater/skater_animation.h"
#include "Extension/Plugins/plugin_joystick.h"
#include "plugin_sound.h"
#include "Extension/Skater/skater_events.h"
#include "Extension/Skater/outfit_overlay.h"
#include "Extension/Skater/board_pose.h"
#include "Extension/Skater/game_speed.h"
#include "Extension/Skater/grind_exit.h"
#include "Extension/Skater/xray.h"
#include "Extension/UI/hud_corner.h"
#include "plugin_camera.h"
#include "Extension/Multiplayer/Session/session.h"
#include "Extension/Music/external_player.h"
#include "Extension/Music/spotify_station.h"
#include "Extension/Objects/ParkEditor/park_editor_runtime.h"
#include "Extension/Profile/local_profile_runtime.h"
#include "Extension/Settings/named_settings.h"
#include "Extension/Skater/ai_skaters.h"
#include "Extension/Skater/no_bail.h"
#include "Extension/Throwdowns/native_throwdowns.h"
#include "Extension/Throwdowns/throwdown_relay.h"
#include "Extension/Throwdowns/throwdown_debug_text.h"
#include "Extension/Trainer/trainer.h"
#include "Extension/Travel/local_location_travel.h"
#include "Extension/UI/Overlay/overlay.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <format>
#include <fstream>
#include <iterator>
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
            const auto lot = std::find_if(park_lots.begin(), park_lots.end(), [&](const auto& l) { return l.key == key; });
            if (lot == park_lots.end() || !valid_park(static_cast<unsigned>(lot - park_lots.begin()), value))
                return "that is not a lot and one of its layouts";
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
            if (line.sequence > sequence && line.tag != "Plugin") result.push_back({line.sequence, line.sender, line.name, line.text, line.local, line.server});
        return result;
    }
    std::string send_chat(std::string_view text) override {
        if (!multiplayer_session_active()) return "not in a session";
        const auto answer = multiplayer::command("chat", text);
        return answer == "Message sent." ? std::string{} : answer;
    }
    std::string local_chat(std::string_view plugin, std::string_view text, std::uint32_t colour) override {
        return multiplayer::plugin_chat(plugin, text, colour);
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
    std::optional<Track> game_track() override {
        const auto song = spotify_station::game_song();
        if (!song) return std::nullopt;
        return Track{true, true, "skate.", song->title, song->artist, {}, 0};
    }
    std::string game_music(std::string_view command) override {
        if (command != "next") return "there is no such command";
        return spotify_station::game_next() ? std::string{} : "the game's music cannot skip now (a Spotify station is on, or no song has started)";
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
        binds_[{std::string(plugin), std::string(action)}].own = combo;
        publish_binds();
        return {};
    }
    // Each bind's press, once, while the game itself has the input (no menu, console or text box).
    // The player's own combo from the Binds page wins over the plugin's.
    std::vector<std::pair<std::string, std::string>> take_presses() override {
        apply_rebinds();
        std::vector<std::pair<std::string, std::string>> presses;
        if (binds_.empty()) return presses;
        ControllerInput input;
        DingoSDKOverlayReadControllerInput(&input, false);
        for (auto& [key, bind] : binds_)
            if (bind.latch.update(combo_in_use(key, bind), input, false)) presses.push_back(key);
        return presses;
    }
    std::string host_throwdown(std::string_view plugin, std::string_view series, std::vector<std::uint64_t> players, int most) override {
        if (!multiplayer_session_active()) return "not in a session";
        if (!thrower_.empty() && thrower_ != plugin) {
            const auto status = multiplayer::throwdown_relay_host_status();
            if (status == "asked" || status == "spawning" || status == "open") return thrower_ + " is hosting a drop";
        }
        auto why = multiplayer::throwdown_relay_host(std::string(series), std::move(players), static_cast<std::uint32_t>(std::max(most, 0)));
        if (why.empty()) thrower_ = plugin;
        return why;
    }
    void cancel_throwdown(std::string_view plugin) override {
        if (thrower_ != plugin) return;
        multiplayer::throwdown_relay_cancel_host(); // its status says "cancelled" from the relay's next tick
    }
    std::string throwdown_status(std::string_view plugin) override {
        return thrower_ == plugin ? multiplayer::throwdown_relay_host_status() : std::string{};
    }
    // ---- board -----------------------------------------------------------------------------------------
    std::optional<std::array<BoardBone, 16>> board_bones() override {
        const auto drawn = board_pose::read();
        if (!drawn) return std::nullopt;
        std::array<BoardBone, 16> out{};
        for (std::size_t i = 0; i < out.size(); ++i) out[i] = {(*drawn)[i].position, (*drawn)[i].rotation, (*drawn)[i].scale};
        return out;
    }
    std::string board_set(std::string_view plugin, int bone, const BoardBone& value, bool world) override {
        return board_pose::set(plugin, bone, {value.position, value.rotation, value.scale}, world ? board_pose::Space::world : board_pose::Space::local);
    }
    std::string board_hide(std::string_view plugin, int bone) override { return board_pose::hide(plugin, bone); }
    void board_clear(std::string_view plugin, int bone) override { board_pose::clear(plugin, bone); }
    void board_park(std::string_view plugin, std::vector<int> bones) override { board_pose::park(plugin, std::move(bones)); }
    std::string board_toss(std::string_view plugin, std::vector<int> bones, const Vec3& velocity, const Vec3& spin, float ground) override {
        return board_pose::toss(plugin, std::move(bones), velocity, spin, ground);
    }
    // ---- outfit --------------------------------------------------------------------------------------
    std::string outfit_set(std::string_view plugin, std::string_view slot, std::string_view item, std::vector<float> placement, bool covers) override {
        return outfit_overlay::set(std::string(plugin), std::string(slot), std::string(item), std::move(placement), covers);
    }
    void outfit_clear(std::string_view plugin, std::string_view slot) override { outfit_overlay::clear(plugin, slot); }
    std::string outfit_find(std::string_view ending) override { return outfit_overlay::find_item(ending); }
    std::vector<OutfitSlot> outfit_slots() override {
        std::vector<OutfitSlot> out;
        for (auto& s : outfit_overlay::slots()) out.push_back({std::move(s.name), std::move(s.item), std::move(s.parameters)});
        return out;
    }
    std::string outfit_status() override { return outfit_overlay::status(); }
    // ---- the skater's physics ------------------------------------------------------------------------
    SkaterNews take_skater_news() override {
        skater_events::want();
        SkaterNews news;
        for (const auto& l : skater_events::take_landings()) news.landings.push_back({l.down_speed, l.speed, l.rail});
        for (const auto& h : skater_events::take_hits())
            news.hits.push_back({skater_body::names[std::min<std::size_t>(h.bone, skater_body::count - 1)], h.impact, h.vehicle, h.board});
        for (const auto& f : skater_events::take_falls()) {
            SkaterNews::Fall fall{f.seconds, f.left, f.right, {}};
            for (std::size_t i = 1; i < skater_body::count; ++i)
                if (f.impact[i] > 0 || f.slide[i] > 0) fall.bones.push_back({skater_body::names[i], f.impact[i], f.slide[i]});
            news.falls.push_back(std::move(fall));
        }
        return news;
    }
    std::vector<PhysicsStep> take_physics_steps() override {
        skater_events::want_steps();
        std::vector<PhysicsStep> out;
        for (const auto& news : skater_events::take_steps()) {
            const auto& s = news.step;
            PhysicsStep step{news.at, s.seconds, s.wipeout, s.airborne, s.ragdoll, std::nullopt, s.spin, {}};
            if (s.velocity) step.velocity = Vec3{(*s.velocity)[0], (*s.velocity)[1], (*s.velocity)[2]};
            for (std::size_t i = 1; i < skater_body::count; ++i) {
                const auto& c = s.body.bodies[i];
                if (!c.touching) continue;
                const float slide = std::sqrt(c.slide[0] * c.slide[0] + c.slide[1] * c.slide[1] + c.slide[2] * c.slide[2]);
                step.bodies.push_back({skater_body::names[i], c.impact, std::isfinite(slide) ? slide : 0.0f, c.hit.board, c.hit.vehicle,
                                      c.hit.world || c.hit.kind_5 || c.hit.kind_11});
            }
            out.push_back(std::move(step));
        }
        return out;
    }
    std::string xray(std::string_view plugin, bool on) override {
        if (!xray::available()) return "the x-ray is unavailable for this game build";
        xray::prepare(plugin, on);
        return {};
    }
    std::string hud_corner(std::string_view plugin, bool hidden) override {
        hud_corner::ask(plugin, hidden);
        return {};
    }
    std::string game_speed(std::string_view plugin, float speed) override {
        if (speed < 1.0f && multiplayer_session_active()) return "never in a multiplayer session";
        game_speed::ask(plugin, speed);
        return {};
    }
    float game_speed_now() override { return game_speed::current(); }
    std::string grind_exit(std::string_view plugin, std::optional<GrindExitWish> wish) override {
        std::optional<grind_exit::Wish> w;
        if (wish) {
            using Side = grind_exit::Wish::Side;
            w = grind_exit::Wish{wish->side == "toe" ? Side::toe : wish->side == "heel" ? Side::heel : wish->side == "right" ? Side::right : Side::left,
                                 wish->stance, wish->strength};
        }
        return grind_exit::wish(plugin, w);
    }
    std::vector<GrindPop> take_grind_pops(std::string_view plugin) override {
        std::vector<GrindPop> out;
        for (auto& p : grind_exit::take_pops(plugin)) out.push_back({p.applied, std::move(p.reason), std::move(p.basis), p.toward, p.before, p.after});
        return out;
    }
    std::string hide_pad(std::string_view plugin, bool lt, bool rt, bool left_x) override {
        if (!grind_exit::available()) return "hiding pad controls is unavailable for this game build";
        grind_exit::hide(plugin, {lt, rt, left_x});
        return {};
    }
    std::optional<Vec3> board_velocity() override {
        skater_events::want();
        return skater_events::board_velocity();
    }
    std::optional<SlamNews> last_slam() override {
        const auto slam = skater_events::last_slam();
        if (!slam.at) return std::nullopt;
        return SlamNews{slam.force, slam.cause, static_cast<double>(GetTickCount64() - slam.at)};
    }
    std::string wipeout(float force) override {
        if (multiplayer::local_throwdown_active()) return "not while a throwdown runs";
        no_bail_request_wipeout(std::clamp(force, 0.5f, 3.0f));
        return {};
    }
    // ---- audio ---------------------------------------------------------------------------------------
    std::uint64_t play_sound(std::string_view plugin, SoundStart sound) override {
        plugin_sound::Voice voice;
        voice.owner = plugin;
        voice.wave = std::move(sound.wave);
        voice.volume = sound.volume;
        voice.pitch = sound.pitch;
        voice.range = sound.range;
        voice.throttle = sound.throttle;
        voice.speed = sound.speed;
        voice.loop = sound.loop;
        voice.position = sound.position;
        return plugin_sound::start(std::move(voice));
    }
    bool change_sound(std::string_view plugin, std::uint64_t id, const SoundChange& change) override {
        auto* voice = plugin_sound::find(id, plugin);
        if (!voice) return false;
        if (change.volume) voice->volume = *change.volume;
        if (change.pitch) voice->pitch = *change.pitch;
        if (change.range) voice->range = *change.range;
        if (change.throttle) voice->throttle = *change.throttle;
        if (change.speed) voice->speed = *change.speed;
        if (change.position) voice->position = *change.position;
        return true;
    }
    void stop_sound(std::string_view plugin, std::uint64_t id) override { plugin_sound::stop(id, plugin); }
    bool sound_playing(std::string_view plugin, std::uint64_t id) override { return plugin_sound::find(id, plugin) != nullptr; }
    // ---- input -------------------------------------------------------------------------------------
    bool camera_mine(std::string_view plugin) const { return !camera_holder_.empty() && camera_holder_ == plugin && camera_shown_; }
    std::optional<FlightInput> flight_input(std::string_view plugin) override {
        if (!camera_mine(plugin)) return std::nullopt;
        // Read once a tick: the mouse's movement is used up as it is read.
        const auto now = GetTickCount64();
        if (now - flight_read_at_ > 4) {
            flight_read_at_ = now;
            overlay::FlightInput in;
            DingoSDKOverlayReadFlightInput(&in, true, false);
            flight_ = in.active ? std::optional<FlightInput>(FlightInput{in.right, in.up, in.forward, in.look_x, in.look_y, in.boost})
                                : std::nullopt;
        }
        return flight_;
    }
    std::optional<PadInput> gamepad() override {
        if (!joystick::game_in_foreground()) return std::nullopt;
        overlay::Gamepad pad;
        {
            joystick::InputAccess access; // seen even while captured from the game
            pad = overlay::read_gamepad();
        }
        if (!pad.valid) return std::nullopt;
        return PadInput{pad.left_x, pad.left_y, pad.right_x, pad.right_y, pad.left_trigger, pad.right_trigger, pad.buttons, pad.playstation};
    }
    std::optional<StickInput> joystick() override {
        if (!joystick::game_in_foreground()) return std::nullopt;
        const auto sample = joystick::poll();
        if (!sample.valid) return std::nullopt;
        return StickInput{sample.axes, sample.buttons, sample.name};
    }
    std::string capture_input(std::string_view plugin, bool on) override {
        if (!on) {
            if (capturer_ == plugin) capturer_.clear();
            return {};
        }
        if (!camera_mine(plugin)) return "only while the plugin's camera is shown";
        capturer_ = plugin;
        capture_until_ = GetTickCount64() + 250;
        return {};
    }
    bool capturing() const noexcept {
        return !capturer_.empty() && camera_mine(capturer_) && GetTickCount64() < capture_until_;
    }
    // ---- skater.animation ---------------------------------------------------------------------------
    std::string play_clip(std::string_view plugin, ClipRef clip, float speed, float blend, bool loop) override {
        if (multiplayer::local_throwdown_active()) return "not while a throwdown runs";
        if (!player_.empty() && player_ != plugin && skater_animation::playing()) return player_ + " is playing a clip";
        player_ = plugin;
        skater_animation::play(skater_animation::Play{std::move(clip), speed, blend, loop});
        return {};
    }
    void stop_clip(std::string_view plugin) override {
        if (player_ != plugin) return;
        skater_animation::play(std::nullopt);
        player_.clear();
    }
    bool clip_playing(std::string_view plugin) override { return player_ == plugin && skater_animation::playing(); }
    std::string replace_trick(std::string_view plugin, std::string trick, std::string pop, ClipRef clip, float speed) override {
        const auto key = std::make_pair(trick, pop);
        if (const auto found = replacements_.find(key); found != replacements_.end() && found->second.first != plugin)
            return found->second.first + " replaces that trick already";
        if (!clip) replacements_.erase(key);
        else {
            const auto mine = std::count_if(replacements_.begin(), replacements_.end(), [&](const auto& r) { return r.second.first == plugin; });
            if (!replacements_.contains(key) && mine >= 32) return "a plugin replaces at most 32 tricks";
            replacements_[key] = {std::string(plugin), skater_animation::Replacement{std::move(trick), std::move(pop), std::move(clip), speed}};
        }
        publish_replacements();
        return {};
    }
    std::string record_clip(std::string_view plugin, float seconds) override {
        if (!recorder_.empty() && recorder_ != plugin && skater_animation::recording()) return recorder_ + " is recording";
        if (!skater_animation::record(seconds)) return "a recording is going on";
        recorder_ = plugin;
        return {};
    }
    ClipRef take_recording(std::string_view plugin) override {
        if (recorder_ != plugin || skater_animation::recording()) return nullptr;
        auto clip = skater_animation::take_recording();
        if (clip) recorder_.clear();
        return clip;
    }
    std::string current_trick() override { return skater_animation::current_trick(); }
    void tick_animation(std::uintptr_t base, std::uintptr_t client) {
        xray::tick();
        hud_corner::tick();
        game_speed::tick();
        grind_exit::tick(client);
        outfit_overlay::tick();
        board_pose::tick(base, local_skater_component(base, client));
        const bool allowed = !multiplayer::local_throwdown_active();
        skater_animation::tick(base, local_skater_component(base, client), allowed);
    }
    // ---- the lobby host's authority ------------------------------------------------------------------
    std::string kick(std::uint64_t player, std::string_view why) override { return multiplayer::host_kick(player, why); }
    // The host's own chat, under the plugin's name: a lobby has no server to speak for it.
    std::string announce(std::string_view plugin, std::string_view text) override {
        if (!multiplayer_session_active()) return "not in a session";
        const auto answer = multiplayer::command("chat", "[" + std::string(plugin) + "] " + std::string(text));
        return answer == "Message sent." ? std::string{} : answer;
    }
    std::string move_player(std::uint64_t player, const Vec3& at) override { return multiplayer::host_teleport(player, at); }
    std::string tell(std::string_view, std::uint64_t, std::string_view) override {
        return "a lobby has no private chat: send that player's copy a message (reskate.net.send) and show it there";
    }
    // ---- camera ------------------------------------------------------------------------------------
    std::optional<CameraView> camera_view() override {
        const auto view = plugin_camera::camera_now();
        if (!view || !(view->vertical_fov > 1 && view->vertical_fov < 175)) return std::nullopt;
        const auto& m = view->world;
        return CameraView{{m[12], m[13], m[14]}, {-m[8], -m[9], -m[10]}, view->vertical_fov};
    }
    std::string set_camera(std::string_view plugin, const std::array<float, 16>& pose, float fov) override {
        if (!camera_holder_.empty() && camera_holder_ != plugin) return camera_holder_ + " has the camera";
        if (!follower_.empty() && follower_ != plugin) return follower_ + " is following a player with it";
        camera_holder_ = plugin;
        camera_pose_ = pose;
        camera_fov_ = fov;
        camera_releasing_ = false;
        return {};
    }
    std::string follow_player(std::string_view plugin, std::uint64_t player) override {
        if (!camera_holder_.empty() && camera_holder_ != plugin) return camera_holder_ + " has the camera";
        if (!player) {
            if (follower_ == plugin) {
                multiplayer::spectate_party_member(0);
                follower_.clear();
            }
            return {};
        }
        if (!multiplayer_session_active()) return "not in a session";
        if (camera_holder_ == plugin) release_camera(plugin);
        follower_ = plugin;
        multiplayer::spectate_party_member(player);
        return {};
    }
    void release_camera(std::string_view plugin) override {
        if (follower_ == plugin) follow_player(plugin, 0);
        if (camera_holder_ != plugin) return;
        camera_holder_.clear();
        camera_releasing_ = true;
        camera_status_.clear();
    }
    std::string camera_status(std::string_view plugin) override {
        if (follower_ == plugin) return multiplayer::party_spectating() ? std::string{} : "Spectate is starting";
        if (camera_holder_ != plugin) return "it does not hold the camera";
        return camera_shown_ ? std::string{} : camera_status_.empty() ? "waiting for the game's camera" : camera_status_;
    }
    void apply_camera(std::uintptr_t base, std::uintptr_t client, bool ready, bool phase) {
        if (multiplayer::party_spectating()) {
            // Spectate has the lease now, and gives the game's camera back itself.
            camera_shown_ = camera_releasing_ = false;
            if (!camera_holder_.empty()) camera_status_ = "Spectate has the camera";
            return;
        }
        std::string detail;
        if (!camera_holder_.empty()) {
            camera_shown_ = update_party_camera(base, client, ready, phase, &camera_pose_, detail, camera_fov_);
            if (!camera_shown_ && !detail.empty()) camera_status_ = detail;
        } else if (camera_releasing_ && update_party_camera(base, client, ready, phase, nullptr, detail)) {
            camera_releasing_ = false;
        }
    }
    std::string freeze(std::string_view plugin, bool on) override {
        const bool before = !freezers_.empty();
        if (on) {
            if (!before && !session_noclip_allowed()) return "the host has turned noclip off";
            freezers_.insert(std::string(plugin));
        } else freezers_.erase(std::string(plugin));
        const bool now = !freezers_.empty();
        if (now == before) return {};
        const auto why = queue(overlay::DebugAction::set_noclip, now);
        if (!why.empty() && now) freezers_.erase(std::string(plugin));
        return why;
    }
    bool frozen() const noexcept { return !freezers_.empty(); }
    void release(std::string_view plugin) override {
        stop_clip(plugin);
        plugin_sound::stop_all(plugin);
        outfit_overlay::clear(plugin);
        board_pose::clear(plugin);
        board_pose::park(plugin, {});
        cancel_throwdown(plugin);
        if (capturer_ == plugin) capturer_.clear();
        if (std::erase_if(replacements_, [&](const auto& r) { return r.second.first == plugin; })) publish_replacements();
        if (recorder_ == plugin) recorder_.clear();
        release_camera(plugin);
        (void)freeze(plugin, false);
        hide_hud(plugin, false);
        xray::prepare(plugin, false);
        hud_corner::release(plugin);
        game_speed::ask(plugin, 1.0f);
        grind_exit::release(plugin);
        plugin_feed::release_tags(std::string(plugin));
        if (std::erase_if(binds_, [&](const auto& item) { return item.first.first == plugin; })) publish_binds();
    }
    void set_storage(const std::filesystem::path& storage) {
        if (!binds_file_.empty() || storage.empty()) return;
        binds_file_ = storage / "binds.json";
        load_overrides();
    }

private:
    void publish_replacements() {
        std::vector<skater_animation::Replacement> list;
        for (const auto& [key, value] : replacements_) list.push_back(value.second);
        skater_animation::set_replacements(std::move(list));
    }
    std::string player_, recorder_, thrower_, capturer_;
    std::uint64_t capture_until_{}, flight_read_at_{};
    std::optional<FlightInput> flight_;
    // By (trick, pop): the plugin and its clip.
    std::map<std::pair<std::string, std::string>, std::pair<std::string, skater_animation::Replacement>> replacements_;
    using BindKey = std::pair<std::string, std::string>;
    struct Bind {
        std::uint32_t own{};
        ControllerComboLatch latch;
    };
    std::uint32_t combo_in_use(const BindKey& key, const Bind& bind) const {
        const auto found = overrides_.find(key);
        return found != overrides_.end() ? found->second : bind.own;
    }
    void publish_binds() {
        std::vector<plugin_ui::Bind> list;
        for (const auto& [key, bind] : binds_) list.push_back({key.first, key.second, bind.own, combo_in_use(key, bind)});
        plugin_ui::publish_binds(std::move(list));
    }
    void apply_rebinds() {
        auto changes = plugin_ui::take_rebinds();
        if (changes.empty()) return;
        for (auto& change : changes) {
            BindKey key{std::move(change.plugin), std::move(change.action)};
            if (!change.combo) overrides_.erase(key);
            else if (!*change.combo || valid_action_binding(*change.combo)) overrides_[std::move(key)] = *change.combo;
        }
        save_overrides();
        publish_binds();
    }
    // binds.json: {"<plugin>": {"<action>": combo}}, the player's choices only.
    void load_overrides() {
        try {
            std::ifstream in(binds_file_, std::ios::binary);
            if (!in) return;
            const std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
            const auto json = Json::parse(text, {256 * 1024, 4, 4096});
            if (!json.is_object()) return;
            for (const auto& [plugin, actions] : json.items()) {
                if (!actions.is_object()) continue;
                for (const auto& [action, combo] : actions.items())
                    if (combo.is_number_unsigned() && combo.get<std::uint64_t>() <= 0xffffffffu)
                        overrides_[{plugin, action}] = static_cast<std::uint32_t>(combo.get<std::uint64_t>());
            }
        } catch (const std::exception&) {
            overrides_.clear(); // a broken file is the plugins' own binds again
        }
    }
    void save_overrides() {
        if (binds_file_.empty()) return;
        auto json = Json::object();
        for (const auto& [key, combo] : overrides_) {
            if (!json.contains(key.first)) json[key.first] = Json::object();
            json[key.first][key.second] = combo;
        }
        try {
            std::filesystem::create_directories(binds_file_.parent_path());
            auto temporary = std::filesystem::path(binds_file_) += ".tmp";
            {
                std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
                const auto text = json.dump(2);
                if (!out || !out.write(text.data(), static_cast<std::streamsize>(text.size()))) return;
            }
            std::filesystem::rename(temporary, binds_file_);
        } catch (const std::exception&) {
        }
    }
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
    std::string camera_holder_, follower_, camera_status_;
    std::array<float, 16> camera_pose_{};
    float camera_fov_{};
    bool camera_shown_{}, camera_releasing_{};
    std::set<std::string, std::less<>> freezers_;
    std::set<std::string, std::less<>> hud_hiders_;
    bool hud_was_hidden_{};
    bool music_started_{};
    std::map<BindKey, Bind> binds_;
    std::map<BindKey, std::uint32_t> overrides_;
    std::filesystem::path binds_file_;
};
RealGame* the_game{};
}

Game& real_game(std::uintptr_t image_base, const std::filesystem::path& storage) {
    static auto* game = new RealGame(image_base); // the process's lifetime, as the host's
    the_game = game;
    game->set_storage(storage);
    return *game;
}
void apply_plugin_camera(std::uintptr_t image_base, std::uintptr_t client, bool ready, bool phase) noexcept {
    try {
        if (the_game) the_game->apply_camera(image_base, client, ready, phase);
    } catch (...) {}
}
bool plugin_freeze_active() noexcept { return the_game && the_game->frozen(); }
bool plugin_input_capture() noexcept { return the_game && the_game->capturing(); }
void tick_plugin_sounds(std::uintptr_t image_base, std::uintptr_t client, bool ready) noexcept {
    plugin_sound::tick(image_base, client, ready);
}
void tick_plugin_animation(std::uintptr_t image_base, std::uintptr_t client) noexcept {
    try {
        if (the_game) the_game->tick_animation(image_base, client);
    } catch (...) {}
}
}
