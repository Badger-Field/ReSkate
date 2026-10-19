#include "plugin_overlay.h"
#include "Extension/Plugins/plugin_camera.h"
#include "Extension/Plugins/plugin_ui.h"
#include "Extension/UI/Overlay/overlay_internal.h"
#include <algorithm>
#include <imgui_internal.h>
#include <memory>

namespace dingosdk::overlay {
namespace {
// Render thread: the layers taken by plugins_pending, drawn by draw_plugins.
std::shared_ptr<const std::vector<plugin_ui::Layer>>& frame() {
    static auto* value = new std::shared_ptr<const std::vector<plugin_ui::Layer>>;
    return *value;
}
ImVec2 at(const plugin_ui::Vec3& p) { return ImVec2(p[0], p[1]); }
ImU32 shadow_of(ImU32 colour) {
    const auto alpha = static_cast<ImU32>(((colour >> IM_COL32_A_SHIFT) & 0xff) * 0.75f);
    return IM_COL32(0, 0, 0, alpha);
}
void text(ImDrawList* draw, ImFont* font, float k, ImVec2 where, const plugin_ui::Shape& shape) {
    const float size = shape.style.size * k;
    const auto width = font->CalcTextSizeA(size, FLT_MAX, 0.0f, shape.text.c_str()).x;
    if (shape.style.align == 1) where.x -= width * 0.5f;
    else if (shape.style.align == 2) where.x -= width;
    if (shape.style.shadow) {
        const float offset = std::max(1.0f, size * 0.06f);
        draw->AddText(font, size, ImVec2(where.x + offset, where.y + offset), shadow_of(shape.style.colour), shape.text.c_str());
    }
    draw->AddText(font, size, where, shape.style.colour, shape.text.c_str());
}
void circle(ImDrawList* draw, ImVec2 centre, float radius, const plugin_ui::Style& style, float k) {
    if (radius <= 0) return;
    if (style.fill) draw->AddCircleFilled(centre, radius, style.colour);
    else draw->AddCircle(centre, radius, style.colour, 0, style.thickness * k);
}
}

// The screen Esc closed, until its plugin's next build no longer has it open.
static std::string& closed_screen() {
    static auto* value = new std::string;
    return *value;
}
static const plugin_ui::Panel* open_screen() {
    const auto panels = plugin_ui::latest_panels();
    static std::shared_ptr<const std::vector<plugin_ui::Panel>> held;
    held = panels;
    if (!held) return nullptr;
    for (const auto& panel : *held)
        if (panel.screen) {
            if (closed_screen() == panel.plugin + '\n' + panel.title) return nullptr;
            return &panel;
        }
    closed_screen().clear();
    return nullptr;
}

// Called before the overlay's ImGui context exists or is made current, too: its size is read from
// the context itself (what its last frame had), never through ImGui's current one.
bool plugins_pending() {
    detail::state().plugin_screen_visible.store(open_screen() != nullptr);
    if (const auto* context = detail::state().context) {
        const auto display = context->IO.DisplaySize;
        if (display.x > 0 && display.y > 0) plugin_ui::set_display(display.x, display.y);
    }
    frame() = plugin_ui::latest_layers();
    return frame() && !frame()->empty();
}

void draw_plugin_screen() {
    const auto* screen = open_screen();
    if (!screen) return;
    const auto display = ImGui::GetIO().DisplaySize;
    const float k = std::max(1.0f, display.y / 1080.0f);
    auto& s = detail::state();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(display);
    ImGui::SetNextWindowBgAlpha(0.94f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(64 * k, 48 * k));
    const auto flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings;
    if (ImGui::Begin("##plugin-screen", nullptr, flags)) {
        ImGui::SetWindowFontScale(1.25f * k);
        if (s.menu.heading) ImGui::PushFont(s.menu.heading);
        ImGui::TextUnformatted(screen->title.c_str());
        if (s.menu.heading) ImGui::PopFont();
        ImGui::TextDisabled("%s  -  Esc closes it", screen->plugin.c_str());
        ImGui::Separator();
        ImGui::Spacing();
        int item = 0;
        for (const auto& w : screen->widgets) {
            ImGui::PushID(item++);
            plugin_ui::Action action{screen->plugin, screen->title, w.id};
            bool acted = false;
            using Kind = plugin_ui::Widget::Kind;
            switch (w.kind) {
            case Kind::heading:
                ImGui::Spacing();
                if (s.menu.bold) ImGui::PushFont(s.menu.bold);
                ImGui::TextUnformatted(w.text.c_str());
                if (s.menu.bold) ImGui::PopFont();
                break;
            case Kind::label: ImGui::TextWrapped("%s", w.text.c_str()); break;
            case Kind::separator: ImGui::Separator(); break;
            case Kind::button: acted = ImGui::Button(w.text.c_str(), ImVec2(320 * k, 0)); break;
            case Kind::toggle: {
                bool on = w.on;
                if (ImGui::Checkbox(w.text.c_str(), &on)) {
                    action.on = on;
                    acted = true;
                }
                break;
            }
            case Kind::slider: {
                float value = w.value;
                ImGui::SetNextItemWidth(480 * k);
                if (ImGui::SliderFloat(w.text.c_str(), &value, w.minimum, w.maximum, "%.2f", ImGuiSliderFlags_AlwaysClamp)) {
                    action.value = value;
                    acted = true;
                }
                break;
            }
            case Kind::dropdown: {
                int selected = std::clamp(w.chosen - 1, 0, static_cast<int>(w.choices.size()) - 1);
                ImGui::SetNextItemWidth(480 * k);
                if (ImGui::BeginCombo(w.text.c_str(), w.choices.empty() ? "" : w.choices[static_cast<std::size_t>(selected)].c_str())) {
                    for (int i = 0; i < static_cast<int>(w.choices.size()); ++i)
                        if (ImGui::Selectable(w.choices[static_cast<std::size_t>(i)].c_str(), i == selected)) {
                            action.chosen = i + 1;
                            acted = true;
                        }
                    ImGui::EndCombo();
                }
                break;
            }
            }
            if (acted) plugin_ui::act(std::move(action));
            ImGui::PopID();
        }
        // Esc closes it at once here; its plugin hears screen_closed.
        if (ImGui::IsKeyPressed(ImGuiKey_Escape, false) && !ImGui::GetIO().WantTextInput) {
            plugin_ui::act({screen->plugin, screen->title, plugin_ui::close_widget});
            closed_screen() = screen->plugin + '\n' + screen->title;
            s.plugin_screen_visible.store(false);
        }
    }
    ImGui::End();
    ImGui::PopStyleVar();
}

void draw_plugins() {
    const auto layers = frame();
    if (!layers || layers->empty()) return;
    const auto display = ImGui::GetIO().DisplaySize;
    if (display.x <= 0 || display.y <= 0) return;
    const float k = display.y / 1080.0f;
    auto& s = detail::state();
    auto* font = s.menu.bold ? s.menu.bold : ImGui::GetFont();
    auto* draw = ImGui::GetBackgroundDrawList();
    const auto camera = plugin_camera::projector(display.x, display.y);
    using Kind = plugin_ui::Shape::Kind;
    for (const auto& layer : *layers)
        for (const auto& shape : layer.shapes) {
            const auto& style = shape.style;
            const auto& p = shape.points;
            switch (shape.kind) {
            case Kind::text: text(draw, font, k, at(p[0]), shape); break;
            case Kind::rect:
                if (style.fill) draw->AddRectFilled(at(p[0]), at(p[1]), style.colour, style.rounding * k);
                else draw->AddRect(at(p[0]), at(p[1]), style.colour, style.rounding * k, 0, style.thickness * k);
                break;
            case Kind::line: draw->AddLine(at(p[0]), at(p[1]), style.colour, style.thickness * k); break;
            case Kind::circle: circle(draw, at(p[0]), shape.radius, style, k); break;
            case Kind::poly: {
                std::vector<ImVec2> points;
                for (const auto& point : p) points.push_back(at(point));
                if (style.fill) draw->AddConvexPolyFilled(points.data(), static_cast<int>(points.size()), style.colour);
                else draw->AddPolyline(points.data(), static_cast<int>(points.size()), style.colour, ImDrawFlags_Closed, style.thickness * k);
                break;
            }
            case Kind::world_text:
                if (const auto on = camera ? camera->project(p[0]) : std::nullopt) text(draw, font, k, ImVec2((*on)[0], (*on)[1]), shape);
                break;
            case Kind::world_line: {
                const auto a = camera ? camera->project(p[0]) : std::nullopt;
                const auto b = camera ? camera->project(p[1]) : std::nullopt;
                if (a && b) draw->AddLine(ImVec2((*a)[0], (*a)[1]), ImVec2((*b)[0], (*b)[1]), style.colour, style.thickness * k);
                break;
            }
            case Kind::world_circle:
                if (const auto on = camera ? camera->project(p[0]) : std::nullopt)
                    circle(draw, ImVec2((*on)[0], (*on)[1]), std::min(shape.radius * camera->focal / (*on)[2], display.y), style, k);
                break;
            }
        }
}
}
