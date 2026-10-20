// Plugin throwdown modes without the game: the registry's checks and news, and the
// pure helpers the native setup page uses.
#include "Extension/Throwdowns/custom_modes.h"
#include "Extension/UI/NativeMenu/native_menu_presenters.h"
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
using namespace dingosdk::multiplayer;
void check(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }

custom_throwdowns::Mode one_up() {
    custom_throwdowns::Mode mode;
    mode.plugin = "smokey";
    mode.id = "oneup";
    mode.name = "1-UP";
    mode.description = "Beat the last total.";
    mode.players = 9; // more than max: clamped
    mode.timer = custom_throwdowns::Row{custom_throwdowns::Row::Kind::slider, "timer", "Turn", 10, 120, 10, 20};
    mode.rows.push_back({custom_throwdowns::Row::Kind::choice, "tricks", "Tricks", 0, 0, 1, 5, {"All", "Flips"}});
    mode.rows.push_back({custom_throwdowns::Row::Kind::button, "area", "Area", 0, 0, 1, 0, {}, "Set area"});
    return mode;
}

void modes_are_checked_and_listed() {
    check(custom_throwdowns::set_mode(one_up()).empty(), "1-UP registers");
    auto bad = one_up();
    bad.id = "a/b";
    check(!custom_throwdowns::set_mode(bad).empty(), "an id with / is refused");
    bad = one_up();
    bad.rows.push_back({custom_throwdowns::Row::Kind::slider, "players", "Players", 1, 4});
    check(!custom_throwdowns::set_mode(bad).empty(), "a row may not be called players");
    bad = one_up();
    bad.rows.push_back({custom_throwdowns::Row::Kind::slider, "huge", "Huge", 0, 100000, 1});
    check(!custom_throwdowns::set_mode(bad).empty(), "a slider has at most 600 steps");
    bad = one_up();
    bad.replaces = "bowling";
    check(!custom_throwdowns::set_mode(bad).empty(), "replaces names a stock throwdown");
    const auto mode = custom_throwdowns::find("smokey/oneup");
    check(mode && mode->players == 6 && mode->rows[0].value == 1, "players and a choice are clamped");
    const auto settings = custom_throwdowns::settings("smokey/oneup");
    check(settings.at("players") == 6 && settings.at("timer") == 20 && settings.at("tricks") == 1 && !settings.contains("area"),
          "settings start at the defaults, without buttons");
}

void settings_and_news_reach_the_plugin() {
    const auto before = custom_throwdowns::revision();
    custom_throwdowns::set_setting("smokey/oneup", "players", 3);
    custom_throwdowns::set_setting("smokey/oneup", "players", 3); // unchanged: no news
    custom_throwdowns::News placed;
    placed.action = "placed";
    placed.at = std::array<float, 3>{1, 2, 3};
    placed.heading = 90;
    custom_throwdowns::post("smokey/oneup", placed);
    const auto news = custom_throwdowns::take("smokey");
    check(news.size() == 2, "one setting and the flag");
    check(news[0].mode == "oneup" && news[0].action == "setting" && news[0].row == "players" && news[0].value == 3, "the setting");
    check(news[1].action == "placed" && news[1].at && (*news[1].at)[2] == 3 && news[1].settings.at("players") == 3,
          "the flag, with the settings chosen");
    check(custom_throwdowns::take("smokey").empty() && custom_throwdowns::take("other").empty(), "taken once, by its own plugin");
    // An update keeps what was chosen; a reset goes back to the defaults.
    auto again = one_up();
    again.description = "New words.";
    check(custom_throwdowns::set_mode(again).empty() && custom_throwdowns::revision() != before, "an update");
    check(custom_throwdowns::settings("smokey/oneup").at("players") == 3, "keeps the players chosen");
    custom_throwdowns::reset_settings("smokey/oneup");
    check(custom_throwdowns::settings("smokey/oneup").at("players") == 6, "a reset starts over");
    custom_throwdowns::remove_plugin("smokey");
    check(custom_throwdowns::modes().empty() && !custom_throwdowns::find("smokey/oneup"), "a plugin's modes go with it");
}

struct Presenter { std::uintptr_t blueprint{}; struct { std::uintptr_t record{}; std::uint64_t handle{}; } data; };
void setup_page_helpers() {
    using menu_data::native_slider_choice, menu_data::native_slider_position;
    check(native_slider_choice(0.f, 10, 120, 10) == 10 && native_slider_choice(1.f, 10, 120, 10) == 120, "slider ends");
    check(native_slider_choice(native_slider_position(70, 10, 120), 10, 120, 10) == 70, "a choice survives the round trip");
    check(native_slider_choice(.52f, 1, 7, 1) == 4, "rounds to the nearest step");
    check(native_slider_choice(std::nanf(""), 1, 7, 1) == 1, "a broken value is the minimum");
    // Two 32-byte rows with a presenter at 8: the same presenters need no publish; a new one keeps the rest.
    std::vector<std::byte> live(64), saved(64);
    const Presenter a{1, {0, 10}}, b{2, {0, 20}};
    std::memcpy(live.data() + 8, &a, sizeof a);
    std::memcpy(saved.data() + 8, &a, sizeof a);
    live[0] = std::byte{7}; // native focus state
    check(!menu_data::remount_presenters<Presenter>(live, saved, 32, 8), "same presenters: nothing to publish");
    std::memcpy(saved.data() + 40, &b, sizeof b);
    const auto next = menu_data::remount_presenters<Presenter>(live, saved, 32, 8);
    check(next && (*next)[0] == std::byte{7}, "a new presenter keeps the live focus bytes");
    check(menu_data::remount_presenters<Presenter>(live, std::span(saved).first(32), 32, 8)->size() == 32, "another count takes the saved rows");
}
} // namespace

int main() {
    try {
        modes_are_checked_and_listed();
        settings_and_news_reach_the_plugin();
        setup_page_helpers();
        std::cout << "custom throwdown checks passed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAILED: " << e.what() << "\n";
        return 1;
    }
}
