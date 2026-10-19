#pragma once
#include <cstdint>
#include <optional>

// skate.'s score HUD as values (ScoringHUDViewModel, read through the game's UI models), what
// trick_watch.h turns into tricks. Moved from Domination (PR 279).
namespace dingosdk::tricks {
struct ScoreHud {
    bool action{}; // a trick is going on
    bool slam{};
    float line_score{}, multiplier{};
};
// Client thread. Empty while there is no score HUD model (the menu, a level loading), or when this
// game build's model has none of the fields; the model is looked for at most once a second.
std::optional<ScoreHud> read_score_hud(std::uintptr_t image_base) noexcept;
}
