#pragma once
#include "plugin_game.h"
#include <cstdint>

// The wider set's game in the real game (plugin_game.h): each part through the function ReSkate's
// own console or menu uses, under the same rules. Client thread.
namespace dingosdk::plugins {
Game& real_game(std::uintptr_t image_base);
}
