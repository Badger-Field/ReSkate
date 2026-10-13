#include "Extension/Assets/map_download.h"
#include "Extension/Assets/live_mods.h"
#include "Extension/Multiplayer/Session/monotonic_clock.h"
#include "Extension/Multiplayer/Net/protocol.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Vfs/https_download.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Launcher/mod_manager.h"
#include "Extension/UI/Overlay/overlay.h"
#include <Windows.h>
#include <winhttp.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace fixture {
namespace fs = std::filesystem;
namespace policy = dingosdk::multiplayer::map_fetch_policy;
fs::path root;
std::atomic<std::uint64_t> clock{1000000};
std::atomic<bool> downloading{}, installing{}, release_download{}, release_install{}, merging{};
std::atomic<bool> fetching{true}, held{}, observed_cancel{}, installed_cancel{};
std::atomic<unsigned> downloads{}, installs{}, applies{}, stops{}, rejoins{};
bool block_download{}, block_install{}, slow{}, transfer_error{}, fallback{};
dingosdk::MultiplayerModel session;
std::mutex messages_mutex, gate_mutex;
std::condition_variable gate;
std::vector<std::string> notices;
std::vector<std::uint64_t> http_budgets;
fs::path archive;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template<class Predicate>
void wait(Predicate predicate, const char* message) {
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!predicate()) {
        if (std::chrono::steady_clock::now() >= end) throw std::runtime_error(message);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}
bool noticed(std::string_view text) {
    std::lock_guard lock(messages_mutex);
    for (const auto& notice : notices)
        if (notice.find(text) != std::string::npos) return true;
    return false;
}
}

namespace dingosdk::multiplayer {
std::uint64_t fake_now_us() noexcept { return fixture::clock.load(); }
}

// Compile the actual lifecycle, not a parallel model. Preincluded headers keep these
// substitutions local to this translation unit; archives also stay in the test fixture.
DWORD test_get_temp_path(DWORD capacity, LPWSTR output) {
    const auto path = fixture::root.wstring() + L"\\";
    if (path.size() >= capacity) return 0;
    std::copy(path.begin(), path.end(), output);
    output[path.size()] = L'\0';
    return static_cast<DWORD>(path.size());
}
#define now_us fake_now_us
#define GetTempPathW test_get_temp_path
#include "../map_download.cpp"
#undef GetTempPathW
#undef now_us

namespace dingosdk::https {
std::optional<std::string> get_text(std::wstring_view, std::uint64_t, std::uint32_t,
    const wchar_t*, Download* result, Watch* watch) {
    fixture::require(watch && watch->deadline_us.load() == map_download::deadline.load(),
        "metadata shares the session deadline");
    if (result) *result = {true, 200, 0};
    return R"({"namespace":"Team","name":"Map","community_listings":[{"community":"reskate","categories":["Maps"]}],
        "latest":{"version_number":"2.0.0","description":"Test map"}})";
}
Download get(std::wstring_view, const std::filesystem::path& destination,
    std::uint64_t max_bytes, std::uint32_t timeout_seconds, const wchar_t*, Watch* watch) {
    using namespace fixture;
    require(max_bytes == (8ull << 30), "size restriction retained");
    require(timeout_seconds == policy::transfer_us / policy::second_us, "HTTP timeout uses transfer budget");
    archive = destination;
    const auto request = ++downloads;
    http_budgets.push_back(policy::remaining(watch->deadline_us.load(), fixture::clock.load()));
    std::ofstream(destination, std::ios::binary) << "partial archive";
    watch->received.store(10);
    watch->total.store(100);
    downloading.store(true);
    if (block_download) {
        std::unique_lock lock(gate_mutex);
        gate.wait(lock, [] { return release_download.load(); });
    }
    if (slow) {
        // Real download progress still cannot extend either absolute deadline.
        fixture::clock.fetch_add(10 * 60 * policy::second_us);
        watch->received.store(90);
    }
    observed_cancel.store(watch->cancel.load());
    if (watch->cancel.load()) return {false, 0, ERROR_CANCELLED};
    if (fallback && request == 1) {
        fixture::clock.fetch_add(60 * policy::second_us);
        return {false, 404, 0};
    }
    if (transfer_error) return {false, 0, ERROR_WINHTTP_TIMEOUT};
    watch->received.store(100);
    return {true, 200, 0};
}
}

