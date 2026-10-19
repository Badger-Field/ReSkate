#pragma once
#include <string>
#include <string_view>

// The breakable deck packs (the Board_Break_<deck> mods; Engine/Vfs/mod_catalog.h): which deck every
// board has, its name for people and how far apart its wheels sit. Breaking a board is a plugin's
// (the board-break mod, through reskate.board); this is what every board needs whichever plugins run,
// as board_pose draws it: the deck's four stretches out of sight, the baseplates on the deck and the
// wheels at the deck's own spacing.
namespace dingosdk::breakable_deck {
// The deck in the game, or empty.
std::string in_play() noexcept;
// A deck's name in the menus: its deck.ini's `label`, else its folder's name, in words.
std::string label(std::string_view deck);
// How far apart the deck's wheels sit, as a standard board's are (deck.ini `axle`); 1 without one.
float axle(std::string_view deck);
// Puts `wanted` in the game (by name, by number, or "next"): the mods are applied again and the level
// loads, at the next client tick. What happens, for people.
std::string choose(std::string wanted);
// The decks by number for the console ("1 Standard, 2 Wide (not installed), ...").
std::string list();
// Each client tick.
void tick() noexcept;
}
