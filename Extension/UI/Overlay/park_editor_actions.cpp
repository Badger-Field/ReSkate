#include "park_editor_internal.h"
#include <algorithm>
#include <iomanip>
#include <locale>
#include <sstream>

namespace dingosdk::overlay::park_editor_detail {
using namespace editor;
namespace {
std::string quoted(std::string_view text) {
    std::string out = "\"";
    for (auto c : text) {
        if (c == '"' || c == '\\')
            out += '\\';
        out += c;
    }
    return out + '"';
}
void select(ParkEditorUI &ui, const EditorObject &object, std::uint64_t revision) {
    ui.selection = {object.id};
    ui.selected = object.id;
    ui.position = object.position;
    ui.angles = angles(object.rotation);
    ui.scale = object.scale;
    ui.draft_revision = revision;
    ui.inspector_dirty = false;
    ui.placing.clear();
    ui.dragging = -1;
    ui.surface_confirm = false;
}
} // namespace
const EditorObject *selected_object(const ParkEditorUI &ui, const ParkEditorModel &model) {
    const auto it = std::find_if(model.objects.begin(), model.objects.end(),
                                 [&](const auto &row) { return row.id == ui.selected; });
    return it == model.objects.end() ? nullptr : &*it;
}
void refresh_selection(ParkEditorUI &ui, const ParkEditorModel &model) {
    if (ui.selection.empty() && ui.selected)
        ui.selection.push_back(ui.selected);
    std::erase_if(ui.selection, [&](auto id) {
        return std::none_of(model.objects.begin(), model.objects.end(),
                            [&](const auto &o) { return o.id == id; });
    });
    if (ui.selection.empty()) {
        ui.selected = 0;
        return;
    }
    const auto ids = ui.selection;
    const auto it = std::find_if(model.objects.begin(), model.objects.end(),
                                 [&](const auto &o) { return o.id == ids.back(); });
    select(ui, *it, model.revision);
    ui.selection = ids;
    if (ids.size() > 1) {
        ui.position = {};
        ui.angles = {};
        ui.scale = 1;
        for (const auto &object : model.objects)
            if (std::find(ids.begin(), ids.end(), object.id) != ids.end())
                ui.position = add(ui.position, object.position);
        ui.position = mul(ui.position, 1.0f / static_cast<float>(ids.size()));
    }
}
void choose(ParkEditorUI &ui, const ParkEditorModel &model, const EditorObject &object, bool extend) {
    if (!extend) {
        select(ui, object, model.revision);
        return;
    }
    const auto it = std::find(ui.selection.begin(), ui.selection.end(), object.id);
    if (it == ui.selection.end())
        ui.selection.push_back(object.id);
    else
        ui.selection.erase(it);
    if (ui.selection.empty())
        ui.selected = 0;
    refresh_selection(ui, model);
}
void capture_drag(ParkEditorUI &ui, const ParkEditorModel &model) {
    ui.drag_pivot = ui.position;
    ui.drag_orientation = rotation(ui.angles);
    ui.drag_scale = ui.scale;
    ui.drag_objects.clear();
    for (const auto &object : model.objects)
        if (object.id == ui.selected ||
            std::find(ui.selection.begin(), ui.selection.end(), object.id) != ui.selection.end())
            ui.drag_objects.push_back({object.id, object.position, object.rotation, object.scale});
}
void copy_selection(ParkEditorUI &ui, const ParkEditorModel &model) {
    std::vector<EditorObject> copied;
    for (const auto &object : model.objects)
        if (object.spawned && (object.id == ui.selected || std::find(ui.selection.begin(), ui.selection.end(),
                                                                     object.id) != ui.selection.end()))
            copied.push_back(object);
    if (copied.empty()) {
        ui.feedback = "Select objects to copy.";
    } else {
        ui.clipboard = std::move(copied);
        ui.paste_serial = 0;
        ui.feedback = "Copied " + std::to_string(ui.clipboard.size()) + " object(s). Ctrl+V to paste.";
    }
    ui.feedback_until = ImGui::GetTime() + 5;
}
void paste_objects(ParkEditorUI &ui, const ParkEditorModel &model) {
    if (ui.clipboard.empty()) {
        ui.feedback = "Copy a selection with Ctrl+C first.";
        ui.feedback_until = ImGui::GetTime() + 5;
        return;
    }
    EditorPasteRequest request{model.generation, model.revision, ui.clipboard};
    const float offset =
        std::max(ui.snapping ? ui.grid : 1.0f, .05f) * static_cast<float>(ui.paste_serial + 1);
    for (auto &object : request.objects) {
        object.position[0] += offset;
        object.position[2] += offset;
    }
    const auto queue = paste_queue.load();
    if (!queue || !queue(request)) {
        ui.feedback = "Paste unavailable. Check object availability and the park's object limit.";
        ui.feedback_until = ImGui::GetTime() + 5;
        return;
    }
    ++ui.paste_serial;
    ui.paste_expected = std::move(request.objects);
    ui.paste_before.clear();
    for (const auto &object : model.objects)
        ui.paste_before.push_back(object.id);
    ui.pending = true;
    ui.queued_at = ImGui::GetTime();
    ui.queued_revision = model.revision;
    ui.queued_status = model.status;
    ui.placing.clear();
    ui.inspector_dirty = false;
    ui.feedback.clear();
}
bool delete_selection(ParkEditorUI &ui, const ParkEditorModel &model) {
    const auto queue = delete_queue.load();
    if (ui.selection.empty() || !queue || !queue({model.generation, model.revision, ui.selection})) {
        ui.feedback = "Delete unavailable. Wait for the current edit or check the status.";
        ui.feedback_until = ImGui::GetTime() + 5;
        return false;
    }
    ui.selection.clear();
    ui.selected = 0;
    ui.pending = true;
    ui.queued_at = ImGui::GetTime();
    ui.queued_revision = model.revision;
    ui.queued_status = model.status;
    ui.inspector_dirty = false;
    ui.feedback.clear();
    return true;
}
void select_pasted(ParkEditorUI &ui, const ParkEditorModel &model) {
    std::vector<std::uint64_t> ids;
    for (const auto &copy : ui.paste_expected) {
        const auto found = std::find_if(model.objects.begin(), model.objects.end(), [&](const auto &object) {
            return object.spawned && object.item == copy.item && object.position == copy.position &&
                   object.rotation == copy.rotation && object.scale == copy.scale &&
                   std::find(ui.paste_before.begin(), ui.paste_before.end(), object.id) ==
                       ui.paste_before.end() &&
                   std::find(ids.begin(), ids.end(), object.id) == ids.end();
        });
        if (found != model.objects.end())
            ids.push_back(found->id);
    }
    if (!model.failed && ids.size() == ui.paste_expected.size()) {
        ui.selection = std::move(ids);
        refresh_selection(ui, model);
    }
    ui.paste_expected.clear();
    ui.paste_before.clear();
}
bool preview(ParkEditorUI &ui, const Model &model, EditorPreviewAction action) {
    const auto queue = preview_queue.load();
    if (!queue)
        return false;
    const bool starting =
        action == EditorPreviewAction::begin_move || action == EditorPreviewAction::begin_place;
    if (!starting && !ui.preview_token)
        return false;
    if (action == EditorPreviewAction::update && ui.position == ui.preview_position &&
        ui.angles == ui.preview_angles && ui.scale == ui.preview_scale &&
        ImGui::GetTime() < ui.preview_heartbeat)
        return true;
    EditorPreviewRequest request;
    request.action = action;
    request.generation = model.editor.generation;
    request.revision = starting ? model.editor.revision : ui.preview_revision;
    request.token = starting ? ++ui.next_preview_token : ui.preview_token;
    request.sequence = starting ? 1 : ui.preview_sequence + 1;
    request.object = ui.selected;
    request.item = ui.placing;
    request.position = ui.position;
    request.rotation = rotation(ui.angles);
    request.scale = ui.scale;
    const bool placing = starting ? action == EditorPreviewAction::begin_place : ui.preview_place;
    if (!placing) {
        const auto q = ui.drag_orientation;
        const auto delta = multiplied(request.rotation, {-q[0], -q[1], -q[2], q[3]});
        const float ratio = ui.drag_scale > 0 ? ui.scale / ui.drag_scale : 1;
        for (const auto &object : ui.drag_objects)
            request.transforms.push_back(
                {object.id, add(ui.position, rotated(mul(sub(object.position, ui.drag_pivot), ratio), delta)),
                 multiplied(delta, object.rotation), object.scale * ratio});
    }
    if (!queue(request)) {
        ui.feedback = "Live preview unavailable. Wait for the current edit or check the status.";
        ui.feedback_until = ImGui::GetTime() + 5;
        return false;
    }
    ui.preview_token = request.token;
    ui.preview_sequence = request.sequence;
    ui.preview_revision = request.revision;
    ui.preview_place = placing;
    ui.preview_position = ui.position;
    ui.preview_angles = ui.angles;
    ui.preview_scale = ui.scale;
    ui.preview_heartbeat = ImGui::GetTime() + .25;
    if (action == EditorPreviewAction::commit || action == EditorPreviewAction::cancel) {
        ui.preview_token = 0;
        ui.pending = true;
        ui.queued_at = ImGui::GetTime();
        ui.queued_revision = model.editor.revision;
        ui.queued_status = model.editor.status;
    }
    return true;
}
void cancel_drag(ParkEditorUI &ui, const Model &model) {
    if (ui.preview_token)
        preview(ui, model, EditorPreviewAction::cancel);
    ui.preview_token = 0;
    ui.dragging = -1;
    ui.inspector_dirty = false;
    ui.draft_revision = 0;
    ui.pick_confirm = ui.asset_drag = ui.surface_confirm = ui.free_release = false;
    refresh_selection(ui, model.editor);
}
bool send(ParkEditorUI &ui, const Model &model, const CallbacksV3 &callbacks, std::string_view operation,
          std::string_view argument, const std::vector<std::string> &texts) {
    if (!callbacks.queue_console_command || ui.pending || model.editor.busy || model.editor.failed ||
        !model.editor.available)
        return false;
    std::ostringstream command;
    command.imbue(std::locale::classic());
    command << std::setprecision(9);
    command << "editor " << operation << ' ' << quoted(model.editor.map) << ' ' << model.editor.generation
            << ' ' << model.editor.revision;
    if (operation == "place")
        command << ' ' << quoted(argument);
    if (operation == "move" || operation == "delete")
        command << ' ' << ui.selected;
    if (operation == "mod-open" || operation == "mod-load" || operation == "mod-save" || operation == "mod-convert" ||
        operation == "mod-details")
        command << ' ' << quoted(argument);
    for (const auto &text : texts)
        command << ' ' << quoted(text);
    if (operation == "place" || operation == "move") {
        for (const auto v : ui.position)
            command << ' ' << v;
        for (const auto v : rotation(ui.angles))
            command << ' ' << v;
        command << ' ' << ui.scale;
    }
    std::array<char, 512> result{};
    const bool ok =
        callbacks.queue_console_command(callbacks.user, command.str().c_str(), result.data(), result.size());
    ui.feedback = result.data();
    ui.feedback_until = ImGui::GetTime() + 5;
    if (ok) {
        ui.feedback.clear();
        ui.pending = true;
        ui.queued_at = ImGui::GetTime();
        ui.queued_revision = model.editor.revision;
        ui.queued_status = model.editor.status;
        ui.inspector_dirty = false;
    }
    return ok;
}
} // namespace dingosdk::overlay::park_editor_detail
