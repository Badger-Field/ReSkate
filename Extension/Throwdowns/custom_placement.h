#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <string_view>

namespace dingosdk::multiplayer::menu_data { class Context; }
// A plugin mode's start flag, through the game's own Spot Battle flow: its
// setup page, flag placement and waiting card. The temporary native queue
// owns only the flag and the waiting card; it never starts a Spot Battle. The
// pose goes to the plugin (custom_modes.h news), as do the waiting card's
// Start and Leave. Ordinary Spot Battles never enter this adapter.
namespace dingosdk::multiplayer::custom_throwdowns {
// Opens the setup page for this mode (a picker card's action). False while
// another flag of ours is being placed or is up.
bool open_setup(const std::string& key);
// The mode whose setup page is open (the menu relabels it), else empty.
std::string setup_mode();
// From the setup page opening until the flag is down or abandoned.
bool placement_active() noexcept;
// Placement, or a flag of ours that is up (its queue still registered).
bool registration_owned() noexcept;
bool owns_registration(std::uint32_t mmid) noexcept;
// Our flag is down and its waiting card up.
bool registration_waiting() noexcept;
// Our flag is down: from its accepted position until it is taken away. The
// waiting card's Start is offered from here on.
bool flag_down() noexcept;
// The InstanceId suffix of the owning mode's own flag, when it has one.
std::string flag_key();
// A queue we created; late events for it are not a new stock throwdown.
bool known_queue(std::uint32_t mmid) noexcept;
// The plugin is done with its flag (its match started, or it gave up).
void release(std::string_view plugin) noexcept;
// A fresh picker card supersedes a setup that never placed a flag.
void retire_unconfirmed() noexcept;
// UI thread, under the model write lock: navigation and handoff.
void tick(const menu_data::Context& context);

// From the throwdown hooks (native_throwdowns.cpp, throwdown_lab.cpp).
void observe_graph(std::uint32_t graph) noexcept;
void observe_created() noexcept;
void observe_queue(std::uint32_t mmid) noexcept;
void observe_position(std::uint32_t mmid, const std::array<float, 3>& at, float yaw_radians) noexcept;
void observe_exit(bool cancelling) noexcept;
void observe_destroyed(std::uint32_t mmid) noexcept;
// The waiting card's Start (ForceStart) and Leave (Destroy) on our queue.
void waiting_start() noexcept;
void waiting_left() noexcept;
// The level is unloading: its queues go with it.
void abandon() noexcept;
} // namespace dingosdk::multiplayer::custom_throwdowns
