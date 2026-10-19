#pragma once
#include <array>
#include <cstdint>
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
    virtual ThrowdownState throwdowns() { return {}; }
    // ---- the rest ----------------------------------------------------------------------------------
    // The music an outside player (Spotify and the like) is playing, and "play", "pause",
    // "next" or "previous" for it.
    virtual Track track() { return {}; }
    virtual std::string music(std::string_view) { return unavailable; }
    virtual Progress progress() { return {}; }
    virtual std::optional<std::string> setting(std::string_view) { return std::nullopt; }
    virtual std::vector<std::string> setting_names() { return {}; }
    // ---- input -------------------------------------------------------------------------------------
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
