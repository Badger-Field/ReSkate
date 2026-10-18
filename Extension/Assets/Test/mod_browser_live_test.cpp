// The MODS page's GET MODS against the real Thunderstore, with no game: the code the card and the
// `mods` console commands use (Extension/Assets/mod_browser.h, package_install.h, mods_writer.h,
// Engine/Vfs/thunderstore_listing.h), in a data root of its own with an empty Mods folder. It needs
// the network, so it is built only with DINGOSDK_BUILD_MOD_BROWSER_LIVE_TEST and run by
// .github/workflows/mod-browser-live.yml, not by ctest.
//
//   dingosdk_mod_browser_live_test [--log <file>]
//
// It makes the data root, runs itself in it (the game's data root is the -dataPath argument:
// mod_catalog.h, engine_data_root), copies ReSkate.log to <file> when asked, and removes the root.
// RESKATE_LIVE_TEST_PACKAGE (Owner-Name) picks the map to install instead of the smallest one.
// Exit 0 when every check passed.
#include "Extension/Assets/live_mods.h"
#include "Extension/Assets/map_download.h"
#include "Extension/Assets/mod_browser.h"
#include "Extension/Assets/mods_writer.h"
#include "Extension/Assets/package_install.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Vfs/builtin_packs.h"
#include "Engine/Vfs/https_download.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Engine/Vfs/mod_list.h"
#include "Engine/Vfs/thunderstore_listing.h"
#include "Launcher/mod_manager.h"
#include <Windows.h>
#include <miniz.h>
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <vector>

using namespace dingosdk;
namespace fs = std::filesystem;
namespace ts = thunderstore_listing;

// What the browser asks of the game, which is not here: nothing is being applied, no map is being
// downloaded for a session, and preview() reasons as live_mods does with nothing yet in effect.
namespace dingosdk::live_mods {
bool busy() noexcept { return false; }
std::string apply(bool) { return "Not applied: there is no game in this test."; }
ApplyEffect preview(const mods::ModList& list) { return preview_of(list, {}, {}); }
}
namespace dingosdk::map_download {
View view() { return {}; }
}

namespace {
int failures = 0;
ULONGLONG started = GetTickCount64();

void say(const std::string& text) {
    std::printf("[%7.1fs] %s\n", static_cast<double>(GetTickCount64() - started) / 1000.0, text.c_str());
    std::fflush(stdout);
}
void check(bool condition, const std::string& what) {
    say(std::string(condition ? "ok   " : "FAIL ") + what);
    if (!condition) ++failures;
}
std::wstring wide(const std::string& text) { return std::wstring(text.begin(), text.end()); }
bool starts(const std::string& text, std::string_view prefix) { return text.starts_with(prefix); }
bool contains(std::string text, std::string_view word) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text.find(word) != std::string::npos;
}

fs::path mods_root() { return package_install::mods_root(); }
mods::ModList folder() { return mods::scan_mods(mods::engine_data_root()); }
std::vector<mod_browser::Row> rows(const mod_browser::Listing& listing, std::string_view query) {
    const auto officials = mods::official_packs(mods_root());
    return mod_browser::rows(listing, query, folder(), officials);
}
fs::path archive_of(const std::string& folder_name) {
    wchar_t temp[MAX_PATH + 1]{};
    const auto length = GetTempPathW(MAX_PATH, temp);
    return fs::path(std::wstring(temp, length)) /
           (L"ReSkate-package-" + std::to_wstring(GetCurrentProcessId()) + L"-" + wide(folder_name) + L".zip");
}
bool present(const fs::path& path) {
    std::error_code error;
    return fs::exists(path, error);
}