namespace dingosdk::mods {
std::filesystem::path engine_data_root() noexcept { return fixture::root; }
ModList scan_mods(const std::filesystem::path&) noexcept { return {}; }
void save_mod_order(const std::filesystem::path&, const std::vector<ModEntry>&) {}
}
namespace dingosdk::launcher_mods {
std::string install(const std::filesystem::path& root, const std::filesystem::path& source, bool,
    const Progress& progress, const std::atomic<bool>& cancel, const InstallOptions& options) {
    using namespace fixture;
    require(fs::exists(source), "installer receives completed archive");
    ++installs;
    installing.store(true);
    if (block_install) {
        std::unique_lock lock(gate_mutex);
        gate.wait(lock, [] { return release_install.load(); });
    }
    progress(0.5f);
    installed_cancel.store(cancel.load());
    if (cancel.load()) throw std::runtime_error("cancelled");
    fs::create_directories(root / options.folder);
    std::ofstream(root / options.folder / "reskate-levels.json") << "{}";
    return options.folder;
}
void remove(const std::filesystem::path&, const std::string&) {}
}
namespace dingosdk::live_mods {
bool only_maps(const std::filesystem::path&) { return true; }
bool busy() noexcept { return fixture::merging.load(); }
std::string apply(bool reload) {
    fixture::require(!reload, "map apply must not reload prematurely");
    ++fixture::applies;
    fixture::merging.store(true);
    return "applying";
}
Progress progress() { return {1, 2, "map"}; }
std::vector<std::string> applied_mods() { return {"Team-Map"}; }
std::string status() { return "applied"; }
}
namespace dingosdk::multiplayer {
MultiplayerModel model() { return fixture::session; }
bool fetching_map() noexcept { return fixture::fetching.load(); }
void hold_map_load(bool hold) noexcept { fixture::held.store(hold); }
bool queue_command(std::string_view action, std::string_view, std::string_view) {
    if (action == "stop") ++fixture::stops;
    if (action == "rejoin") ++fixture::rejoins;
    return true;
}
std::string map_package_name(std::string_view folder, std::string_view version) {
    return std::string(folder) + "-" + std::string(version);
}
}
namespace dingosdk::overlay {
void set_map_download_icon(const std::string&) noexcept {}
void notify(NoticeLevel, std::string title, std::string text) noexcept {
    std::lock_guard lock(fixture::messages_mutex);
    fixture::notices.push_back(title + ": " + text);
}
}
namespace dingosdk::logging {
bool enabled(Level) noexcept { return false; }
void write(Level, Context, Channel, std::string_view) noexcept {}
void write(Level, Channel, std::string_view) noexcept {}
}

