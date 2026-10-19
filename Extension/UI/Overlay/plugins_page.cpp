#include "skate_menu_internal.h"
#include "Extension/Plugins/plugin_ui.h"

#include <algorithm>
#include <map>
#include <string>
#include <vector>

// The ADDONS page's PLUGINS tab: each plugin's panels (reskate.menu.panel), drawn with the menu's
// own widgets. Plugins build them on the client thread ten times a second while this tab shows
// (plugin_ui.h), and what the player does goes back to them as actions.
namespace dingosdk::overlay::menu {
namespace {
// What the player last set on a widget, shown until the plugin's next build agrees, or a second
// passes: the plugin's answer comes a few frames later.
struct Edit {
    bool on{};
    float value{};
    int chosen{};
    double at{};
};
std::map<std::string, Edit>& edits() {
    static auto* value = new std::map<std::string, Edit>;
    return *value;
}
}

void plugins_addon(SkateMenu& menu) {
    plugin_ui::note_panels_shown();
    const auto panels = plugin_ui::latest_panels();
    const double now = ImGui::GetTime();
    std::erase_if(edits(), [&](const auto& item) { return now - item.second.at > 1.0; });
    if (!panels || std::none_of(panels->begin(), panels->end(), [](const auto& p) { return !p.screen; })) {
        begin_card(menu, "plugins-none", "PLUGINS");
        note("No plugin has a panel here. A plugin adds one with reskate.menu.panel (it needs the \"menu\" permission). "
             "plugin list in the console shows every plugin.");
        end_card();
        return;
    }
    int index = 0;
    for (const auto& panel : *panels) {
        if (panel.screen) continue; // a full-screen screen (plugin_overlay.cpp), not a panel
        ImGui::PushID(index++);
        begin_card(menu, "plugin-panel", panel.title.c_str(), panel.plugin.c_str());
        int item = 0;
        for (const auto& w : panel.widgets) {
            ImGui::PushID(item++);
            const auto key = panel.plugin + '\n' + panel.title + '\n' + w.id;
            auto& all = edits();
            const auto edit = all.find(key);
            const auto send = [&](plugin_ui::Action action, Edit remembered) {
                action.plugin = panel.plugin;
                action.panel = panel.title;
                action.widget = w.id;
                plugin_ui::act(std::move(action));
                remembered.at = now;
                all[key] = remembered;
            };
            using Kind = plugin_ui::Widget::Kind;
            switch (w.kind) {
            case Kind::heading: section(menu, w.text.c_str()); break;
            case Kind::label: ImGui::TextWrapped("%s", w.text.c_str()); break;
            case Kind::separator: ImGui::Separator(); break;
            case Kind::button:
                if (ImGui::Button(w.text.c_str(), ImVec2(-FLT_MIN, px(34)))) send({}, {});
                break;
            case Kind::toggle: {
                bool on = edit != all.end() ? edit->second.on : w.on;
                if (edit != all.end() && edit->second.on == w.on) all.erase(edit);
                if (toggle_row(menu, w.text.c_str(), "", on)) {
                    plugin_ui::Action action;
                    action.on = on;
                    send(action, {on});
                }
                break;
            }
            case Kind::slider: {
                float value = edit != all.end() ? edit->second.value : w.value;
                field(menu, w.text.c_str());
                if (slider_float("##slider", &value, w.minimum, w.maximum, "%.2f", ImGuiSliderFlags_AlwaysClamp)) {
                    plugin_ui::Action action;
                    action.value = value;
                    send(action, {false, value});
                }
                break;
            }
            case Kind::dropdown: {
                int selected = (edit != all.end() ? edit->second.chosen : w.chosen) - 1;
                if (edit != all.end() && edit->second.chosen == w.chosen) all.erase(edit);
                if (option_row(menu, w.text.c_str(), "", selected, w.choices)) {
                    plugin_ui::Action action;
                    action.chosen = selected + 1;
                    send(action, {false, 0, selected + 1});
                }
                break;
            }
            case Kind::bar:
                ImGui::ProgressBar((w.value - w.minimum) / (w.maximum - w.minimum), ImVec2(-FLT_MIN, px(22)), w.text.c_str());
                break;
            }
            ImGui::PopID();
        }
        end_card();
        ImGui::PopID();
    }
}
}