const char* stage_name(mod_browser::Stage stage) {
    using S = mod_browser::Stage;
    return stage == S::checking ? "checking" : stage == S::downloading ? "downloading" : stage == S::installing ? "installing"
         : stage == S::enabling ? "enabling" : stage == S::done ? "done" : stage == S::failed ? "failed" : "idle";
}
// Follows an install until it ends (or `until` says to stop following), saying each stage.
mod_browser::View follow(ULONGLONG timeout_ms, const std::function<bool(const mod_browser::View&)>& until = {}) {
    const auto begun = GetTickCount64();
    auto last = mod_browser::Stage::idle;
    ULONGLONG said_bytes{};
    for (;;) {
        const auto now = mod_browser::view();
        if (now.stage != last) {
            say(std::string("  stage ") + stage_name(now.stage) + " (" + now.folder + (now.version.empty() ? "" : " v" + now.version) + ")");
            last = now.stage;
        }
        if (now.stage == mod_browser::Stage::downloading && GetTickCount64() - said_bytes > 2000) {
            said_bytes = GetTickCount64();
            say("  " + ts::size_text(now.received) + (now.total ? " of " + ts::size_text(now.total) : std::string()));
        }
        if (now.stage == mod_browser::Stage::done || now.stage == mod_browser::Stage::failed) return now;
        if (until && until(now)) return now;
        if (GetTickCount64() - begun > timeout_ms) {
            say("  timed out; cancelling");
            mod_browser::cancel();
            timeout_ms += 60000;
        }
        Sleep(100);
    }
}
// The claim on the Mods folder goes once the install's thread has ended.
bool claim_released() {
    for (int i = 0; i < 100 && mods_writer::claim().held(); ++i) Sleep(100);
    return !mods_writer::claim().held();
}
bool staging_left() { return present(mods_root() / L".reskate-install"); }

// A stored (uncompressed) zip of `files`, written by hand: miniz is built here without its writer.
void write_zip(const fs::path& path, const std::vector<std::pair<std::string, std::string>>& files) {
    std::string out, central;
    const auto u16 = [](std::string& to, unsigned value) { to += static_cast<char>(value & 0xff); to += static_cast<char>(value >> 8 & 0xff); };
    const auto u32 = [&](std::string& to, std::uint32_t value) { u16(to, value & 0xffff); u16(to, value >> 16); };
    for (const auto& [name, data] : files) {
        const auto crc = static_cast<std::uint32_t>(mz_crc32(MZ_CRC32_INIT, reinterpret_cast<const unsigned char*>(data.data()), data.size()));
        const auto offset = static_cast<std::uint32_t>(out.size());
        const auto size = static_cast<std::uint32_t>(data.size());
        u32(out, 0x04034b50); u16(out, 20); u16(out, 0); u16(out, 0); u16(out, 0); u16(out, 0x21);
        u32(out, crc); u32(out, size); u32(out, size); u16(out, static_cast<unsigned>(name.size())); u16(out, 0);
        out += name;
        out += data;
        u32(central, 0x02014b50); u16(central, 20); u16(central, 20); u16(central, 0); u16(central, 0); u16(central, 0); u16(central, 0x21);
        u32(central, crc); u32(central, size); u32(central, size); u16(central, static_cast<unsigned>(name.size()));
        u16(central, 0); u16(central, 0); u16(central, 0); u16(central, 0); u32(central, 0); u32(central, offset);
        central += name;
    }
    const auto at = static_cast<std::uint32_t>(out.size());
    out += central;
    u32(out, 0x06054b50); u16(out, 0); u16(out, 0); u16(out, static_cast<unsigned>(files.size())); u16(out, static_cast<unsigned>(files.size()));
    u32(out, static_cast<std::uint32_t>(central.size())); u32(out, at); u16(out, 0);
    std::ofstream(path, std::ios::binary | std::ios::trunc) << out;
}
std::string error_of(const std::function<void()>& run) {
    try { run(); } catch (const std::exception& failure) { return failure.what(); }
    return {};
}

