#include "mod_browser.h"
#include "live_mods.h"
#include "map_download.h"
#include "mods_writer.h"
#include "package_install.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Vfs/https_download.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Engine/Vfs/mod_list.h"
#include "Engine/Vfs/thunderstore_package.h"
#include "Launcher/mod_manager.h"
#include <Windows.h>
#include <winhttp.h>
#include <algorithm>
#include <atomic>
#include <mutex>
#include <thread>

namespace dingosdk::mod_browser {
namespace {
namespace listing_ = thunderstore_listing;
namespace package = thunderstore_package;

// A mod is at most a few gigabytes; the download may take as long as a slow line needs for that.
constexpr std::uint64_t largest_download = 8ull << 30;
constexpr std::uint32_t download_seconds = 4 * 60 * 60;
constexpr std::uint32_t listing_seconds = 20;
constexpr ULONGLONG listing_period = 10 * 60 * 1000, retry_period = 60 * 1000;

// RESKATE_THUNDERSTORE_COMMUNITY, as the launcher reads it, else reskate.
std::string community() {
    char value[128]{};
    const auto length = GetEnvironmentVariableA("RESKATE_THUNDERSTORE_COMMUNITY", value, sizeof(value));
    return listing_::community(length && length < sizeof(value) ? std::string_view(value, length) : std::string_view());
}
std::wstring wide(const std::string& text) { return std::wstring(text.begin(), text.end()); } // ASCII URLs only here

std::string fetch(const std::string& url, std::uint64_t most) {
    https::Download result;
    auto body = https::get_text(wide(url), most, listing_seconds, package_install::agent, &result);
    if (!body)
        throw std::runtime_error(result.http_status && result.http_status != 200
            ? "Thunderstore answered " + std::to_string(result.http_status) + "."
            : "Thunderstore could not be reached (Windows error " + std::to_string(result.error) + ").");
    return std::move(*body);
}
// The listing index and its chunks, else the plain listing: what the launcher reads
// (Launcher/gui_mods_browse.cpp).
std::vector<listing_::Package> fetch_listing(const std::string& name) {
    try {
        const auto index = listing_::parse_index(listing_::gunzip(fetch(listing_::listing_index_url(name), 1 << 20), 4 << 20));
        std::vector<listing_::Package> packages;
        for (const auto& url : index) {
            auto chunk = listing_::parse_listing(listing_::gunzip(fetch(url, 32ull << 20), 128ull << 20));
            std::move(chunk.begin(), chunk.end(), std::back_inserter(packages));
        }
        return packages;
    } catch (const std::exception& failure) {
        logging::log(logging::Level::warning, logging::Channel::assets,
            "Mod browser: the Thunderstore listing index could not be read ({}); reading the plain listing.", failure.what());
    }
    return listing_::parse_listing(listing_::gunzip(fetch(listing_::listing_url(name), 64ull << 20), 128ull << 20));
}

std::mutex listing_mutex;
std::shared_ptr<const Listing> last_listing;   // under listing_mutex
std::atomic<bool> listing_reading{};
std::atomic<ULONGLONG> listing_next{};

void read_listing() {
    auto fresh = std::make_shared<Listing>();
    try {
        const auto name = community();
        fresh->packages = fetch_listing(name);
        logging::log(logging::Level::info, logging::Channel::assets, "Mod browser: {} package(s) in {}.", fresh->packages.size(), name);
    } catch (const std::exception& failure) {
        fresh->error = failure.what();
        logging::log(logging::Level::warning, logging::Channel::assets, "Mod browser: the Thunderstore listing could not be read: {}", fresh->error);
    }
    fresh->loaded = true;
    {
        std::lock_guard lock(listing_mutex);
        // (A failed read keeps what the last good one had, and tries again sooner.)
        if (!fresh->error.empty()) {
            if (last_listing) fresh->packages = last_listing->packages;
            listing_next = GetTickCount64() + retry_period;
        }
        last_listing = std::move(fresh);
    }
    listing_reading = false;
}

// One install at a time, which `stage` sees to: it leaves idle, done and failed only by install().
std::mutex mutex;
std::atomic<Stage> stage{Stage::idle};
listing_::Package job;           // under mutex, while busy
bool replacing{};                // under mutex
std::string outcome;             // under mutex: done and failed
std::string last_result, last_error; // under mutex: the last install's outcome, kept after dismiss
std::string query_text;          // under mutex: the search, the card's and `mods browse`'s
std::atomic<std::uint64_t> query_changes{};
std::atomic<bool> open_wanted{}, reveal_wanted{};
https::Watch watch;
std::atomic<bool> stop_install{};
std::atomic<float> written{};

bool working(Stage now) { return now == Stage::checking || now == Stage::downloading || now == Stage::installing || now == Stage::enabling; }

// How an install ended, for the log (one line each, "Mod browser: <how>: ...") and `mods status`.
enum class Result { done, refused, cancelled, failed };
constexpr const char* result_names[]{"done", "refused", "cancelled", "failed"};
void finish(Result how, std::string message) {
    const auto name = result_names[static_cast<int>(how)];
    logging::log(how == Result::done ? logging::Level::info : logging::Level::warning, logging::Channel::assets,
        "Mod browser: {}: {}", name, message);
    std::lock_guard lock(mutex);
    (how == Result::done ? last_result : last_error) = std::string(name) + ": " + message;
    outcome = std::move(message);
    stage = how == Result::done ? Stage::done : Stage::failed;
}
void log_stage(const std::string& text) { logging::log(logging::Level::info, logging::Channel::assets, "Mod browser: {}", text); }
bool advance(Stage from, Stage to) {
    std::lock_guard lock(mutex);
    if (watch.cancel.load() || stage.load() != from) return false;
    stage = to;
    return true;
}

std::string accept(std::span<const std::string> files) { return listing_::refuse_contents(files); }

bool same(std::string_view a, std::string_view b) { return a.size() == b.size() && _strnicmp(a.data(), b.data(), a.size()) == 0; }

listing_::InstalledMods installed_mods(const mods::ModList& list) {
    listing_::InstalledMods installed;
    for (const auto& entry : list.entries)
        installed[listing_::folder_key(entry.mod.name)] = {entry.mod.version, entry.enabled};
    return installed;
}

void run() {
    listing_::Package wanted;
    {
        std::lock_guard lock(mutex);
        wanted = job;
    }
    const package::Name name{wanted.owner, wanted.name, ""};
    const auto folder = wanted.folder();
    const auto title = wanted.title();
    const auto cancelled = [&] { return watch.cancel.load(); };
    try {
        if (listing_::nsfw(wanted) || wanted.deprecated) return finish(Result::refused, title + " is not installed from here.");
        // What Thunderstore says of it now: the listing may be old, and only this tells whether the
        // community's moderators rejected it.
        https::Download result;
        const auto answer = https::get_text(package::details_url(name), 512 * 1024, listing_seconds, package_install::agent, &result, &watch);
        if (cancelled()) return finish(Result::cancelled, "Install of " + title + " cancelled.");
        if (!answer)
            return finish(Result::failed, result.http_status == 404 ? title + " is not on Thunderstore any more."
                                                                     : "Thunderstore could not be reached to look at " + title + ".");
        const auto choice = package::choose(*answer, name, community(), "");
        if (!choice.ok) return finish(Result::refused, choice.reason + " " + title + " is not installed from here.");
        const auto list = mods::scan_mods(mods::engine_data_root());
        const auto installed = installed_mods(list);
        const auto officials = mods::official_packs(package_install::mods_root(), true);
        const auto keeps = [&](std::string_view other) {
            return kept(other, officials) || mods::kept_by_reskate(package_install::mods_root(), other);
        };
        if (keeps(folder))
            return finish(Result::refused, title + " is an official mod: it is installed on the launcher's OFFICIAL tab.");
        if (const auto missing = listing_::missing_dependencies(wanted, installed, keeps); !missing.empty())
            return finish(Result::refused, title + " needs " + missing.front() + (missing.size() > 1 ? " and " + std::to_string(missing.size() - 1) + " other mod(s)" : "") +
                                             ". Install it from the launcher, which brings what a mod needs.");
        const auto have = installed.find(listing_::folder_key(folder));
        const bool replace = have != installed.end();
        if (replace && listing_::compare_versions(choice.version, have->second.version) <= 0)
            return finish(Result::refused, title + " v" + have->second.version + " is installed already.");
        {
            std::lock_guard lock(mutex);
            job.version = choice.version;
            replacing = replace;
        }
        if (!advance(Stage::checking, Stage::downloading)) return finish(Result::cancelled, "Install of " + title + " cancelled.");
        const package_install::Archive archive(folder);
        // (The listing's size holds for the version it lists: a little over it is refused.)
        const auto most = wanted.file_size && wanted.version == choice.version ? std::min(largest_download, wanted.file_size + (1 << 20))
                                                                               : largest_download;
        log_stage("downloading " + wanted.full_name + " v" + choice.version +
                  (wanted.file_size && wanted.version == choice.version ? " (" + listing_::size_text(wanted.file_size) + ")." : "."));
        // How far it is, in the log every tenth (or 16 MB when Thunderstore gives no size), from a
        // thread beside the download, which holds this one until it ends.
        watch.received = 0;
        watch.total = 0;
        struct Progress {
            std::atomic<bool> done{};
            std::thread thread;
            ~Progress() {
                done = true;
                if (thread.joinable()) thread.join();
            }
        } progress;
        try {
            progress.thread = std::thread([&done = progress.done, full_name = wanted.full_name] {
                std::uint64_t logged{};
                while (!done.load()) {
                    Sleep(250);
                    const auto received = watch.received.load(), total = watch.total.load();
                    if (const auto step = listing_::progress_step(received, total); step > logged) {
                        logged = step;
                        try {
                            log_stage("downloading " + full_name + ": " + listing_::size_text(received) +
                                      (total ? " of " + listing_::size_text(total) + " (" + std::to_string(step * 10) + "%)" : std::string()));
                        } catch (...) {}
                    }
                }
            });
        } catch (...) {}
        const auto got = package_install::download(name, choice.version, {}, archive, most, download_seconds, watch).result;
        if (cancelled()) return finish(Result::cancelled, "Install of " + title + " cancelled.");
        if (!got.ok)
            return finish(Result::failed, got.error == ERROR_TIMEOUT || got.error == ERROR_WINHTTP_TIMEOUT ? "The download of " + title + " timed out."
                                       : got.http_status && got.http_status != 200
                                           ? "The download of " + title + " failed (Thunderstore answered " + std::to_string(got.http_status) + ")."
                                           : "The download of " + title + " failed (Windows error " + std::to_string(got.error) + ").");
        if (!advance(Stage::downloading, Stage::installing)) return finish(Result::cancelled, "Install of " + title + " cancelled.");
        log_stage("installing " + wanted.full_name + " v" + choice.version + " into Mods.");
        // (What the archive holds is refused before anything is written: that is a refusal, not a failure.)
        bool refused_files{};
        const auto checked = [&refused_files](std::span<const std::string> files) {
            auto refused = accept(files);
            refused_files = !refused.empty();
            return refused;
        };
        try {
            // (An installed version is set aside and deleted once the new one is in. The game may hold
            // its files open when it was used this run: then it cannot be replaced until the game is
            // closed, and stays as it was.)
            package_install::install(archive, name, replace, [](float done) { written = done; }, stop_install, checked);
        } catch (const std::exception& failure) {
            if (stop_install.load()) return finish(Result::cancelled, "Install of " + title + " cancelled.");
            if (refused_files) {
                // A package with nothing of its own that names other mods is a mod pack: the launcher
                // installs what it names (as it would refuse the pack itself, which has nothing to load).
                const std::string why = failure.what();
                const bool pack = !wanted.dependencies.empty() && why.find("nothing for the game to load") != std::string::npos;
                return finish(Result::refused, why + " " + title +
                    (pack ? " is a mod pack of " + std::to_string(wanted.dependencies.size()) + " other mod(s): install it from the launcher, which brings them."
                          : " is not installed from here."));
            }
            return finish(Result::failed, replace ? title + " could not be replaced while the game runs (" + failure.what() +
                                                       "). Close the game and update it from the launcher."
                                                 : title + " could not be installed: " + failure.what());
        }
        // (Past here it is in Mods: a cancel no longer stops it.)
        {
            std::lock_guard lock(mutex);
            stage = Stage::enabling;
        }
        log_stage("enabling " + folder + " in mods.json.");
        // What is in the folder now, read from the disk: the same rule as the archive was held to.
        if (const auto refused = package_install::check_installed(folder, accept); !refused.empty()) {
            try { launcher_mods::remove(package_install::mods_root(), folder); } catch (const std::exception&) {}
            return finish(Result::refused, refused + " " + title + " was taken out again; install it from the launcher if you trust it.");
        }
        auto now = mods::scan_mods(mods::engine_data_root());
        if (!now.issue.empty()) return finish(Result::failed, title + " is installed, but mods.json could not be read: " + now.issue);
        bool found{};
        for (auto& entry : now.entries)
            if (_stricmp(entry.mod.name.c_str(), folder.c_str()) == 0) found = entry.enabled = true;
        if (!found) return finish(Result::failed, title + " is installed, but its folder is not in the mods list.");
        mods::save_mod_order(now.root, now.entries);
        finish(Result::done, (replace ? "Updated " + title + " to v" : "Installed " + title + " v") + choice.version + ". Apply to load it.");
    } catch (const std::exception& failure) {
        finish(Result::failed, title + " could not be installed: " + failure.what());
    }
}
} // namespace

std::shared_ptr<const Listing> listing(bool again) {
    const auto now = GetTickCount64();
    if ((again || now >= listing_next.load()) && !listing_reading.exchange(true)) {
        listing_next = now + listing_period;
        try {
            std::thread(read_listing).detach();
        } catch (...) { listing_reading = false; }
    }
    std::lock_guard lock(listing_mutex);
    return last_listing;
}
bool reading() noexcept { return listing_reading.load(); }

View view() {
    View out;
    std::lock_guard lock(mutex);
    out.stage = stage.load();
    out.last_result = last_result;
    out.last_error = last_error;
    if (out.stage == Stage::idle) return out;
    out.title = job.title();
    out.version = job.version;
    out.folder = job.folder();
    out.update = replacing;
    out.received = watch.received.load();
    out.total = watch.total.load();
    out.installed = written.load();
    out.message = outcome;
    return out;
}

bool busy() noexcept { return working(stage.load()); }

bool kept(std::string_view folder, std::span<const mods::BuiltinPack> officials) noexcept {
    return same(folder, mods::board_break_official) || same(folder, mods::road_rash_official) || mods::retired_mod(folder) ||
           std::any_of(officials.begin(), officials.end(), [&](const mods::BuiltinPack& pack) { return same(pack.name, folder); });
}

std::string blocked() {
    if (busy()) return "Another mod is being installed.";
    if (map_download::view().stage != map_download::Stage::idle) return "A map is being downloaded for a session.";
    if (mods_writer::claim().held()) return "Another install is finishing.";
    if (live_mods::busy()) return "Mods are being applied.";
    return {};
}

std::vector<Row> rows(const Listing& listing, std::string_view query, const mods::ModList& installed,
                      std::span<const mods::BuiltinPack> officials) {
    const auto have = installed_mods(installed);
    // (Official mods are the launcher's OFFICIAL tab's, as in its own browser.)
    const auto keeps = [officials](std::string_view folder) { return kept(folder, officials); };
    std::vector<Row> out;
    for (const auto* package : listing_::search(listing.packages, query, have, keeps)) {
        Row row{package, listing_::action(*package, have), {}};
        if (row.action != listing_::Action::installed)
            if (const auto missing = listing_::missing_dependencies(*package, have, keeps); !missing.empty())
                row.needs = "It needs " + missing.front() + " first: install it from the launcher, which brings what a mod needs.";
        out.push_back(std::move(row));
    }
    return out;
}

std::string refusal(const Row& row) {
    if (row.action == listing_::Action::installed) return "It is installed at its newest version.";
    if (auto why = blocked(); !why.empty()) return why;
    return row.needs;
}

std::string install(const Row& row) {
    if (!row.package) return "There is no such mod.";
    if (auto why = refusal(row); !why.empty()) return why;
    const auto& package = *row.package;
    // Mods and mods.json are this install's until it ends (mods_writer.h); a map download waits.
    if (!mods_writer::claim().try_take()) return "Another install is finishing.";
    mods_writer::Hold hold(mods_writer::claim());
    {
        std::lock_guard lock(mutex);
        if (working(stage.load())) return "Another mod is being installed.";
        job = package;
        replacing = false;
        outcome.clear();
        watch.cancel = false;
        watch.received = 0;
        watch.total = 0;
        stop_install = false;
        written = 0;
        stage = Stage::checking;
    }
    log_stage("checking " + package.full_name + " on Thunderstore.");
    try {
        // Detached, as the live merge's thread is: the game may close while a mod downloads.
        std::thread([hold = std::move(hold)] {
            try { run(); } catch (...) { finish(Result::failed, "The install could not be finished."); }
        }).detach();
    } catch (...) {
        finish(Result::failed, "The install could not be started.");
        return "The install could not be started.";
    }
    return {};
}

std::string apply(const mods::ModList& installed) {
    if (busy()) return "A mod is being installed: apply once it is in.";
    return live_mods::apply(live_mods::preview(installed) == live_mods::ApplyEffect::reload_level);
}

std::string query() {
    std::lock_guard lock(mutex);
    return query_text;
}
std::uint64_t query_generation() noexcept { return query_changes.load(); }
void set_query(std::string text) {
    std::lock_guard lock(mutex);
    if (text == query_text) return;
    query_text = std::move(text);
    ++query_changes;
}
void show() noexcept {
    open_wanted = true;
    reveal_wanted = true;
}
bool take_open() noexcept { return open_wanted.exchange(false); }
bool take_reveal() noexcept { return reveal_wanted.exchange(false); }

void cancel() noexcept {
    std::lock_guard lock(mutex);
    const auto now = stage.load();
    if (now != Stage::checking && now != Stage::downloading && now != Stage::installing) return;
    watch.cancel = true;
    stop_install = true;
    try { log_stage("cancelling the install of " + job.full_name + "."); } catch (...) {}
}

void dismiss() noexcept {
    std::lock_guard lock(mutex);
    if (stage.load() == Stage::done || stage.load() == Stage::failed) stage = Stage::idle;
}
}
