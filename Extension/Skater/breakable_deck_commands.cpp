#include "breakable_deck.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Extension/Console/commands.h"

// Which breakable deck every board has; the Mods page's deck card sends it too.
namespace dingosdk::console {
void register_breakable_deck_commands(Commands &registry) {
    auto which = argument("deck", Type::text, true);
    which.complete = [](const Model &, auto) {
        auto names = mods::installed_board_break_decks();
        names.insert(names.begin(), "next");
        return names;
    };
    auto deck = action("boarddeck", "Which breakable deck every board has, by its name or its number, or next; the level loads again with it",
                       Group::gameplay, {std::move(which)});
    deck.run = [](const Model &, const Values &args, const Output &out) {
        if (!args.empty() && !std::get<std::string>(args[0]).empty()) {
            out(breakable_deck::choose(std::get<std::string>(args[0])));
            return;
        }
        out("In the game: " + breakable_deck::label(breakable_deck::in_play()) + ". Decks: " + breakable_deck::list() + ".");
    };
    registry.add(std::move(deck));
}
}