// a. The listing: the index and its gzipped chunks, the plain listing, and the browser's own read.
std::shared_ptr<const mod_browser::Listing> check_listing() {
    say("== a. listing");
    char value[128]{};
    const auto length = GetEnvironmentVariableA("RESKATE_THUNDERSTORE_COMMUNITY", value, sizeof(value));
    const auto community = ts::community(length && length < sizeof(value) ? std::string_view(value, length) : std::string_view());
    const auto get = [](const std::string& url, std::uint64_t most) {
        https::Download result;
        auto body = https::get_text(wide(url), most, 60, package_install::agent, &result);
        if (!body) throw std::runtime_error("GET " + url + " failed: HTTP " + std::to_string(result.http_status) + ", error " + std::to_string(result.error));
        return std::move(*body);
    };
    std::size_t from_index{}, plain{};
    const auto index_error = error_of([&] {
        const auto urls = ts::parse_index(ts::gunzip(get(ts::listing_index_url(community), 1 << 20), 4 << 20));
        say("  index: " + std::to_string(urls.size()) + " chunk(s)");
        for (const auto& url : urls) from_index += ts::parse_listing(ts::gunzip(get(url, 32ull << 20), 128ull << 20)).size();
    });
    check(index_error.empty() && from_index > 0, "the listing index and its gzipped chunks read: " + std::to_string(from_index) + " package(s)" +
          (index_error.empty() ? "" : " (" + index_error + ")"));
    const auto plain_error = error_of([&] { plain = ts::parse_listing(ts::gunzip(get(ts::listing_url(community), 64ull << 20), 128ull << 20)).size(); });
    check(plain_error.empty() && plain > 0, "the plain listing (the fallback) reads: " + std::to_string(plain) + " package(s)" +
          (plain_error.empty() ? "" : " (" + plain_error + ")"));

    // The browser's own read, as the card starts it.
    std::shared_ptr<const mod_browser::Listing> listing;
    for (int i = 0; i < 1200 && !(listing = mod_browser::listing()); ++i) Sleep(100);
    check(listing && listing->error.empty() && !listing->packages.empty(),
          "the browser reads the listing of " + community + ": " + (listing ? std::to_string(listing->packages.size()) : std::string("none")) +
          " package(s)" + (listing && !listing->error.empty() ? " (" + listing->error + ")" : ""));
    if (!listing || listing->packages.empty()) return nullptr;

    const auto all = rows(*listing, "");
    check(!all.empty() && all.size() <= listing->packages.size(), "the card lists " + std::to_string(all.size()) + " of them");
    check(std::none_of(all.begin(), all.end(), [](const mod_browser::Row& row) { return ts::nsfw(*row.package) || row.package->deprecated; }),
          "none marked NSFW, and no deprecated one that is not installed");
    for (std::size_t i = 0; i < all.size() && i < 5; ++i) say("  " + ts::row_text(*all[i].package, all[i].action, mod_browser::refusal(all[i])));
    if (!all.empty()) {
        const auto owner = all.front().package->owner;
        const auto found = rows(*listing, owner);
        check(!found.empty() && std::all_of(found.begin(), found.end(), [&](const mod_browser::Row& row) {
                  return contains(row.package->owner + " " + row.package->title() + " " + row.package->description, ts::folder_key(owner)) ||
                         std::any_of(row.package->categories.begin(), row.package->categories.end(), [&](const std::string& c) { return contains(c, ts::folder_key(owner)); });
              }),
              "searching for \"" + owner + "\" finds " + std::to_string(found.size()) + ", each of which has it");
    }
    check(rows(*listing, "zz-no-such-mod-zz").empty(), "a search nothing matches finds nothing");
    return listing;
}

bool in_category(const ts::Package& package, std::string_view category) {
    return std::any_of(package.categories.begin(), package.categories.end(), [&](const std::string& c) { return ts::folder_key(c) == category; });
}
const mod_browser::Row* row_named(const std::vector<mod_browser::Row>& all, std::string_view name) {
    const auto found = std::find_if(all.begin(), all.end(), [&](const mod_browser::Row& row) { return ts::folder_key(row.package->full_name) == ts::folder_key(name); });
    return found == all.end() ? nullptr : &*found;
}

