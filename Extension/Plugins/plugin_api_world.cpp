#include "plugin_api.h"
#include "plugin_game.h"
#include "lua.h"
#include "lualib.h"
#include <algorithm>
#include <charconv>
#include <cmath>

// The wider set's game parts of the reskate API: the skater's state, control and save points, the
// HUD, park editor objects, the environment, travel, AI skaters, other players' skaters, nametags,
// voice, chat, throwdowns, music, progress, settings, input binds and full-screen screens. Each
// checks its permission, its arguments and its own rate, and the game (plugin_game.h) does the rest
// under the game's own rules (a session's host, the park editor's limits).
namespace dingosdk::plugins {
namespace {
Plugin& plugin_of(lua_State* L) {
    auto& plugin = *static_cast<Plugin*>(lua_callbacks(L)->userdata);
    if (plugin.halted()) luaL_error(L, "the plugin was stopped");
    return plugin;
}
void need(lua_State* L, Plugin& plugin, const char* permission, const char* what) {
    if (!plugin.has(permission)) luaL_error(L, "%s needs the \"%s\" permission in reskate-plugin.json", what, permission);
}
std::string checked_text(lua_State* L, int index, std::size_t most, const char* what) {
    std::size_t length = 0;
    const char* text = luaL_checklstring(L, index, &length);
    if (length > most) luaL_error(L, "%s is longer than %d characters", what, static_cast<int>(most));
    return std::string(text, length);
}
Vec3 position_of(lua_State* L, int index, const char* what) {
    const float* v = luaL_checkvector(L, index);
    for (int i = 0; i < 3; ++i)
        if (!std::isfinite(v[i]) || std::fabs(v[i]) > 1e5f) luaL_error(L, "%s must be a finite position within 100 km", what);
    return {v[0], v[1], v[2]};
}
void push_position(lua_State* L, const Vec3& v) { lua_pushvector(L, v[0], v[1], v[2]); }
// ok, or false and why: what most of these return.
int result(lua_State* L, const std::string& why) {
    lua_pushboolean(L, why.empty());
    if (why.empty()) return 1;
    lua_pushlstring(L, why.data(), why.size());
    return 2;
}
// A player by id ("7656...") or a player table ({id = ...}).
std::uint64_t player_of(lua_State* L, int index) {
    std::string id;
    if (lua_istable(L, index)) {
        lua_getfield(L, index, "id");
        id = luaL_checkstring(L, -1);
        lua_pop(L, 1);
    } else id = luaL_checkstring(L, index);
    std::uint64_t value = 0;
    if (std::from_chars(id.data(), id.data() + id.size(), value).ec != std::errc{} || !value) luaL_argerror(L, index, "not a player");
    return value;
}
// A per-second budget; false past it.
bool within(double now, double& second, int& count, int most) {
    if (now - second >= 1000) {
        second = now;
        count = 0;
    }
    return ++count <= most;
}
}

const char* skater_state_name(std::uint32_t state) {
    if (state >= 100 && state < 200) return "riding";
    if (state >= 200 && state < 300) return "air";
    if (state >= 300 && state < 400) return "wipeout";
    if (state >= 400 && state < 500) return "grinding";
    if (state >= 500 && state < 600) return "off_board";
    if (state == 602) return "planted";
    return state ? "other" : "none";
}

struct WorldApi {
    static Game& game(Plugin& plugin) { return plugin.services_.game(); }

