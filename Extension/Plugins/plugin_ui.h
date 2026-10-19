#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <utility>
#include <string>
#include <vector>

// What plugins show, handed from the client thread (where plugins run) to the render thread (the
// overlay draws it): each plugin's shapes for this frame, and its menu panels on the ADDONS page's
// PLUGINS tab with what the player did in them on the way back. Header only, so the overlay and
// the plugin host share it without depending on each other.
namespace dingosdk::plugin_ui {
using Vec3 = std::array<float, 3>;

struct Style {
    std::uint32_t colour = 0xffffffff; // ImGui's IM_COL32 order: 0xAABBGGRR
    float size = 18;                   // text height, in pixels at 1080 lines
    float thickness = 2;
    bool fill{};
    float rounding{};
    int align{};                       // text: 0 left, 1 centre, 2 right of its point
    bool shadow = true;
    std::array<float, 4> uv{0, 0, 1, 1}; // pictures: the part of the picture drawn (u0, v0, u1, v1)
};
// Screen shapes are in pixels from the top left; world shapes in world metres, projected through
// the camera as the frame is drawn, and left out behind it.
struct Shape {
    // image: `text` is the picture's file, points[0] and [1] its corners; world_image: centred on
    // points[0], `radius` metres tall.
    enum class Kind { text, rect, line, circle, poly, world_text, world_line, world_circle, image, world_image } kind{Kind::text};
    std::vector<Vec3> points;
    float radius{};
    std::string text;
    Style style;
};
struct Layer {
    std::string plugin;
    std::vector<Shape> shapes;
};

struct Widget {
    // bar: a read-only gauge, `value` between `minimum` and `maximum` (an axis, a level).
    enum class Kind { heading, label, separator, button, toggle, slider, dropdown, bar } kind{Kind::label};
    std::string id, text;
    bool on{};
    float value{}, minimum{}, maximum{1};
    std::vector<std::string> choices;
    int chosen{};
};
struct Panel {
    std::string plugin, title;
    std::vector<Widget> widgets;
    bool screen{}; // a full-screen screen (reskate.ui.screen), not a PLUGINS tab panel
};
// The widget a full-screen screen's Esc sends.
inline constexpr char close_widget[] = "__close";
// A plugin's bind (reskate.input.bind) as the Binds page lists it: the plugin's own combo, and the
// one in use (the player's, when they recorded or cleared one; 0 is none).
struct Bind {
    std::string plugin, action;
    std::uint32_t own{}, combo{};
};
// The Binds page's change to one: a combo (0 clears it), or nullopt for the plugin's own again.
struct Rebind {
    std::string plugin, action;
    std::optional<std::uint32_t> combo;
};
// What the player did to a widget: pressed a button, or set a toggle, slider or dropdown.
struct Action {
    std::string plugin, panel, widget;
    bool on{};
    float value{};
    int chosen{};
};

namespace detail {
inline std::mutex mutex;
inline std::shared_ptr<const std::vector<Layer>> layers = std::make_shared<std::vector<Layer>>();
inline std::shared_ptr<const std::vector<Panel>> panels = std::make_shared<std::vector<Panel>>();
inline std::vector<Action> actions;
inline std::shared_ptr<const std::vector<Bind>> binds = std::make_shared<std::vector<Bind>>();
inline std::vector<Rebind> rebinds;
inline std::atomic<std::int64_t> panels_seen_ms{};
inline std::atomic<float> display_width{1920}, display_height{1080};
inline std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
}

// Client thread: this tick's shapes and panels. Render thread: the latest of each.
inline void publish_layers(std::vector<Layer> value) {
    auto shared = std::make_shared<const std::vector<Layer>>(std::move(value));
    std::lock_guard lock(detail::mutex);
    detail::layers = std::move(shared);
}
inline std::shared_ptr<const std::vector<Layer>> latest_layers() {
    std::lock_guard lock(detail::mutex);
    return detail::layers;
}
inline void publish_panels(std::vector<Panel> value) {
    auto shared = std::make_shared<const std::vector<Panel>>(std::move(value));
    std::lock_guard lock(detail::mutex);
    detail::panels = std::move(shared);
}
inline std::shared_ptr<const std::vector<Panel>> latest_panels() {
    std::lock_guard lock(detail::mutex);
    return detail::panels;
}
// Render thread: a widget changed. At most 64 wait, so a stuck client thread holds no more.
inline void act(Action action) {
    std::lock_guard lock(detail::mutex);
    if (detail::actions.size() < 64) detail::actions.push_back(std::move(action));
}
inline std::vector<Action> take_actions() {
    std::lock_guard lock(detail::mutex);
    return std::exchange(detail::actions, {});
}
// Client thread: every plugin's binds, whenever they change.
inline void publish_binds(std::vector<Bind> value) {
    auto shared = std::make_shared<const std::vector<Bind>>(std::move(value));
    std::lock_guard lock(detail::mutex);
    detail::binds = std::move(shared);
}
inline std::shared_ptr<const std::vector<Bind>> latest_binds() {
    std::lock_guard lock(detail::mutex);
    return detail::binds;
}
// Render thread: the player recorded, cleared or reset a plugin's bind.
inline void rebind(Rebind change) {
    std::lock_guard lock(detail::mutex);
    if (detail::rebinds.size() < 64) detail::rebinds.push_back(std::move(change));
}
inline std::vector<Rebind> take_rebinds() {
    std::lock_guard lock(detail::mutex);
    return std::exchange(detail::rebinds, {});
}
// Render thread, each frame the PLUGINS tab shows: panels are built only while it is open.
inline void note_panels_shown() { detail::panels_seen_ms.store(detail::now_ms(), std::memory_order_relaxed); }
inline bool panels_shown() { return detail::now_ms() - detail::panels_seen_ms.load(std::memory_order_relaxed) < 500; }
// Render thread, each frame: the game window's size, which plugins draw in.
inline void set_display(float width, float height) {
    detail::display_width.store(width, std::memory_order_relaxed);
    detail::display_height.store(height, std::memory_order_relaxed);
}
inline std::array<float, 2> display() {
    return {detail::display_width.load(std::memory_order_relaxed), detail::display_height.load(std::memory_order_relaxed)};
}
}