// b and f. One small map, all the way into mods.json, and what applying it would do.
std::string check_install(const mod_browser::Listing& listing) {
    say("== b. install a small map");
    auto all = rows(listing, "");
    std::vector<const mod_browser::Row*> candidates;
    char wanted[160]{};
    if (const auto length = GetEnvironmentVariableA("RESKATE_LIVE_TEST_PACKAGE", wanted, sizeof(wanted)); length && length < sizeof(wanted)) {
        const auto* row = row_named(all, std::string_view(wanted, length));
        check(row != nullptr, std::string("RESKATE_LIVE_TEST_PACKAGE names a listed mod: ") + wanted);
        if (row) candidates.push_back(row);
    } else {
        for (const auto& row : all)
            if (in_category(*row.package, "maps") && row.action == ts::Action::install && row.needs.empty() && row.package->file_size)
                candidates.push_back(&row);
        std::sort(candidates.begin(), candidates.end(), [](const auto* a, const auto* b) { return a->package->file_size < b->package->file_size; });
    }
    check(!candidates.empty(), std::to_string(candidates.size()) + " map(s) to try");
    for (std::size_t i = 0; i < candidates.size() && i < 3; ++i) {
        const auto& row = *candidates[i];
        const auto folder_name = row.package->folder();
        say("  installing " + ts::row_text(*row.package, row.action, mod_browser::refusal(row)));
        const auto begun = GetTickCount64();
        const auto refused = mod_browser::install(row);
        check(refused.empty(), "the install starts" + (refused.empty() ? std::string() : ": " + refused));
        if (!refused.empty()) continue;
        const auto end = follow(30 * 60 * 1000);
        say("  took " + std::to_string((GetTickCount64() - begun) / 1000) + " s: " + end.message);
        const auto job = mod_browser::view();
        check(claim_released(), "the claim on the Mods folder is released");
        check(!staging_left() && !present(archive_of(folder_name)), "no staging folder or downloaded archive is left");
        if (end.stage != mod_browser::Stage::done) {
            // (A map holding more than the game may install from here is refused: the next is tried.)
            check(starts(job.last_error, "refused:") || starts(job.last_error, "failed:"), "it ended as " + job.last_error);
            check(!present(mods_root() / folder_name), "and nothing of it is in Mods");
            if (starts(job.last_error, "refused:")) continue;
            ++failures;
            continue;
        }
        check(starts(job.last_result, "done:"), "it is done: " + job.last_result);
        check(present(mods_root() / folder_name), "Mods/" + folder_name + " is there");
        const auto list = folder();
        const auto entry = std::find_if(list.entries.begin(), list.entries.end(), [&](const mods::ModEntry& e) { return e.mod.name == folder_name; });
        check(list.issue.empty() && entry != list.entries.end() && entry->enabled, "it is enabled in mods.json");
        const auto content = package_install::check_installed(folder_name, ts::refuse_contents);
        check(content.empty(), "the folder on disk passes the content check" + (content.empty() ? std::string() : ": " + content));
        const auto again = row_named(rows(listing, ""), folder_name);
        check(again && again->action == ts::Action::installed, "the card now shows it installed");

        say("== f. what applying it would do");
        const auto maps_only = live_mods::only_maps(mods_root() / folder_name);
        const auto effect = live_mods::preview(list);
        say(std::string("  preview: ") + live_mods::apply_label(effect) + (maps_only ? " (it holds maps only)" : " (it holds more than maps)"));
        check(effect == (maps_only ? live_mods::ApplyEffect::maps_only : live_mods::ApplyEffect::reload_level),
              "preview says what its contents call for");
        check(maps_only && effect == live_mods::ApplyEffect::maps_only, "the map applies without reloading the level (maps_only)");
        return folder_name;
    }
    check(false, "a map was installed");
    return {};
}

// e. A second install while one runs, and while another installer holds the Mods folder.
void check_claim(const mod_browser::Listing& listing) {
    say("== e. one installer at a time");
    const auto all = rows(listing, "");
    const auto other = std::find_if(all.begin(), all.end(), [](const mod_browser::Row& row) { return row.action == ts::Action::install && row.needs.empty(); });
    if (other == all.end()) return check(false, "a mod to try installing");
    check(mods_writer::claim().try_take(), "the claim is taken, as a map download for a session takes it");
    const auto refused = mod_browser::install(*other);
    check(!refused.empty() && !mod_browser::busy(), "an install is refused meanwhile: " + refused);
    check(mod_browser::refusal(*other) == refused, "with the reason its button shows");
    mods_writer::claim().release();
}

