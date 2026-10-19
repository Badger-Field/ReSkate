#include "custom_modes.h"
#include <algorithm>
#include <atomic>
#include <mutex>

namespace dingosdk::multiplayer::custom_throwdowns {
namespace {
struct Registry {
    std::mutex mutex;
    std::vector<Mode> modes;
    std::map<std::string, std::map<std::string, int>, std::less<>> settings;
    std::map<std::string, std::vector<News>, std::less<>> news;
    std::atomic<std::uint64_t> revision{1};
};
Registry& registry() { static auto* r = new Registry; return *r; }
std::map<std::string, int> defaults(const Mode& mode) {
    std::map<std::string, int> result{{"players", mode.players}};
    if (mode.timer) result["timer"] = mode.timer->value;
    for (const auto& row : mode.rows)
        if (row.kind != Row::Kind::button) result[row.id] = row.value;
    return result;
}
std::string plugin_of(std::string_view key) {
    const auto at = key.find('/');
    return std::string(at == std::string_view::npos ? key : key.substr(0, at));
}
} // namespace

std::string set_mode(Mode mode) {
    if (mode.plugin.empty() || mode.id.empty() || mode.id.size() > 32 || mode.id.find('/') != std::string::npos)
        return "a mode needs an id of 1 to 32 characters, without /";
    if (mode.name.empty() || mode.name.size() > 32) return "a mode needs a name of 1 to 32 characters";
    if (mode.description.size() > 240) return "a description is at most 240 characters";
    if (mode.min_players < 1 || mode.max_players > 10 || mode.min_players > mode.max_players)
        return "players are 1 to 10, min no more than max";
    mode.players = std::clamp(mode.players, mode.min_players, mode.max_players);
    if (mode.rows.size() > 8) return "a mode has at most 8 rows";
    std::vector<std::string> ids{"players", "timer"};
    for (auto& row : mode.rows) {
        if (row.id.empty() || std::find(ids.begin(), ids.end(), row.id) != ids.end())
            return "each row needs its own id (not players or timer)";
        ids.push_back(row.id);
        if (row.caption.empty() || row.caption.size() > 32) return "a row's caption is 1 to 32 characters";
        if (row.kind == Row::Kind::choice) {
            if (row.options.empty() || row.options.size() > 12) return "a choice has 1 to 12 options";
            row.value = std::clamp(row.value, 0, static_cast<int>(row.options.size()) - 1);
        }
    }
    const auto slider = [](Row& row) {
        if (row.step < 1 || row.minimum > row.maximum || (row.maximum - row.minimum) / row.step > 600) return false;
        row.value = std::clamp(row.value, row.minimum, row.maximum);
        return true;
    };
    if (mode.timer && !slider(*mode.timer)) return "the timer runs min to max in at most 600 steps";
    for (auto& row : mode.rows)
        if (row.kind == Row::Kind::slider && !slider(row)) return "a slider runs min to max in at most 600 steps";
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    const auto key = mode.key();
    const auto found = std::find_if(r.modes.begin(), r.modes.end(), [&](const Mode& m) { return m.key() == key; });
    if (found == r.modes.end() && r.modes.size() >= 32) return "32 modes are registered already";
    // An update keeps the values already chosen for rows it still has.
    auto values = defaults(mode);
    if (found != r.modes.end())
        for (auto& [row, value] : values)
            if (const auto kept = r.settings[key].find(row); kept != r.settings[key].end()) value = kept->second;
    r.settings[key] = std::move(values);
    if (found != r.modes.end()) *found = std::move(mode);
    else r.modes.push_back(std::move(mode));
    r.revision.fetch_add(1);
    return {};
}
void remove_mode(std::string_view plugin, std::string_view id) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    if (std::erase_if(r.modes, [&](const Mode& m) { return m.plugin == plugin && m.id == id; })) r.revision.fetch_add(1);
}
void remove_plugin(std::string_view plugin) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    if (std::erase_if(r.modes, [&](const Mode& m) { return m.plugin == plugin; })) r.revision.fetch_add(1);
    if (const auto found = r.news.find(plugin); found != r.news.end()) r.news.erase(found);
}
std::vector<Mode> modes() {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    return r.modes;
}
std::optional<Mode> find(std::string_view key) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    for (const auto& mode : r.modes)
        if (mode.key() == key) return mode;
    return std::nullopt;
}
std::uint64_t revision() noexcept { return registry().revision.load(); }

std::map<std::string, int> settings(std::string_view key) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    const auto found = r.settings.find(key);
    return found == r.settings.end() ? std::map<std::string, int>{} : found->second;
}
void reset_settings(std::string_view key) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    for (const auto& mode : r.modes)
        if (mode.key() == key) r.settings[std::string(key)] = defaults(mode);
}
void set_setting(std::string_view key, std::string_view row, int value) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    auto& values = r.settings[std::string(key)];
    const auto found = values.find(std::string(row));
    if (found != values.end() && found->second == value) return;
    values[std::string(row)] = value;
    const auto at = key.find('/');
    auto& queue = r.news[plugin_of(key)];
    if (queue.size() >= 64) queue.erase(queue.begin());
    queue.push_back({std::string(key.substr(at + 1)), "setting", std::string(row), value, values});
}
void post(std::string_view key, News news) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    const auto at = key.find('/');
    news.mode = std::string(at == std::string_view::npos ? key : key.substr(at + 1));
    if (const auto found = r.settings.find(key); found != r.settings.end() && news.settings.empty())
        news.settings = found->second;
    auto& queue = r.news[plugin_of(key)];
    if (queue.size() >= 64) queue.erase(queue.begin());
    queue.push_back(std::move(news));
}
std::vector<News> take(std::string_view plugin) {
    auto& r = registry();
    std::lock_guard lock(r.mutex);
    const auto found = r.news.find(plugin);
    if (found == r.news.end()) return {};
    return std::exchange(found->second, {});
}
} // namespace dingosdk::multiplayer::custom_throwdowns
