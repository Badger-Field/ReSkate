#pragma once
#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

// Throwdown modes that plugins add to the game's Throwdowns page. A mode is a
// card on the picker, the game's own Spot Battle setup page relabelled with the
// mode's rows, and its native start flag: the plugin is told where the flag
// went and runs the mode itself. Thread-safe; the menu reads it on the UI
// thread, plugins write it from theirs.
namespace dingosdk::multiplayer::custom_throwdowns {
struct Row {
    enum class Kind { slider, choice, button };
    Kind kind{};
    std::string id, caption;
    int minimum{}, maximum{}, step{1}, value{}; // slider; choice: value is the option index
    std::vector<std::string> options;           // choice
    std::string text;                           // button: its label
};
struct Mode {
    std::string plugin, id, name, description;
    // The picker logo: a texture or image asset the plugin's mod adds, by path
    // (a bare name is a Throwdown_ModeSelect_ContentResources record). Empty:
    // the Spot Battle art.
    std::string art;
    // The mod's flag (InstanceId ReSkate.<flag>). Empty: Spot Battle's own.
    std::string flag;
    int min_players = 1, max_players = 6, players = 4;
    std::optional<Row> timer; // the setup page's Timer row, when the mode has one
    std::vector<Row> rows;
    std::string key() const { return plugin + "/" + id; }
};
// What happened to a plugin's mode: opened (its setup page), setting (a row
// changed), button, placed (the flag is down: `at` and `heading`), start (the
// host held Start on the waiting card), leave, cancelled (Back out of setup or
// placement). `settings` holds players, timer and every row by id.
struct News {
    std::string mode, action, row;
    int value{};
    std::map<std::string, int> settings;
    std::optional<std::array<float, 3>> at;
    float heading{}; // degrees
};

// Empty when accepted, else why not.
std::string set_mode(Mode mode);
void remove_mode(std::string_view plugin, std::string_view id);
void remove_plugin(std::string_view plugin);
std::vector<Mode> modes();
std::optional<Mode> find(std::string_view key);
// Changes whenever a mode is set or removed.
std::uint64_t revision() noexcept;

// The setup page's values: players, timer and each row's value by its id.
// Reset to the mode's defaults when its setup opens.
std::map<std::string, int> settings(std::string_view key);
void reset_settings(std::string_view key);
// Records a new value; a change is also news for the plugin.
void set_setting(std::string_view key, std::string_view row, int value);
void post(std::string_view key, News news);
std::vector<News> take(std::string_view plugin);
} // namespace dingosdk::multiplayer::custom_throwdowns