// c, and e while it runs. A large download, cancelled part way.
void check_cancel(const mod_browser::Listing& listing) {
    say("== c. cancel a download");
    auto all = rows(listing, "");
    std::vector<const mod_browser::Row*> candidates;
    for (const auto& row : all)
        if (row.action == ts::Action::install && row.needs.empty() && row.package->file_size > (4ull << 20)) candidates.push_back(&row);
    std::sort(candidates.begin(), candidates.end(), [](const auto* a, const auto* b) { return a->package->file_size > b->package->file_size; });
    if (candidates.empty()) return check(false, "a mod large enough to cancel part way");
    for (std::size_t i = 0; i < candidates.size() && i < 3; ++i) {
        const auto& row = *candidates[i];
        const auto folder_name = row.package->folder();
        say("  installing " + ts::row_text(*row.package, row.action, {}));
        if (const auto refused = mod_browser::install(row); !refused.empty()) {
            check(false, "the install starts: " + refused);
            continue;
        }
        const auto running = follow(5 * 60 * 1000, [](const mod_browser::View& now) {
            return now.stage == mod_browser::Stage::downloading && now.received >= (256u << 10);
        });
        if (running.stage != mod_browser::Stage::downloading) {
            say("  it did not get to downloading (" + mod_browser::view().last_error + "); trying another");
            claim_released();
            continue;
        }
        const auto second = std::find_if(all.begin(), all.end(), [&](const mod_browser::Row& r) { return &r != &row && r.action == ts::Action::install; });
        if (second != all.end()) {
            const auto refused = mod_browser::install(*second);
            check(!refused.empty(), "e. a second install while one runs is refused: " + refused);
        }
        say("  cancelling at " + ts::size_text(running.received));
        mod_browser::cancel();
        const auto end = follow(2 * 60 * 1000);
        const auto job = mod_browser::view();
        check(end.stage == mod_browser::Stage::failed && starts(job.last_error, "cancelled:"), "it ends cancelled: " + job.last_error);
        check(!present(mods_root() / folder_name), "no folder of it is in Mods");
        check(!staging_left() && !present(archive_of(folder_name)), "no staging folder or downloaded archive is left");
        check(claim_released(), "the claim is released");
        return;
    }
    check(false, "a download was cancelled part way");
}

// d. What may not be installed from the game is refused: a real package if the listing has one,
// and archives made here, always.
void check_refusals(const mod_browser::Listing& listing) {
    say("== d. refusals");
    const auto all = rows(listing, "");
    std::vector<const mod_browser::Row*> likely;
    for (const auto& row : all) {
        const auto& p = *row.package;
        std::string text = p.full_name + " " + p.description;
        for (const auto& c : p.categories) text += " " + c;
        if (row.action == ts::Action::install && row.needs.empty() && p.file_size && p.file_size < (200ull << 20) &&
            (contains(text, "lua") || contains(text, "dll") || contains(text, "script") || contains(text, "plugin") || contains(text, "bepinex")))
            likely.push_back(&row);
    }
    std::sort(likely.begin(), likely.end(), [](const auto* a, const auto* b) { return a->package->file_size < b->package->file_size; });
    bool found{};
    for (std::size_t i = 0; i < likely.size() && i < 3 && !found; ++i) {
        const auto& row = *likely[i];
        say("  trying " + ts::row_text(*row.package, row.action, {}));
        if (!mod_browser::install(row).empty()) continue;
        follow(15 * 60 * 1000);
        claim_released();
        const auto job = mod_browser::view();
        if (starts(job.last_error, "refused:") && (contains(job.last_error, ".dll") || contains(job.last_error, ".lua"))) {
            found = true;
            check(!present(mods_root() / row.package->folder()) && !staging_left(), "refused: " + job.last_error + "; nothing of it is in Mods");
        } else {
            say("  not one: " + (job.stage == mod_browser::Stage::done ? job.last_result : job.last_error));
        }
    }
    say(std::string("  a real package with a DLL or Lua ") + (found ? "was found and refused." : "was not found in the listing."));

    // Made here: what the archive holds is refused before anything is written (the accept hook).
    const thunderstore_package::Name fake{"Live_Test", "Synthetic", ""};
    std::atomic<bool> stop{};
    {
        const package_install::Archive archive(fake.folder());
        write_zip(archive.path(), {{"manifest.json", R"({"name":"Synthetic","version_number":"1.0.0"})"}, {"layout.toc", "toc"},
                                   {"Win32/levels/fake.toc", "toc"}, {"evil.dll", "MZ"}});
        const auto refused = error_of([&] { package_install::install(archive, fake, false, {}, stop, ts::refuse_contents); });
        check(refused.find("evil.dll") != std::string::npos, "an archive holding a DLL is refused: " + refused);
        check(!present(mods_root() / fake.folder()) && !staging_left(), "and nothing of it is written");
    }
    {
        const package_install::Archive archive(fake.folder());
        write_zip(archive.path(), {{"layout.toc", "toc"}, {"../outside.toc", "toc"}});
        const auto refused = error_of([&] { package_install::install(archive, fake, false, {}, stop, ts::refuse_contents); });
        check(!refused.empty() && !present(mods_root().parent_path() / L"outside.toc"), "an archive with a path out of the folder is refused: " + refused);
    }
    {
        // And the folder on disk is checked again after an install: a script put there afterwards is refused.
        const package_install::Archive archive(fake.folder());
        write_zip(archive.path(), {{"manifest.json", R"({"name":"Synthetic","version_number":"1.0.0"})"}, {"layout.toc", "toc"},
                                   {"Win32/levels/fake.toc", "toc"}});
        const auto refused = error_of([&] { package_install::install(archive, fake, false, {}, stop, ts::refuse_contents); });
        check(refused.empty() && package_install::check_installed(fake.folder(), ts::refuse_contents).empty(), "a clean archive installs and passes");
        std::ofstream(mods_root() / fake.folder() / L"init.lua") << "print('hi')";
        const auto after = package_install::check_installed(fake.folder(), ts::refuse_contents);
        check(after.find("init.lua") != std::string::npos, "a script in the installed folder is refused: " + after);
        std::error_code error;
        fs::remove_all(mods_root() / fake.folder(), error);
    }
}

