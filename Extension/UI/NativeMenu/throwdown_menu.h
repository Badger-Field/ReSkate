#pragma once
#include <cstdint>

// The game's own Throwdowns page as a logo grid (SmokeyDawg58's picker, whose
// assets ReSkate carries): the stock throwdowns, then the plugins' modes
// (custom_modes.h), and the Spot Battle setup page relabelled for the one
// being set up.
namespace dingosdk::multiplayer {
// Client UI thread, from the native menu tick.
void tick_native_throwdown_menu(std::uintptr_t base, bool loading) noexcept;
// Before a level unloads: false if the page could not be restored.
bool release_native_throwdown_menu(std::uintptr_t base) noexcept;
}