    // ---- events.skater -------------------------------------------------------------------------
    static int state(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "events.skater", "reskate.skater.state");
        const auto now = game(plugin).physics_state();
        if (!now) lua_pushnil(L);
        else lua_pushstring(L, skater_state_name(*now));
        return 1;
    }
    // ---- skater.control ------------------------------------------------------------------------
    static int control(lua_State* L, const char* action) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "skater.control", "reskate.skater control");
        if (!within(plugin.services_.now_ms(), plugin.control_second_, plugin.controls_, 4)) return result(L, "asked more than 4 times in a second");
        return result(L, game(plugin).control(action));
    }
    static int noclip(lua_State* L) { return control(L, lua_toboolean(L, 1) ? "noclip_on" : "noclip_off"); }
    static int boost(lua_State* L) {
        const std::string kind = luaL_checkstring(L, 1);
        if (kind == "forward") return control(L, "boost_forward");
        if (kind == "up") return control(L, "boost_up");
        if (kind == "offboard") return control(L, "boost_offboard");
        luaL_argerror(L, 1, "a boost is \"forward\", \"up\" or \"offboard\"");
        return 0;
    }
    // ---- trainer: one save point of the plugin's own -------------------------------------------
    static int set_point(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "trainer", "reskate.trainer.set_point");
        const auto s = plugin.services_.skater();
        if (!s) return result(L, "there is no skater");
        plugin.trainer_point_ = std::pair(s->position, s->heading);
        return result(L, {});
    }
    static int back(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "trainer", "reskate.trainer.back");
        if (!plugin.trainer_point_) return result(L, "no save point is set");
        if (!within(plugin.services_.now_ms(), plugin.teleport_second_, plugin.teleports_, plugin.limits_.teleports_per_second))
            return result(L, "asked more than twice in a second");
        switch (plugin.services_.teleport(plugin.trainer_point_->first, plugin.trainer_point_->second)) {
        case Teleport::sent: return result(L, {});
        case Teleport::forbidden: return result(L, "the host has turned teleporting off");
        default: return result(L, "teleporting is unavailable now");
        }
    }
    // ---- hud ---------------------------------------------------------------------------------------
    static int hide_hud(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "hud", "reskate.hud.hide");
        game(plugin).hide_hud(plugin.id(), lua_toboolean(L, 1) != 0);
        return 0;
    }
    // ---- world.objects -----------------------------------------------------------------------------
    static int catalog(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.objects", "reskate.objects.catalog");
        const auto items = game(plugin).object_catalog();
        lua_createtable(L, static_cast<int>(items.size()), 0);
        for (std::size_t i = 0; i < items.size(); ++i) {
            lua_createtable(L, 0, 3);
            lua_pushlstring(L, items[i].key.data(), items[i].key.size());
            lua_setfield(L, -2, "key");
            lua_pushlstring(L, items[i].title.data(), items[i].title.size());
            lua_setfield(L, -2, "title");
            lua_pushlstring(L, items[i].category.data(), items[i].category.size());
            lua_setfield(L, -2, "category");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    static int objects(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.objects", "reskate.objects.list");
        const auto list = game(plugin).objects();
        lua_createtable(L, static_cast<int>(list.size()), 0);
        for (std::size_t i = 0; i < list.size(); ++i) {
            const auto& o = list[i];
            lua_createtable(L, 0, 5);
            const auto id = std::to_string(o.id);
            lua_pushlstring(L, id.data(), id.size());
            lua_setfield(L, -2, "id");
            lua_pushlstring(L, o.item.data(), o.item.size());
            lua_setfield(L, -2, "item");
            push_position(L, o.position);
            lua_setfield(L, -2, "position");
            // The heading the pose turns to, as the skater's: about world up.
            const float heading = std::atan2(2 * (o.rotation[3] * o.rotation[1] + o.rotation[0] * o.rotation[2]),
                                             1 - 2 * (o.rotation[1] * o.rotation[1] + o.rotation[0] * o.rotation[0]));
            lua_pushnumber(L, heading);
            lua_setfield(L, -2, "heading");
            lua_pushnumber(L, o.scale);
            lua_setfield(L, -2, "scale");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    static GameObject pose(lua_State* L, int at, int heading_index, int scale_index) {
        GameObject object;
        object.position = position_of(L, at, "the object's position");
        const double heading = luaL_optnumber(L, heading_index, 0);
        if (!std::isfinite(heading)) luaL_argerror(L, heading_index, "a heading is a finite number of radians");
        object.rotation = {0, static_cast<float>(std::sin(heading / 2)), 0, static_cast<float>(std::cos(heading / 2))};
        if (scale_index) {
            const double scale = luaL_optnumber(L, scale_index, 1);
            if (!(scale >= 0.1 && scale <= 10)) luaL_argerror(L, scale_index, "a scale is 0.1 to 10");
            object.scale = static_cast<float>(scale);
        }
        return object;
    }
    static std::uint64_t object_id(lua_State* L, int index) {
        const std::string text = luaL_checkstring(L, index);
        std::uint64_t id = 0;
        if (std::from_chars(text.data(), text.data() + text.size(), id).ec != std::errc{} || !id) luaL_argerror(L, index, "not an object's id");
        return id;
    }
    static int edit(lua_State* L, const char* operation, GameObject object) {
        auto& plugin = plugin_of(L);
        if (!within(plugin.services_.now_ms(), plugin.edit_second_, plugin.edits_, 4)) return result(L, "more than 4 edits in a second");
        return result(L, game(plugin).edit_object(operation, object));
    }
    static int place(lua_State* L) {
        need(L, plugin_of(L), "world.objects", "reskate.objects.place");
        auto object = pose(L, 2, 3, 4);
        object.item = checked_text(L, 1, 200, "an item's key");
        return edit(L, "place", object);
    }
    static int move(lua_State* L) {
        need(L, plugin_of(L), "world.objects", "reskate.objects.move");
        auto object = pose(L, 2, 3, 0);
        object.id = object_id(L, 1);
        return edit(L, "move", object);
    }
    static int remove(lua_State* L) {
        need(L, plugin_of(L), "world.objects", "reskate.objects.remove");
        GameObject object;
        object.id = object_id(L, 1);
        return edit(L, "delete", object);
    }
    // ---- world.environment -----------------------------------------------------------------------
    static int environment(lua_State* L, const char* kind, const std::string& key, const std::string& value) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.environment", "reskate.world's environment");
        if (!within(plugin.services_.now_ms(), plugin.environment_second_, plugin.environments_, 4))
            return result(L, "more than 4 changes in a second");
        return result(L, game(plugin).set_environment(kind, key, value));
    }
    static int set_time(lua_State* L) { return environment(L, "tod", checked_text(L, 1, 20, "a time of day"), {}); }
    static int set_control(lua_State* L) {
        const auto key = checked_text(L, 1, 30, "a world control");
        const double value = luaL_checknumber(L, 2);
        if (!std::isfinite(value)) luaL_argerror(L, 2, "a finite number");
        return environment(L, "control", key, std::to_string(value));
    }
    static int set_park(lua_State* L) { return environment(L, "park", checked_text(L, 1, 30, "a lot"), checked_text(L, 2, 40, "a layout")); }
    // ---- travel ------------------------------------------------------------------------------------
    static int locations(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "travel", "reskate.travel.locations");
        const auto list = game(plugin).destinations();
        lua_createtable(L, static_cast<int>(list.size()), 0);
        for (std::size_t i = 0; i < list.size(); ++i) {
            lua_createtable(L, 0, 3);
            lua_pushlstring(L, list[i].id.data(), list[i].id.size());
            lua_setfield(L, -2, "id");
            lua_pushlstring(L, list[i].name.data(), list[i].name.size());
            lua_setfield(L, -2, "name");
            lua_pushlstring(L, list[i].map.data(), list[i].map.size());
            lua_setfield(L, -2, "map");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    static int levels(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "travel", "reskate.travel.levels");
        const auto list = game(plugin).levels();
        lua_createtable(L, static_cast<int>(list.size()), 0);
        for (std::size_t i = 0; i < list.size(); ++i) {
            lua_createtable(L, 0, 3);
            lua_pushlstring(L, list[i].asset.data(), list[i].asset.size());
            lua_setfield(L, -2, "asset");
            lua_pushlstring(L, list[i].name.data(), list[i].name.size());
            lua_setfield(L, -2, "name");
            lua_pushboolean(L, list[i].can_load);
            lua_setfield(L, -2, "can_load");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    static int travel(lua_State* L, const char* kind) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "travel", "reskate.travel");
        const auto target = checked_text(L, 1, 260, "a destination");
        const double now = plugin.services_.now_ms();
        if (now - plugin.travel_at_ < 5000) return result(L, "one trip every 5 s");
        plugin.travel_at_ = now;
        return result(L, game(plugin).travel(kind, target));
    }
    static int go(lua_State* L) { return travel(L, "location"); }
    static int load(lua_State* L) { return travel(L, "level"); }
    // ---- world.ai -----------------------------------------------------------------------------------
    static int ai_spawn(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.ai", "reskate.ai.spawn");
        const auto count = static_cast<int>(luaL_optinteger(L, 1, 1));
        if (count < 1 || count > 8) luaL_argerror(L, 1, "1 to 8 at a time");
        if (!within(plugin.services_.now_ms(), plugin.control_second_, plugin.controls_, 4)) return result(L, "asked more than 4 times in a second");
        return result(L, game(plugin).ai("spawn", count));
    }
    static int ai_clear(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.ai", "reskate.ai.clear");
        return result(L, game(plugin).ai("clear", 0));
    }
    static int ai_count(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "world.ai", "reskate.ai.count");
        lua_pushinteger(L, game(plugin).ai_count());
        return 1;
    }
    // ---- session.skaters / session.nametags / voice.control -----------------------------------------
    static int skaters(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "session.skaters", "reskate.session.skaters");
        const auto list = game(plugin).peer_skaters();
        lua_createtable(L, static_cast<int>(list.size()), 0);
        for (std::size_t i = 0; i < list.size(); ++i) {
            lua_createtable(L, 0, 4);
            const auto id = std::to_string(list[i].id);
            lua_pushlstring(L, id.data(), id.size());
            lua_setfield(L, -2, "id");
            push_position(L, list[i].position);
            lua_setfield(L, -2, "position");
            lua_pushnumber(L, list[i].heading);
            lua_setfield(L, -2, "heading");
            lua_pushnumber(L, list[i].age_ms);
            lua_setfield(L, -2, "age_ms");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    static int tag(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "session.nametags", "reskate.session.tag");
        const auto player = player_of(L, 1);
        std::string text = lua_isnoneornil(L, 2) ? std::string{} : checked_text(L, 2, 16, "a nametag's tag");
        for (const char c : text)
            if (c < 0x20 || c > 0x7e) luaL_error(L, "a nametag's tag is printable text");
        std::uint32_t colour = 0xffffffff;
        if (!lua_isnoneornil(L, 3)) {
            const double value = luaL_checknumber(L, 3);
            if (!(value >= 0 && value <= 4294967295.0) || value != std::floor(value)) luaL_argerror(L, 3, "a colour number is 0xRRGGBBAA");
            const auto rgba = static_cast<std::uint32_t>(value);
            colour = (rgba >> 24) | ((rgba >> 16) & 0xff) << 8 | ((rgba >> 8) & 0xff) << 16 | (rgba & 0xff) << 24;
        }
        game(plugin).set_nametag(plugin.id(), player, std::move(text), colour);
        return 0;
    }
    static int voice_volume(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "voice.control", "reskate.voice.set_volume");
        const auto player = player_of(L, 1);
        const double volume = luaL_checknumber(L, 2);
        if (!(volume >= 0 && volume <= 10)) luaL_argerror(L, 2, "a volume is 0 (muted) to 10");
        if (!within(plugin.services_.now_ms(), plugin.voice_second_, plugin.voices_, 10)) return result(L, "more than 10 changes in a second");
        return result(L, game(plugin).voice_volume(player, static_cast<float>(volume)));
    }
    // ---- chat.send ------------------------------------------------------------------------------------
    static int chat_send(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "chat.send", "reskate.chat.send");
        const auto text = checked_text(L, 1, 200, "a chat line");
        if (text.empty() || text.front() == '/') luaL_argerror(L, 1, "a chat line, not empty and not a / command");
        const double now = plugin.services_.now_ms();
        if (now - plugin.chat_at_ < 3000) return result(L, "one line every 3 s");
        plugin.chat_at_ = now;
        return result(L, game(plugin).send_chat(text));
    }
    // ---- throwdowns -------------------------------------------------------------------------------------
    static int throwdown_state(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "throwdowns", "reskate.throwdowns.state");
        const auto t = game(plugin).throwdowns();
        lua_createtable(L, 0, 3);
        lua_pushboolean(L, t.active);
        lua_setfield(L, -2, "active");
        lua_pushboolean(L, t.host);
        lua_setfield(L, -2, "host");
        lua_createtable(L, static_cast<int>(t.skate.size()), 0);
        for (std::size_t i = 0; i < t.skate.size(); ++i) {
            lua_createtable(L, 0, 4);
            lua_pushlstring(L, t.skate[i].name.data(), t.skate[i].name.size());
            lua_setfield(L, -2, "name");
            lua_pushinteger(L, t.skate[i].letters);
            lua_setfield(L, -2, "letters");
            lua_pushboolean(L, t.skate[i].up);
            lua_setfield(L, -2, "up");
            lua_pushboolean(L, t.skate[i].out);
            lua_setfield(L, -2, "out");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        lua_setfield(L, -2, "skate");
        return 1;
    }
    // ---- music -------------------------------------------------------------------------------------------
    static void push_track(lua_State* L, const Track& t) {
        lua_createtable(L, 0, 5);
        lua_pushlstring(L, t.app.data(), t.app.size());
        lua_setfield(L, -2, "app");
        lua_pushlstring(L, t.title.data(), t.title.size());
        lua_setfield(L, -2, "title");
        lua_pushlstring(L, t.artist.data(), t.artist.size());
        lua_setfield(L, -2, "artist");
        lua_pushlstring(L, t.album.data(), t.album.size());
        lua_setfield(L, -2, "album");
        lua_pushboolean(L, t.playing);
        lua_setfield(L, -2, "playing");
    }
    static int current(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "music", "reskate.music.current");
        const auto t = game(plugin).track();
        if (!t.available) lua_pushnil(L);
        else push_track(L, t);
        return 1;
    }
    static int music(lua_State* L, const char* command) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "music", "reskate.music");
        if (!within(plugin.services_.now_ms(), plugin.music_second_, plugin.musics_, 2)) return result(L, "more than 2 a second");
        return result(L, game(plugin).music(command));
    }
    static int play(lua_State* L) { return music(L, "play"); }
    static int pause(lua_State* L) { return music(L, "pause"); }
    static int next(lua_State* L) { return music(L, "next"); }
    static int previous(lua_State* L) { return music(L, "previous"); }
    // ---- progression.read / settings.read -------------------------------------------------------------
    static int progress(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "progression.read", "reskate.progression.get");
        const auto p = game(plugin).progress();
        if (!p.available) {
            lua_pushnil(L);
            return 1;
        }
        lua_createtable(L, 0, 5);
        lua_createtable(L, static_cast<int>(p.districts.size()), 0);
        for (std::size_t i = 0; i < p.districts.size(); ++i) {
            lua_createtable(L, 0, 3);
            lua_pushlstring(L, p.districts[i].id.data(), p.districts[i].id.size());
            lua_setfield(L, -2, "id");
            lua_pushlstring(L, p.districts[i].name.data(), p.districts[i].name.size());
            lua_setfield(L, -2, "name");
            lua_pushinteger(L, p.districts[i].rank);
            lua_setfield(L, -2, "rank");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        lua_setfield(L, -2, "districts");
        lua_createtable(L, static_cast<int>(p.challenges.size()), 0);
        for (std::size_t i = 0; i < p.challenges.size(); ++i) {
            const auto& c = p.challenges[i];
            lua_createtable(L, 0, 6);
            lua_pushlstring(L, c.id.data(), c.id.size());
            lua_setfield(L, -2, "id");
            lua_pushlstring(L, c.type.data(), c.type.size());
            lua_setfield(L, -2, "type");
            lua_pushlstring(L, c.neighborhood.data(), c.neighborhood.size());
            lua_setfield(L, -2, "neighborhood");
            lua_pushinteger(L, c.attempts);
            lua_setfield(L, -2, "attempts");
            lua_pushinteger(L, c.completed_goals);
            lua_setfield(L, -2, "completed_goals");
            lua_pushinteger(L, c.goal_count);
            lua_setfield(L, -2, "goal_count");
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        lua_setfield(L, -2, "challenges");
        lua_pushnumber(L, p.score);
        lua_setfield(L, -2, "score");
        lua_pushnumber(L, p.score_cap);
        lua_setfield(L, -2, "score_cap");
        lua_pushinteger(L, p.score_level);
        lua_setfield(L, -2, "score_level");
        return 1;
    }
    static int setting(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "settings.read", "reskate.settings.game");
        const auto value = game(plugin).setting(checked_text(L, 1, 120, "a setting's name"));
        if (value) lua_pushlstring(L, value->data(), value->size());
        else lua_pushnil(L);
        return 1;
    }
    static int setting_names(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "settings.read", "reskate.settings.names");
        const auto names = game(plugin).setting_names();
        lua_createtable(L, static_cast<int>(names.size()), 0);
        for (std::size_t i = 0; i < names.size(); ++i) {
            lua_pushlstring(L, names[i].data(), names[i].size());
            lua_rawseti(L, -2, static_cast<int>(i + 1));
        }
        return 1;
    }
    // ---- input -------------------------------------------------------------------------------------------
    // bind(action, "F7" | "Ctrl+K" | "Pad.LB+Pad.RB"): the "action" event names it on each press,
    // while the game itself has the input (no menu, console or text box open).
    static int bind(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "input", "reskate.input.bind");
        const auto action = checked_text(L, 1, 48, "an action's name");
        const auto key = checked_text(L, 2, 60, "a key");
        if (action.empty()) luaL_argerror(L, 1, "an action needs a name");
        const auto combo = combo_of(key);
        if (!combo) luaL_error(L, "\"%s\" is not a key: 0-9, A-Z, Space, F1-F12 with Ctrl, Shift or Alt; or Pad.A, Pad.LB+Pad.RB...", key.c_str());
        if (!plugin.binds_.contains(action) && plugin.binds_.size() >= 16) luaL_error(L, "at most 16 actions");
        if (const auto why = game(plugin).bind(plugin.id(), action, combo); !why.empty()) return result(L, why);
        plugin.binds_.insert(action);
        return result(L, {});
    }
    // ---- ui.screen ---------------------------------------------------------------------------------------
    // screen(title, build): a full-screen screen, drawn while open() has it open; Esc closes it.
    static int screen(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "ui.screen", "reskate.ui.screen");
        std::size_t length = 0;
        const char* text = luaL_checklstring(L, 1, &length);
        const std::string title(text, length);
        luaL_checktype(L, 2, LUA_TFUNCTION);
        if (title.empty() || title.size() > 40) luaL_argerror(L, 1, "a screen's title is 1 to 40 characters");
        if (plugin.screens_.size() >= 4) luaL_error(L, "at most 4 screens");
        for (const auto& entry : plugin.screens_)
            if (entry.title == title) luaL_error(L, "the screen \"%s\" is there already", title.c_str());
        lua_pushvalue(L, 2);
        plugin.screens_.push_back({title, lua_ref(L, -1)});
        lua_pop(L, 1);
        return 0;
    }
    static int open(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "ui.screen", "reskate.ui.open");
        const std::string title = luaL_checkstring(L, 1);
        if (std::none_of(plugin.screens_.begin(), plugin.screens_.end(), [&](const auto& e) { return e.title == title; }))
            luaL_error(L, "there is no screen \"%s\"", title.c_str());
        plugin.open_screen_ = title;
        return 0;
    }
    static int close(lua_State* L) {
        auto& plugin = plugin_of(L);
        need(L, plugin, "ui.screen", "reskate.ui.close");
        plugin.open_screen_.clear();
        return 0;
    }

    static void install(Plugin& plugin, lua_State* L);
};