namespace fixture {
namespace download = dingosdk::map_download;
using Stage = download::Stage;

void drain() {
    release_download.store(true);
    release_install.store(true);
    gate.notify_all();
    wait([] { return download::workers.load() == 0; }, "workers did not drain");
}
void reset() {
    drain();
    require(download::view().stage == Stage::idle, "previous lifecycle did not finish");
    fs::remove_all(root / "Mods");
    clock.store(1000000);
    downloading.store(false);
    installing.store(false);
    release_download.store(false);
    release_install.store(false);
    merging.store(false);
    fetching.store(true);
    held.store(false);
    observed_cancel.store(false);
    installed_cancel.store(false);
    downloads.store(0);
    installs.store(0);
    applies.store(0);
    stops.store(0);
    rejoins.store(0);
    block_download = block_install = slow = transfer_error = fallback = false;
    session = {};
    session.active = true;
    session.connected = false;
    notices.clear();
    http_budgets.clear();
    archive.clear();
    download::last_failed.clear();
}
void offer(std::uint64_t deadline = 0, bool update = false) {
    dingosdk::multiplayer::MapNeed need{"Team-Map-1.0.0", "Test map", true, false,
        update ? "0.9.0" : "", deadline};
    download::offer(need);
    wait([] { return download::asking(); }, "metadata did not reach confirmation");
    wait([] { return download::workers.load() == 0; }, "metadata worker did not drain");
}
void start_apply() {
    wait([] { return download::view().stage == Stage::applying; }, "did not install and reach applying");
    drain();
    download::tick();
    require(applies.load() == 1 && held.load(), "apply starts once and holds session loading");
}
void applying() {
    download::answer(true);
    start_apply();
}
void joined() {
    merging.store(false);
    download::tick();
    require(download::view().stage == Stage::joining, "completed merge advances to joining");
    fetching.store(false);
    session.connected = true;
    clock.fetch_add(2500000);
    download::tick();
    require(download::view().stage == Stage::idle, "joined session clears lifecycle");
    require(!held.load() && stops.load() == 0 && rejoins.load() == 0, "normal join does not leave or rejoin");
}
void idle(const char* reason) {
    require(download::view().stage == Stage::idle, reason);
    drain();
    require(stops.load() == 1, "failure leaves waiting session exactly once");
    download::tick();
    require(held.load() == (merging.load() && download::apply_started.load()),
        "failure holds map loading only while an uncancellable live merge drains");
    require(archive.empty() || !fs::exists(archive), "partial archive removed");
}

void tests() {
    reset();
    offer();
    applying();
    require(downloads.load() == 1 && installs.load() == 1, "success downloads and installs exactly once");
    joined();

    reset();
    block_download = true;
    slow = true;
    offer();
    download::answer(true);
    wait([] { return downloading.load(); }, "slow download did not start");
    clock.fetch_add(10 * 60 * policy::second_us);
    download::watch.received.store(50);
    download::tick();
    require(download::view().stage == Stage::downloading && download::view().received == 50 && held.load(),
        "progressing transfer remains active beyond old five minute deadline");
    release_download.store(true);
    gate.notify_all();
    start_apply();
    require(clock.load() > 5 * 60 * policy::second_us, "slow progressing transfer exceeds former five minute limit");
    joined();

    reset();
    transfer_error = true;
    offer();
    download::answer(true);
    wait([] { return download::view().stage == Stage::idle; }, "HTTP timeout did not fail");
    idle("transfer timeout");
    require(installs.load() == 0 && noticed("timed out"), "HTTP timeout cannot install");
    transfer_error = false;
    stops.store(0);
    offer();
    applying();
    require(downloads.load() == 2, "timeout allows same package retry");
    joined();

    reset();
    block_download = true;
    offer();
    download::answer(true);
    wait([] { return downloading.load(); }, "download did not start");
    clock.store(download::deadline.load());
    download::tick();
    idle("active session deadline");
    require(observed_cancel.load() && installs.load() == 0, "session expiry cancels active HTTP");

    reset();
    block_install = true;
    offer();
    download::answer(true);
    wait([] { return installing.load(); }, "install did not start");
    clock.store(download::deadline.load());
    download::tick();
    idle("install timeout");
    require(installed_cancel.load() && applies.load() == 0 && noticed("could not be installed"), "installer observes cancellation");

    reset();
    offer();
    applying();
    clock.store(download::deadline.load());
    download::tick();
    idle("apply timeout");
    require(merging.load() && rejoins.load() == 0, "late atomic merge must not join");
    download::offer({"Team-Map-1.0.0", "Test map", true});
    require(download::view().stage == Stage::idle && noticed("still finishing"), "busy live merge blocks retry");
    merging.store(false);
    download::tick();
    require(rejoins.load() == 0 && !held.load(), "finished cancelled apply cannot rejoin and releases load hold");

    reset();
    offer();
    download::answer(false);
    idle("decline");
    require(downloads.load() == 0 && noticed("cancelled"), "decline never downloads");

    reset();
    offer();
    clock.store(download::ask_until.load() - 1);
    download::tick();
    require(download::asking(), "confirmation remains open one microsecond before deadline");
    clock.fetch_add(1);
    download::answer(true);
    idle("confirmation exact boundary");
    require(downloads.load() == 0 && noticed("two minutes"), "late click cannot bypass confirmation timeout");

    reset();
    offer();
    clock.store(download::ask_until.load());
    download::tick();
    idle("confirmation tick timeout");
    require(downloads.load() == 0 && noticed("two minutes"), "tick expires unanswered prompt");

    reset();
    offer(clock.load() + policy::completion_reserve_us);
    download::answer(true);
    idle("completion reserve boundary");
    require(downloads.load() == 0, "no transfer starts when only completion reserve remains");

    reset();
    block_download = true;
    offer(clock.load() + policy::completion_reserve_us + 1);
    download::answer(true);
    wait([] { return downloading.load(); }, "last microsecond transfer did not start");
    require(download::transfer_until.load() == clock.load() + 1, "near-boundary transfer keeps reserve");
    clock.fetch_add(1);
    download::tick();
    idle("last transfer microsecond");
    require(observed_cancel.load() && installs.load() == 0, "transfer exact boundary cancels without install");

    reset();
    block_download = true;
    offer();
    download::answer(true);
    wait([] { return downloading.load(); }, "HTTP did not start for cancel race");
    download::answer(false);
    require(download::watch.cancel.load(), "cancel flag is immediate");
    download::tick();
    require(held.load(), "cancelled HTTP keeps map loading held until worker drains");
    download::offer({"Team-Map-1.0.0", "Test map", true});
    require(downloads.load() == 1 && noticed("still finishing"), "retry cannot reuse a draining HTTP worker");
    drain();
    require(observed_cancel.load() && !fs::exists(archive) && installs.load() == 0, "cancel cleans archive without install");
    // The same package is retriable after a user cancellation.
    block_download = false;
    offer();
    applying();
    require(downloads.load() == 2, "retry fetches same package again");
    // Retry guard deliberately queued another stop above.
    stops.store(0);
    joined();

    reset();
    block_download = true;
    offer();
    download::answer(true);
    wait([] { return downloading.load(); }, "HTTP did not start for completion race");
    clock.store(download::transfer_until.load());
    // Return a successful HTTP response before tick notices its expired deadline.
    drain();
    download::tick();
    require(download::view().stage == Stage::idle && installs.load() == 0,
        "success racing the transfer deadline cannot reach installation");
    idle("transfer completion race");

    reset();
    fallback = true;
    offer();
    applying();
    require(downloads.load() == 2 && http_budgets.size() == 2, "404 selects newest fallback");
    require(http_budgets[0] == policy::transfer_us, "initial request gets at most 27 minutes");
    require(http_budgets[1] == policy::transfer_us - 60 * policy::second_us, "fallback loses first request's minute");
    joined();

    reset();
    fallback = true;
    offer(0, true);
    download::answer(true);
    wait([] { return download::view().stage == Stage::idle; }, "missing host version did not fail");
    idle("host version consistency");
    require(downloads.load() == 1 && installs.load() == 0, "version mismatch never falls back to another host version");

    reset();
    offer();
    const auto original_deadline = download::deadline.load();
    download::offer({"Team-Other-1.0.0", "Other map", true});
    require(download::asking() && download::deadline.load() == original_deadline && stops.load() == 1,
        "host changing maps does not replace in-flight request or extend its deadline");
    stops.store(0);
    download::answer(false);
    idle("host map change");

    reset();
    download::tick();
    require(!held.load() && downloads.load() == 0 && stops.load() == 0 && rejoins.load() == 0,
        "ordinary join without download is untouched");
}
}

int main(int argc, char** argv) {
    try {
        fixture::require(argc == 2, "pass a fixture directory");
        fixture::root = std::filesystem::absolute(argv[1]);
        std::filesystem::create_directories(fixture::root);
        fixture::tests();
        fixture::drain();
        std::filesystem::remove_all(fixture::root);
        std::cout << "Production map download timeout lifecycle tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        // A bounded wait failure can leave detached production workers alive. Do not
        // destroy their fixture globals while they may still be using them.
        std::_Exit(1);
    }
}
