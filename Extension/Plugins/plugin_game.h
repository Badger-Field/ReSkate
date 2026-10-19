#pragma once
#include "plugin_wave.h"
#include "Extension/AnimationStudio/anim_core.h"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

// The game behind the wider set of the plugin API (docs: ReSkate Plugin System Design): what plugins
// may read and change of the skater, the world, the session and the rest. Every function has an
// "unavailable" default, so the host runs without a game (the tests give it a fake one) and the
// game side fills in what it can (plugin_game_runtime.cpp). Client thread throughout.
//
// A string a function returns is empty when it worked, and otherwise says why not, for the plugin.
namespace dingosdk::plugins {
using Vec3 = std::array<float, 3>;
inline constexpr char unavailable[] = "unavailable here";

// The game's camera as it is now: where it is, the way it looks, and its vertical field of view.
struct CameraView {
    Vec3 position{}, forward{};
    float fov{};
};
// Input for a plugin that flies something: the flight keys and mouse look (as Freecam reads them),
// a gamepad's sticks, triggers and buttons (XInput's bits), a joystick or radio's axes and buttons.
struct FlightInput {
    float right{}, up{}, forward{}, look_x{}, look_y{};
    bool boost{};
};
struct PadInput {
    float left_x{}, left_y{}, right_x{}, right_y{}, left_trigger{}, right_trigger{};
    std::uint32_t buttons{};
    bool playstation{};
};
struct StickInput {
    std::array<float, 6> axes{};
    std::uint32_t buttons{};
    std::string name;
};
// A sound a plugin plays (reskate.audio): its wave, or the motor synth when it has none.
struct SoundStart {
    std::shared_ptr<const Wave> wave;
    float volume{1}, pitch{1}, range{40}, throttle{}, speed{};
    bool loop{};
    std::optional<Vec3> position; // empty: at the local skater
};
struct SoundChange {
    std::optional<float> volume, pitch, range, throttle, speed;
    std::optional<std::optional<Vec3>> position;
};
// What the local skater's physics did since the last tick (events.skater).
struct SkaterNews {
    struct Landing {
        float down_speed{}, speed{};
        bool rail{};
    };
    struct Hit {
        std::string bone; // as the game names its body ("Left forearm")
        float impact{};
        bool vehicle{}, board{};
    };
    struct Fall {
        float seconds{}, left{}, right{};
        struct Bone {
            std::string name;
            float impact{}, slide{};
        };
        std::vector<Bone> bones; // those that hit or slid
    };
    std::vector<Landing> landings;
    std::vector<Hit> hits;
    std::vector<Fall> falls;
};
// The last slam: its force and the game's cause number, and how long ago (ms).
struct SlamNews {
    float force{};
    int cause{-1};
    double ago_ms{};
};
struct CatalogItem {
    std::string key, title, category;
};
// A park editor object on the map being played: its layout id, the item, where and how it sits.
struct GameObject {
    std::uint64_t id{};
    std::string item;
    Vec3 position{};
    std::array<float, 4> rotation{0, 0, 0, 1}; // x, y, z, w
    float scale{1};
};
struct Destination {
    std::string id, name, map; // fast travel
};
struct LevelInfo {
    std::string asset, name;
    bool can_load{};
};
// Another player's skater as the session last heard it.
struct PeerSkater {
    std::uint64_t id{};
    Vec3 position{};
    float heading{}; // as the local skater's
    double age_ms{}; // since its pose arrived
};
struct ChatLine {
    std::uint64_t sequence{}, sender{};
    std::string name, text;
    bool local{}, server{};
};
struct Track {
    bool available{}, playing{};
    std::string app, title, artist, album;
    std::uint64_t revision{};
};
struct ThrowdownState {
    bool active{}, host{};
    struct Player {
        std::string name;
        int letters{};
        bool up{}, out{};
    };
    std::vector<Player> skate; // S.K.A.T.E.'s players and letters, while one is shown
};
struct Progress {
    bool available{};
    struct District {
        std::string id, name;
        int rank{};
    };
    std::vector<District> districts;
    struct Challenge {
        std::string id, type, neighborhood;
        int attempts{}, completed_goals{}, goal_count{};
    };
    std::vector<Challenge> challenges;
    double score{}, score_cap{};
    int score_level{};
};

class Game {
public:
    virtual ~Game() = default;
    // ---- the local skater ----------------------------------------------------------------------
    // Its physics state (100s riding, 200s in the air, 300 a wipeout, 400s grinding, 504 off the
    // board), or empty without a skater; and how many wipeouts it has had this session.
    virtual std::optional<std::uint32_t> physics_state() { return std::nullopt; }
    virtual std::uint64_t wipeouts() { return 0; }
    // "noclip_on", "noclip_off", "boost_forward", "boost_up", "boost_offboard": queued as the
    // console's own are, under the host's rules.
    virtual std::string control(std::string_view) { return unavailable; }
    // The game's HUD hidden while any plugin asks; given back when the last one stops asking.
    virtual void hide_hud(std::string_view /*plugin*/, bool) {}
    // ---- the world -------------------------------------------------------------------------------
    virtual std::vector<CatalogItem> object_catalog() { return {}; }
    virtual std::vector<GameObject> objects() { return {}; }
    // "place" (item and pose), "move" (id and pose) or "delete" (id): through the park editor,
    // under the session's object rules and limits. One at a time: it is busy until done.
    virtual std::string edit_object(std::string_view, const GameObject&) { return unavailable; }
    // kind "tod" (key: morning, noon, afternoon, evening, night, weatherday, weathernight, default),
    // "control" (key: fog, fog_distance, sky_brightness, clouds, wind_strength, wind_direction,
    // traffic, pedestrians; value a number, -1 the default) or "park" (key: a lot, value a layout).
    virtual std::string set_environment(std::string_view, std::string_view, std::string_view) { return unavailable; }
    virtual std::vector<Destination> destinations() { return {}; }
    virtual std::vector<LevelInfo> levels() { return {}; }
    // kind "location" (a destination's id) or "level" (a level's asset); a guest is refused.
    virtual std::string travel(std::string_view, std::string_view) { return unavailable; }
    // "spawn" (count) or "clear"; refused in a session, where others would never see them.
    virtual std::string ai(std::string_view, int) { return unavailable; }
    virtual int ai_count() { return 0; }
    // ---- the session ----------------------------------------------------------------------------
    virtual std::vector<PeerSkater> peer_skaters() { return {}; }
    // A short tag beside a player's nametag, the plugin's own (empty text takes it away).
    virtual void set_nametag(std::string_view /*plugin*/, std::uint64_t, std::string, std::uint32_t) {}
    // 0 mutes, 1 is as heard, up to 10. The local player's ears only.
    virtual std::string voice_volume(std::uint64_t, float) { return unavailable; }
    // Chat lines after `sequence`, as this player sees them (filtered when they filter).
    virtual std::vector<ChatLine> chat_since(std::uint64_t) { return {}; }
    virtual std::string send_chat(std::string_view) { return unavailable; }
    // A line in this player's chat only, under the plugin's name with a "Plugin" tag.
    virtual std::string local_chat(std::string_view /*plugin*/, std::string_view, std::uint32_t /*colour, 0 the usual*/) { return unavailable; }
    virtual ThrowdownState throwdowns() { return {}; }
    // A drop the local player hosts where the skater stands, for chosen players of the session
    // (`series` "JamSession", "SpotBattle" or "ThrowdownSkate"): they see it and join it with the
    // game's own Join, and it starts once all have. Its status: "", "asked", "spawning", "open",
    // "started", "cancelled", or why it failed.
    virtual std::string host_throwdown(std::string_view /*plugin*/, std::string_view /*series*/, std::vector<std::uint64_t> /*players*/,
                                       int /*max players*/) {
        return unavailable;
    }
    virtual void cancel_throwdown(std::string_view /*plugin*/) {}
    virtual std::string throwdown_status(std::string_view /*plugin*/) { return {}; }
    // ---- the rest ----------------------------------------------------------------------------------
    // The music an outside player (Spotify and the like) is playing, and "play", "pause",
    // "next" or "previous" for it.
    virtual Track track() { return {}; }
    virtual std::string music(std::string_view) { return unavailable; }
    // The game's own soundtrack on the player's headphones (none while a Spotify station is on:
    // then the outside player's song is the one heard), and "next" for it.
    virtual std::optional<Track> game_track() { return std::nullopt; }
    virtual std::string game_music(std::string_view) { return unavailable; }
    virtual Progress progress() { return {}; }
    virtual std::optional<std::string> setting(std::string_view) { return std::nullopt; }
    virtual std::vector<std::string> setting_names() { return {}; }
    // ---- camera ------------------------------------------------------------------------------------
    // The view from a pose (rows right, up, back and position, as follow_camera.h builds them) with
    // a vertical field of view in degrees (0 the camera's own). One plugin holds it at a time, and
    // Spectate takes it back while it runs; the game's own camera returns on release.
    virtual std::optional<CameraView> camera_view() { return std::nullopt; }
    virtual std::string set_camera(std::string_view /*plugin*/, const std::array<float, 16>&, float) { return unavailable; }
    // Spectate's camera on a session player (0 stops the one this plugin started).
    virtual std::string follow_player(std::string_view /*plugin*/, std::uint64_t) { return unavailable; }
    virtual void release_camera(std::string_view /*plugin*/) {}
    // "" while the plugin's view is the one shown, otherwise why not.
    virtual std::string camera_status(std::string_view /*plugin*/) { return unavailable; }
    // Holds the skater where it is (noclip, with no input), while any plugin asks.
    virtual std::string freeze(std::string_view /*plugin*/, bool) { return unavailable; }
    // ---- the session's authority (plugins' server copies) --------------------------------------------
    // Drops a player from the session; a chat line to everyone; one to one player (a dedicated
    // server's only: a lobby has no private chat); moves a player's skater (their game does the move).
    virtual std::string kick(std::uint64_t, std::string_view /*why*/) { return unavailable; }
    virtual std::string announce(std::string_view /*plugin*/, std::string_view) { return unavailable; }
    virtual std::string tell(std::string_view /*plugin*/, std::uint64_t, std::string_view) { return unavailable; }
    virtual std::string move_player(std::uint64_t, const Vec3&) { return unavailable; }
    // ---- skater.animation: clips on the local skater's body (Extension/Skater/skater_animation.h) ------
    // One plugin plays at a time; none while a throwdown runs. A replacement plays when the game does
    // `trick` ("kickflip") with that pop ("" any, "low", "mid", "high"); a null clip takes it away.
    using ClipRef = std::shared_ptr<const anim_studio::Clip>;
    virtual std::string play_clip(std::string_view /*plugin*/, ClipRef, float /*speed*/, float /*blend*/, bool /*loop*/) { return unavailable; }
    virtual void stop_clip(std::string_view /*plugin*/) {}
    virtual bool clip_playing(std::string_view /*plugin*/) { return false; }
    virtual std::string replace_trick(std::string_view /*plugin*/, std::string /*trick*/, std::string /*pop*/, ClipRef, float /*speed*/) {
        return unavailable;
    }
    // Records the skater's pose for so many seconds; the clip comes from take_recording once done.
    virtual std::string record_clip(std::string_view /*plugin*/, float /*seconds*/) { return unavailable; }
    virtual ClipRef take_recording(std::string_view /*plugin*/) { return nullptr; }
    // The game's name for the trick the skater is doing now ("kickflip"), or empty.
    virtual std::string current_trick() { return {}; }
    // ---- outfit: items worn over the local skater's outfit (Extension/Skater/outfit_overlay.h) ----------
    struct OutfitSlot {
        std::string name, item;
        std::vector<float> parameters;
    };
    virtual std::string outfit_set(std::string_view /*plugin*/, std::string_view /*slot*/, std::string_view /*item*/, std::vector<float>, bool) {
        return unavailable;
    }
    virtual void outfit_clear(std::string_view /*plugin*/, std::string_view /*slot, empty: all*/) {}
    virtual std::string outfit_find(std::string_view) { return {}; }
    virtual std::vector<OutfitSlot> outfit_slots() { return {}; }
    virtual std::string outfit_status() { return unavailable; }
    // ---- board: what is drawn of the local skateboard (Extension/Skater/board_pose.h) ------------------
    struct BoardBone {
        Vec3 position{};
        std::array<float, 4> rotation{0, 0, 0, 1};
        Vec3 scale{1, 1, 1};
    };
    virtual std::optional<std::array<BoardBone, 16>> board_bones() { return std::nullopt; }
    virtual std::string board_set(std::string_view /*plugin*/, int, const BoardBone&, bool /*world*/) { return unavailable; }
    virtual std::string board_hide(std::string_view /*plugin*/, int) { return unavailable; }
    virtual void board_clear(std::string_view /*plugin*/, int /*bone, -1: all*/) {}
    virtual void board_park(std::string_view /*plugin*/, std::vector<int>) {}
    virtual std::string board_toss(std::string_view /*plugin*/, std::vector<int>, const Vec3&, const Vec3&, float) { return unavailable; }
    // ---- the skater's physics -----------------------------------------------------------------------
    virtual SkaterNews take_skater_news() { return {}; }
    virtual std::optional<Vec3> board_velocity() { return std::nullopt; }
    virtual std::optional<SlamNews> last_slam() { return std::nullopt; }
    // The skater bails as from a slam of `force` (0.5 to 3); not in a throwdown. While No Bail protects
    // the skater the wipeout is dropped, as any other is.
    virtual std::string wipeout(float /*force*/) { return unavailable; }
    // ---- audio: plugins' own sounds, in a level ---------------------------------------------------------
    // Its id, or 0 when the plugin plays 8 already (or there is no level).
    virtual std::uint64_t play_sound(std::string_view /*plugin*/, SoundStart) { return 0; }
    virtual bool change_sound(std::string_view /*plugin*/, std::uint64_t, const SoundChange&) { return false; }
    virtual void stop_sound(std::string_view /*plugin*/, std::uint64_t) {}
    virtual bool sound_playing(std::string_view /*plugin*/, std::uint64_t) { return false; }
    // ---- input -------------------------------------------------------------------------------------
    // The flight keys and mouse only while the plugin's camera is shown; pads while the game has the
    // focus. Capture keeps the game from the controller too, for 250 ms a call (renewed each tick),
    // and only while the plugin's camera is shown.
    virtual std::optional<FlightInput> flight_input(std::string_view /*plugin*/) { return std::nullopt; }
    virtual std::optional<PadInput> gamepad() { return std::nullopt; }
    virtual std::optional<StickInput> joystick() { return std::nullopt; }
    virtual std::string capture_input(std::string_view /*plugin*/, bool) { return unavailable; }
    // A named action on a key or pad combo (controller_bindings.h's encoding); presses come back
    // from take_presses as (plugin, action), only while the game itself has the input.
    virtual std::string bind(std::string_view /*plugin*/, std::string_view, std::uint32_t) { return unavailable; }
    virtual std::vector<std::pair<std::string, std::string>> take_presses() { return {}; }
    // Everything a plugin holds of the game (its HUD hide, nametags, binds), when it stops.
    virtual void release(std::string_view /*plugin*/) {}
};

// "F7", "Ctrl+K", "Shift+Alt+1", "Space"; or a pad combo, "Pad.LB+Pad.RB", "Pad.A", "Pad.Up"...
// As controller_bindings.h encodes binds; 0 for a name that is not one.
std::uint32_t combo_of(std::string_view name);
// A physics state as plugins hear it: "riding", "air", "wipeout", "grinding", "off_board",
// "planted", "other" or "none".
const char* skater_state_name(std::uint32_t state);
}