void install_world_api(Plugin& plugin, lua_State* L) { WorldApi::install(plugin, L); }

// Adds the functions to the table at `field` of the reskate table on top of the stack, making it
// when there is none yet.
static void into(lua_State* L, const char* field, std::initializer_list<std::pair<const char*, lua_CFunction>> functions) {
    lua_getfield(L, -1, field);
    if (lua_isnil(L, -1)) {
        lua_pop(L, 1);
        lua_createtable(L, 0, static_cast<int>(functions.size()));
        lua_pushvalue(L, -1);
        lua_setfield(L, -3, field);
    }
    // A table made read-only earlier in the install is opened for this, and closed again.
    lua_setreadonly(L, -1, false);
    for (const auto& [name, function] : functions) {
        lua_pushcfunction(L, function, name);
        lua_setfield(L, -2, name);
    }
    lua_setreadonly(L, -1, true);
    lua_pop(L, 1);
}

void WorldApi::install(Plugin& plugin, lua_State* L) {
    (void)plugin;
    into(L, "skater", {{"state", state}, {"noclip", noclip}, {"boost", boost}});
    into(L, "trainer", {{"set_point", set_point}, {"back", back}});
    into(L, "hud", {{"hide", hide_hud}});
    into(L, "objects", {{"catalog", catalog}, {"list", objects}, {"place", place}, {"move", move}, {"remove", remove}});
    into(L, "world", {{"set_time", set_time}, {"set_control", set_control}, {"set_park", set_park}});
    into(L, "travel", {{"locations", locations}, {"levels", levels}, {"go", go}, {"load", load}});
    into(L, "ai", {{"spawn", ai_spawn}, {"clear", ai_clear}, {"count", ai_count}});
    into(L, "session", {{"skaters", skaters}, {"tag", tag}});
    into(L, "voice", {{"set_volume", voice_volume}});
    into(L, "chat", {{"send", chat_send}});
    into(L, "throwdowns", {{"state", throwdown_state}});
    into(L, "music", {{"current", current}, {"play", play}, {"pause", pause}, {"next", next}, {"previous", previous}});
    into(L, "progression", {{"get", progress}});
    into(L, "settings", {{"game", setting}, {"names", setting_names}});
    into(L, "input", {{"bind", bind}});
    into(L, "ui", {{"screen", screen}, {"open", open}, {"close", close}});
}

