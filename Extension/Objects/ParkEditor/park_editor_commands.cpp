#include "Extension/Console/commands.h"
#include "park_editor_runtime.h"

namespace dingosdk::console {
void register_park_editor_commands(Commands &registry) {
    auto enabled = variable("parkeditor", "Open the fullscreen park editor", Group::objects,
                            argument("0|1", Type::boolean));
    enabled.inspect = [](const Model &m) {
        return boolean_state(m.debug.park_editor ||
                                 (m.editor.available && m.debug.camera_available && m.debug.ui_available),
                             m.debug.park_editor, m.editor.status);
    };
    enabled.run = [](const Model &, const Values &args, const Output &) {
        request_debug(overlay::DebugAction::set_park_editor, std::get<bool>(args[0]));
    };
    registry.add(std::move(enabled));
    // Layout edits need the open editor; park mod operations (load a preset,
    // save/create a mod) also run from the overlay's PARK MODS tab.
    for (const std::string operation : {"place", "move", "delete", "new", "undo", "redo", "mod-open", "mod-load",
                                        "mod-save", "mod-create", "mod-details", "mod-convert", "mods-refresh"}) {
        const bool object = operation == "place" || operation == "move" || operation == "delete";
        const bool named = operation == "mod-open" || operation == "mod-load" || operation == "mod-save" ||
                           operation == "mod-convert" || operation == "mod-details";
        const bool described = operation == "mod-create" || operation == "mod-details";
        std::vector<Argument> args{argument("map"), argument("generation", Type::unsigned_integer),
                                   argument("revision", Type::unsigned_integer)};
        if (operation == "place")
            args.push_back(argument("item"));
        else if (operation == "move" || operation == "delete")
            args.push_back(argument("id", Type::unsigned_integer));
        else if (named)
            args.push_back(argument(operation == "mod-convert" ? "park" : "mod"));
        if (described)
            for (const auto *name : {"name", "author", "version", "description"})
                args.push_back(argument(name));
        if (operation == "place" || operation == "move")
            for (const auto *name : {"x", "y", "z", "qx", "qy", "qz", "qw"}) {
                auto value = argument(name, Type::number);
                value.minimum = -100000;
                value.maximum = 100000;
                args.push_back(value);
            }
        if (operation == "place" || operation == "move") {
            auto value = argument("scale", Type::number);
            value.minimum = .01;
            value.maximum = 100;
            args.push_back(value);
        }
        auto command =
            action("editor " + operation, "Park editor " + operation, Group::objects, std::move(args));
        command.inspect = [object](const Model &m) {
            return State{(!object || m.debug.park_editor) && m.editor.available && !m.editor.busy && !m.editor.failed,
                         {},
                         "Park editor is unavailable or busy.",
                         {},
                         false};
        };
        // From a script (RESKATE_STARTUP_COMMANDS, a test harness), which cannot see the overlay's
        // selection: map "-" is the current level's map, and generation and revision 0 0 are the
        // editor's current ones. The overlay passes its own, so a stale selection is still refused.
        command.run = [operation, named, described](const Model &m, const Values &values, const Output &output) {
            profile::PlacedObject object;
            std::string argument_value;
            std::vector<std::string> texts;
            std::size_t next = 3;
            if (operation == "place")
                object.item = std::get<std::string>(values[next++]);
            else if (operation == "move" || operation == "delete")
                object.id = std::get<std::uint64_t>(values[next++]);
            else if (named)
                argument_value = std::get<std::string>(values[next++]);
            if (described)
                for (int i = 0; i < 4; ++i)
                    texts.push_back(std::get<std::string>(values[next++]));
            if (operation == "place" || operation == "move") {
                for (unsigned i = 0; i < 3; ++i)
                    object.position[i] = static_cast<float>(std::get<double>(values[4 + i]));
                for (unsigned i = 0; i < 4; ++i)
                    object.rotation[i] = static_cast<float>(std::get<double>(values[7 + i]));
                object.scale = static_cast<float>(std::get<double>(values[11]));
            }
            auto map = std::get<std::string>(values[0]);
            auto generation = std::get<std::uint64_t>(values[1]), revision = std::get<std::uint64_t>(values[2]);
            if (map == "-") map = m.editor.map;
            if (generation == 0 && revision == 0) {
                generation = m.editor.generation;
                revision = m.editor.revision;
            }
            output(edit_local_park(operation, map, generation, revision, argument_value, object, texts));
        };
        registry.add(std::move(command));
    }
}
} // namespace dingosdk::console
