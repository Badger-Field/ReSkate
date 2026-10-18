#include "mod_browser.h"
#include "live_mods.h"
#include "map_download.h"
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
https::Watch watch;
std::atomic<bool> stop_install{};
std::atomic<float> written{};

bool working(Stage now) { return now == Stage::checking || now == Stage::downloading || now == Stage::installing || now == Stage::enabling; }

void finish(Stage end, std::string message) {
    logging::log(end == Stage::done ? logging::Level::info : logging::Level::warning, logging::Channel::assets,
        "Mod browser: {}", message);
    std::lock_guard lock(mutex);
    outcome = std::move(message);
    stage = end;
}
bool advance(Stage from, Stage to) {
    std::lock_guard lock(mutex);
    if (watch.cancel.load() || stage.load() != from) return false;
    stage = to;
    return true;
}

std::string accept(std::span<const std::string> files) { return listing_::refuse_contents(files); }

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
        if (listing_::nsfw(wanted) || wanted.deprecated) return finish(Stage::failed, title + " is not installed from here.");
        // What Thunderstore says of it now: the listing may be old, and only this tells whether the
        // community's moderators rejected it.
        https::Download result;
        const auto answer = https::get_text(package::details_url(name), 512 * 1024, listing_seconds, package_install::agent, &result, &watch);
        if (cancelled()) return finish(Stage::failed, "Install of " + title + " cancelled.");
        if (!answer)
            return finish(Stage::failed, result.http_status == 404 ? title + " is not on Thunderstore any more."
                                                                     : "Thunderstore could not be reached to look at " + title + ".");
        const auto choice = package::choose(*answer, name, community(), "");
        if (!choice.ok) return finish(Stage::failed, choice.reason + " " + title + " is not installed from here.");
        const auto list = mods::scan_mods(mods::engine_data_root());
        const auto installed = installed_mods(list);
        if (const auto missing = listing_::missing_dependencies(wanted, installed); !missing.empty())
            return finish(Stage::failed, title + " needs " + missing.front() + (missing.size() > 1 ? " and " + std::to_string(missing.size() - 1) + " other mod(s)" : "") +
                                             ". Install it from the launcher, which brings what a mod needs.");
        const auto have = installed.find(listing_::folder_key(folder));
        const bool replace = have != installed.end();
        if (replace && listing_::compare_versions(choice.version, have->second.version) <= 0)
            return finish(Stage::failed, title + " v" + have->second.version + " is installed already.");
        {
            std::lock_guard lock(mutex);
            job.version = choice.version;
            replacing = replace;
        }
        if (!advance(Stage::checking, Stage::downloading)) return finish(Stage::failed, "Install of " + title + " cancelled.");
        const package_install::Archive archive(folder);
        // (The listing's size holds for the version it lists: a little over it is refused.)
        const auto most = wanted.file_size && wanted.version == choice.version ? std::min(largest_download, wanted.file_size + (1 << 20))
                                                                               : largest_download;
        const auto got = package_install::download(name, choice.version, {}, archive, most, download_seconds, watch).result;
        if (cancelled()) return finish(Stage::failed, "Install of " + title + " cancelled.");
        if (!got.ok)
            return finish(Stage::failed, got.error == ERROR_TIMEOUT || got.error == ERROR_WINHTTP_TIMEOUT ? "The download of " + title + " timed out."
                                       : got.http_status && got.http_status != 200
                                           ? "The download of " + title + " failed (Thunderstore answered " + std::to_string(got.http_status) + ")."
                                           : "The download of " + title + " failed (Windows error " + std::to_string(got.error) + ").");
        if (!advance(Stage::downloading, Stage::installing)) return finish(Stage::failed, "Install of " + title + " cancelled.");
        try {
            // (An installed version goes to the Recycle Bin. The game may hold its files open when it
            // was used this run: then it cannot be replaced until the game is closed.)
            package_install::install(archive, name, replace, [](float done) { written = done; }, stop_install, accept);
        } catch (const std::exception& failure) {
            if (stop_install.load()) return finish(Stage::failed, "Install of " + title + " cancelled.");
            return finish(Stage::failed, replace ? title + " could not be replaced while the game runs (" + failure.what() +
                                                       "). Close the game and update it from the launcher."
                                                 : title + " could not be installed: " + failure.what());
        }
        // (Past here it is in Mods: a cancel no longer stops it.)
        {
            std::lock_guard lock(mutex);
            stage = Stage::enabling;
        }
        // What is in the folder now, read from the disk: the same rule as the archive was held to.
        if (const auto refused = package_install::check_installed(folder, accept); !refused.empty()) {
            try { launcher_mods::remove(package_install::mods_root(), folder); } catch (const std::exception&) {}
            return finish(Stage::failed, refused + " " + title + " was taken out again; install it from the launcher if you trust it.");
        }
        auto now = mods::scan_mods(mods::engine_data_root());
        if (!now.issue.empty()) return finish(Stage::failed, title + " is installed, but mods.json could not be read: " + now.issue);
        bool found{};
        for (auto& entry : now.entries)
            if (_stricmp(entry.mod.name.c_str(), folder.c_str()) == 0) found = entry.enabled = true;
        if (!found) return finish(Stage::failed, title + " is installed, but its folder is not in the mods list.");
        mods::save_mod_order(now.root, now.entries);
        finish(Stage::done, (replace ? "Updated " + title + " to v" : "Installed " + title + " v") + choice.version + ". Apply to load it.");
    } catch (const std::exception& failure) {
        finish(Stage::failed, title + " could not be installed: " + failure.what());
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

std::string blocked() {
    if (busy()) return "Another mod is being installed.";
    if (map_download::view().stage != map_download::Stage::idle) return "A map is being downloaded for a session.";
    if (live_mods::busy()) return "Mods are being applied.";
    return {};
}

void install(const listing_::Package& package) {
    if (!blocked().empty()) return;
    {
        std::lock_guard lock(mutex);
        if (working(stage.load())) return;
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
    try {
        // Detached, as the live merge's thread is: the game may close while a mod downloads.
        std::thread([] {
            try { run(); } catch (...) { finish(Stage::failed, "The install could not be finished."); }
        }).detach();
    } catch (...) {
        finish(Stage::failed, "The install could not be started.");
    }
}

void cancel() noexcept {
    std::lock_guard lock(mutex);
    const auto now = stage.load();
    if (now != Stage::checking && now != Stage::downloading && now != Stage::installing) return;
    watch.cancel = true;
    stop_install = true;
}

void dismiss() noexcept {
    std::lock_guard lock(mutex);
    if (stage.load() == Stage::done || stage.load() == Stage::failed) stage = Stage::idle;
}
}