int child() {
    const auto root = mods::engine_data_root();
    logging::Options options;
    options.directory = root / L"logs";
    options.truncate_file = true;
    logging::initialize(options);
    say("data root " + root.string());
    check(present(mods_root() / mods::order_file), "the data root has an empty Mods folder and mods.json");
    if (const auto listing = check_listing()) {
        check_install(*listing);
        check_claim(*listing);
        check_cancel(*listing);
        check_refusals(*listing);
    }
    logging::flush();
    say(failures ? std::to_string(failures) + " check(s) failed." : "Every check passed.");
    return failures ? 1 : 0;
}

int parent(int argc, wchar_t** argv) {
    fs::path log;
    for (int i = 1; i + 1 < argc; ++i)
        if (std::wstring_view(argv[i]) == L"--log") log = argv[i + 1];
    wchar_t temp[MAX_PATH + 1]{};
    const auto length = GetTempPathW(MAX_PATH, temp);
    const auto root = fs::path(std::wstring(temp, length)) / (L"reskate-mod-browser-live-" + std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    fs::remove_all(root, error);
    fs::create_directories(root / mods::mods_folder);
    mods::save_mod_order(root / mods::mods_folder, {});

    std::wstring command = L"\"" + std::wstring(argv[0]) + L"\" \"-dataPath=" + root.wstring() + L"\" --child";
    STARTUPINFOW startup{sizeof(startup)};
    PROCESS_INFORMATION process{};
    DWORD code = 2;
    if (CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &process)) {
        WaitForSingleObject(process.hProcess, INFINITE);
        GetExitCodeProcess(process.hProcess, &code);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    } else {
        std::printf("Could not start the test (Windows error %lu).\n", GetLastError());
    }
    if (!log.empty()) fs::copy_file(root / L"logs" / L"ReSkate.log", log, fs::copy_options::overwrite_existing, error);
    for (int i = 0; i < 20; ++i) {
        fs::remove_all(root, error);
        if (!present(root)) break;
        Sleep(500);
    }
    if (present(root)) {
        std::printf("FAIL the data root could not be removed: %s\n", root.string().c_str());
        code = code ? code : 1;
    }
    return static_cast<int>(code);
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    for (int i = 1; i < argc; ++i)
        if (std::wstring_view(argv[i]) == L"--child") return child();
    return parent(argc, argv);
}
