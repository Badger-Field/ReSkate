#include "custom_placement.h"
#include "custom_modes.h"
#include "throwdown_lab.h"
#include "throwdown_relay.h"
#include "Extension/UI/NativeMenu/native_menu_data.h"
#include "Engine/Core/Log/logging.h"
#include <Windows.h>
#include <algorithm>
#include <cmath>
#include <mutex>
#include <optional>
#include <vector>

// The generic path of SmokeyDawg58's 1-UP flag adapter (one_up_placement.cpp in
// reskate-custom-mods): native settings, then the start flag, then the waiting card.
namespace dingosdk::multiplayer::custom_throwdowns {
namespace {
enum class Step { idle, open, setup, flag, placing, closing, waiting, cleanup };
struct Placement {
    std::mutex mutex;
    Step step{};
    std::string key;
    std::uint64_t until{}, changed{}, sent{};
    std::uint32_t mmid{};
    std::vector<std::uint32_t> queues;
    bool created{}, setup_seen{}, flag_seen{}, exited{}, cancelling{}, destroy_queued{}, destroyed{};
    bool handed_off{}, show_setup{}, told{};
    std::optional<std::array<float, 3>> at;
    float yaw{};
};
Placement& placement() { static auto* p = new Placement; return *p; }
void reset(Placement& p) {
    p.step = Step::idle; p.key.clear(); p.at.reset(); p.yaw = 0; p.mmid = 0; p.sent = 0;
    p.created = p.setup_seen = p.flag_seen = p.exited = p.cancelling = p.destroy_queued = p.destroyed = false;
    p.handed_off = p.show_setup = p.told = false;
}
void cleanup(Placement& p, std::uint64_t now) {
    if (p.step != Step::cleanup) p.sent = 0;
    if (p.mmid && p.step == Step::waiting) {
        ThrowdownLocalAction closed{ThrowdownLocalAction::Kind::destroy_requested};
        closed.mmid = p.mmid;
        throwdown_relay_local(std::move(closed));
    }
    p.cancelling = true; p.step = Step::cleanup; p.until = now + 15000;
}
// News for the plugin, once per flag: cancelled before the flag was down.
void tell(Placement& p, std::string action) {
    if (p.key.empty() || p.told) return;
    p.told = action != "placed";
    custom_throwdowns::News news;
    news.action = std::move(action);
    if (p.at && news.action == "placed") { news.at = p.at; news.heading = p.yaw * 180.f / 3.14159265f; }
    post(p.key, std::move(news));
}
// QueueStateNavigation's live graph publishes these two fields, with metadata
// [-1] for a command without arguments. Do not change the Back keyword stack.
bool navigate(const menu_data::Context& c, std::string_view name) {
    using namespace menu_data;
    const auto roots = c.roots({0xcc4776b5});
    require(roots.size() == 1, "Native navigation model is unavailable.");
    const auto current = c.field(roots.front().model, 0xf3ba9cf8);
    const auto metadata = c.field(roots.front().model, 0xf437b255);
    if (read<std::uint32_t>(c.address(current))) return false;
    c.set(current, game::native_name_hash(name));
    c.array(metadata, std::vector<std::int32_t>{-1});
    logging::log(logging::Level::info, logging::Channel::ui, "Custom throwdown: navigation {}.", name);
    return true;
}
} // namespace

bool open_setup(const std::string& key) {
    if (!find(key)) return false;
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    // A fresh card replaces a setup that never got as far as a flag.
    if (p.step != Step::idle && !((p.step == Step::open || p.step == Step::setup) && !p.flag_seen && !p.mmid)) return false;
    tell(p, "cancelled"); // the setup this card replaces, if any
    reset(p);
    p.step = Step::open; p.key = key; p.show_setup = true;
    p.changed = GetTickCount64(); p.until = p.changed + 15000;
    reset_settings(key);
    custom_throwdowns::News news;
    news.action = "opened";
    post(key, std::move(news));
    prepare_throwdown_injection();
    return true;
}
std::string setup_mode() {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return p.show_setup && (p.step == Step::open || p.step == Step::setup) ? p.key : std::string{};
}
bool placement_active() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return p.step != Step::idle && p.step != Step::waiting;
}
bool registration_owned() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    // The plugin's match outlives the temporary registration. Once that is
    // destroyed, stock throwdowns own their events again.
    return p.step != Step::idle && (p.step != Step::waiting || p.mmid != 0);
}
bool owns_registration(std::uint32_t mmid) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return mmid && p.step != Step::idle && p.mmid == mmid;
}
bool registration_waiting() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return p.step == Step::waiting && p.handed_off && p.at && p.mmid;
}
bool flag_down() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return (p.step == Step::closing || p.step == Step::waiting) && p.at && p.mmid && !p.cancelling;
}
std::string flag_key() {
    auto& p = placement();
    std::string key;
    {
        std::lock_guard lock(p.mutex);
        if (p.step == Step::idle) return {};
        key = p.key;
    }
    const auto mode = find(key);
    return mode ? mode->flag : std::string{};
}
std::optional<Offer> flag_offer() {
    auto& p = placement();
    std::string key;
    std::uint32_t mmid{};
    {
        std::lock_guard lock(p.mutex);
        if ((p.step != Step::waiting && p.step != Step::closing) || !p.at || !p.mmid || p.cancelling) return std::nullopt;
        key = p.key;
        mmid = p.mmid;
    }
    const auto mode = find(key);
    if (!mode) return std::nullopt;
    const auto values = settings(key);
    const auto players = values.find("players");
    return Offer{key, mode->flag, static_cast<std::uint32_t>(players == values.end() ? mode->players : players->second), mmid};
}
bool known_queue(std::uint32_t mmid) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    return mmid && std::find(p.queues.begin(), p.queues.end(), mmid) != p.queues.end();
}
void release(std::string_view plugin) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::idle || p.key.substr(0, p.key.find('/')) != plugin) return;
    p.told = true; // the plugin asked: no news back
    cleanup(p, GetTickCount64());
}
void retire_unconfirmed() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if ((p.step != Step::open && p.step != Step::setup && p.step != Step::cleanup) || p.flag_seen || p.mmid || p.handed_off) return;
    tell(p, "cancelled");
    reset(p);
}
void observe_graph(std::uint32_t graph) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::idle) return;
    if (graph == 0x0f131312 && p.step == Step::setup) { // Spot Battle setup OnBegin
        p.setup_seen = true; p.until = GetTickCount64() + 300000;
    }
    if (graph == 1867583346U && p.step == Step::setup && p.setup_seen) { // the same setup's OnEnd
        // Back and Confirm both leave this state. Stop adapting its page now;
        // a Confirm goes on to the flag update below. Ignore a previous
        // entry's late OnEnd, which comes before this one's OnBegin.
        p.show_setup = false; p.changed = GetTickCount64(); p.until = p.changed + 1500;
    }
    if (graph == 1867583346U && p.step == Step::cleanup && !p.flag_seen && !p.mmid) p.exited = true;
    // Spot Battle's own placement update, after its validator and spawn set up.
    if (graph == 0x9700be92 && p.created && p.setup_seen && (p.step == Step::setup || (p.step == Step::flag && !p.flag_seen))) {
        p.show_setup = false; p.step = Step::flag; p.flag_seen = true; p.until = GetTickCount64() + 300000;
        logging::write(logging::Level::info, logging::Channel::ui, "Custom throwdown: entered the native flag placement.");
    }
}
void observe_created() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::setup) p.created = true;
}
void observe_queue(std::uint32_t mmid) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (!mmid || p.step == Step::idle || std::find(p.queues.begin(), p.queues.end(), mmid) != p.queues.end()) return;
    if (p.queues.size() == 64) p.queues.erase(p.queues.begin());
    p.queues.push_back(mmid);
}
void observe_position(std::uint32_t mmid, const std::array<float, 3>& at, float yaw) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if ((p.step != Step::placing && p.step != Step::flag) || !p.created || !p.flag_seen || !mmid || !std::isfinite(yaw) ||
        !std::all_of(at.begin(), at.end(), [](float n) { return std::isfinite(n) && std::abs(n) < 100000.f; })) return;
    p.at = at; p.yaw = yaw; p.mmid = mmid; p.step = Step::closing; p.until = GetTickCount64() + 15000;
    logging::log(logging::Level::info, logging::Channel::ui,
        "Custom throwdown: flag at ({:.2f}, {:.2f}, {:.2f}), heading {:.1f} degrees, native queue {:#x}.",
        at[0], at[1], at[2], yaw * 180.f / 3.14159265f, mmid);
}
void observe_exit(bool cancelling) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::idle) return;
    p.exited = true; p.cancelling = cancelling || !p.at;
    p.step = p.cancelling ? Step::cleanup : Step::waiting;
    p.changed = GetTickCount64(); p.until = p.changed + 15000;
    if (p.cancelling) tell(p, "cancelled");
}
void observe_destroyed(std::uint32_t mmid) noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::cleanup && p.mmid == mmid) { p.destroyed = true; p.changed = GetTickCount64(); }
}
void waiting_start() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    // Start can come before the setup page's exit has moved us to waiting.
    if ((p.step != Step::waiting && p.step != Step::closing) || !p.at || !p.mmid) return;
    if (!p.handed_off) { p.handed_off = true; tell(p, "placed"); }
    if (!p.key.empty() && !p.told) {
        p.told = true;
        custom_throwdowns::News news;
        news.action = "start";
        news.host = true;
        news.players = throwdown_relay_mode_players(p.mmid);
        if (!news.players.empty()) news.leader = news.players.front();
        post(p.key, std::move(news));
    }
    throwdown_relay_local({ThrowdownLocalAction::Kind::force_started, {}, p.mmid});
    // The flag has done its job: the plugin runs the match from here.
    cleanup(p, GetTickCount64());
}
void waiting_left() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    if (p.step == Step::idle) return;
    tell(p, p.handed_off ? "leave" : "cancelled");
    cleanup(p, GetTickCount64());
}
void abandon() noexcept {
    auto& p = placement();
    std::lock_guard lock(p.mutex);
    // The level's own teardown removes its queues. Never destroy an old MMID
    // in a new world, where the native coordinator can reuse it.
    if (p.step != Step::idle) {
        tell(p, p.handed_off ? "leave" : "cancelled");
        reset(p);
    }
    p.queues.clear();
}
void tick(const menu_data::Context& c) {
    const auto now = GetTickCount64();
    auto& p = placement();
    std::string command;
    Step next{};
    std::uint32_t destroy{};
    {
        std::lock_guard lock(p.mutex);
        if (p.step == Step::idle) return;
        if (!find(p.key) && p.step != Step::cleanup) { p.told = true; cleanup(p, now); } // its plugin went
        if (p.step == Step::flag && p.flag_seen) { p.step = Step::placing; p.until = now + 300000; }
        if (p.step == Step::waiting && !p.handed_off && p.at) {
            // The registration stays: it owns the world flag, the waiting card
            // and its Start and Leave prompts.
            p.handed_off = true;
            tell(p, "placed");
        }
        if (p.step == Step::setup && p.setup_seen && !p.show_setup && now >= p.until) {
            // Back already did the native navigation. Sending an exit could
            // close the next stock challenge's setup.
            tell(p, "cancelled");
            reset(p);
            return;
        }
        if ((p.step == Step::open || p.step == Step::setup) && !p.created && !p.setup_seen && now >= p.until) {
            logging::write(logging::Level::warning, logging::Channel::ui, "Custom throwdown: the setup page did not open.");
            tell(p, "cancelled");
            reset(p);
            return; // No native queue was created: never close another menu.
        }
        if (now >= p.until && p.step != Step::cleanup && p.step != Step::waiting) {
            tell(p, "cancelled");
            p.at.reset();
            cleanup(p, now);
        }
        if (p.step == Step::open) { command = "SpotBattle"; next = Step::setup; }
        else if (p.step == Step::cleanup) {
            if (!p.exited && !p.sent) { command = "ThrowdownerExit"; next = Step::cleanup; }
            if (p.mmid && !p.destroy_queued) destroy = p.mmid;
            if ((p.exited && !p.mmid) || (p.destroyed && now >= p.changed + 500)) reset(p);
            else if (now >= p.until) {
                logging::write(logging::Level::warning, logging::Channel::ui, "Custom throwdown: the native flag could not be removed.");
                reset(p);
            }
        }
    }
    if (destroy && prepare_throwdown_injection() && queue_throwdown_destroy(destroy)) {
        std::lock_guard lock(p.mutex);
        if (p.mmid == destroy) p.destroy_queued = true;
    }
    if (!command.empty()) {
        // Publication can run expression callbacks: arm the next step before
        // publishing, without holding this lock across native code.
        Step previous;
        { std::lock_guard lock(p.mutex); previous = p.step; p.step = next; }
        bool sent{};
        try { sent = navigate(c, command); }
        catch (...) { std::lock_guard lock(p.mutex); p.step = previous; throw; }
        std::lock_guard lock(p.mutex);
        if (!sent) p.step = previous;
        else { p.sent = now; if (next != Step::cleanup) p.until = now + 15000; }
    }
}
} // namespace dingosdk::multiplayer::custom_throwdowns