std::uint32_t combo_of(std::string_view name) {
    constexpr std::array<std::pair<std::string_view, std::uint32_t>, 16> pad{{
        {"up", 0x1}, {"down", 0x2}, {"left", 0x4}, {"right", 0x8}, {"start", 0x10}, {"back", 0x20}, {"l3", 0x40}, {"r3", 0x80},
        {"lb", 0x100}, {"rb", 0x200}, {"a", 0x1000}, {"b", 0x2000}, {"x", 0x4000}, {"y", 0x8000}, {"lt", 0x10000}, {"rt", 0x20000},
    }};
    std::array<std::uint64_t, 4> keys{};
    std::uint32_t buttons = 0;
    bool keyboard = false, controller = false;
    std::size_t start = 0;
    while (start <= name.size()) {
        const auto end = std::min(name.find('+', start), name.size());
        std::string part(name.substr(start, end - start));
        for (auto& c : part) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
        start = end + 1;
        if (part.starts_with("pad.")) {
            const auto button = std::string_view(part).substr(4);
            const auto found = std::find_if(pad.begin(), pad.end(), [&](const auto& p) { return p.first == button; });
            if (found == pad.end()) return 0;
            buttons |= found->second;
            controller = true;
            continue;
        }
        unsigned key = 0;
        if (part == "ctrl") key = 0x11;
        else if (part == "shift") key = 0x10;
        else if (part == "alt") key = 0x12;
        else if (part == "space") key = 0x20;
        else if (part.size() == 1 && ((part[0] >= 'a' && part[0] <= 'z') || (part[0] >= '0' && part[0] <= '9')))
            key = part[0] >= 'a' ? static_cast<unsigned>(part[0] - 32) : static_cast<unsigned>(part[0]);
        else if (part.size() >= 2 && part[0] == 'f') {
            unsigned n = 0;
            if (std::from_chars(part.data() + 1, part.data() + part.size(), n).ec != std::errc{} || n < 1 || n > 12) return 0;
            key = 0x70 + n - 1;
        } else return 0;
        keys[key / 64] |= std::uint64_t{1} << (key % 64);
        keyboard = true;
    }
    if (keyboard == controller) return 0; // a key or a pad combo, not both, and not nothing
    if (controller) return buttons;
    std::uint32_t value = 0x40000000u; // as controller_bindings.h's keyboard_binding_from_keys
    unsigned count = 0;
    const auto id_of = [](unsigned key) -> unsigned {
        if (key >= '0' && key <= '9') return key - '0' + 1;
        if (key >= 'A' && key <= 'Z') return key - 'A' + 11;
        if (key == 0x20) return 37;
        if (key >= 0x70 && key <= 0x7b) return key - 0x70 + 38;
        if (key == 0x11) return 50;
        if (key == 0x10) return 51;
        if (key == 0x12) return 52;
        return 0;
    };
    unsigned single = 0;
    for (unsigned key = 0; key < 256; ++key) {
        if (!(keys[key / 64] & (std::uint64_t{1} << (key % 64)))) continue;
        if (count == 5) return 0;
        single = key;
        ++count;
    }
    if (count == 1) return 0x80000000u | single;
    // Chord ids go in ascending order.
    std::vector<unsigned> ids;
    for (unsigned key = 0; key < 256; ++key)
        if (keys[key / 64] & (std::uint64_t{1} << (key % 64))) ids.push_back(id_of(key));
    std::sort(ids.begin(), ids.end());
    for (std::size_t i = 0; i < ids.size(); ++i) value |= ids[i] << (i * 6);
    return value;
}
}
