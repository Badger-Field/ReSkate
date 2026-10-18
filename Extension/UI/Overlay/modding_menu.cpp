#include "modding_menu.h"
#include "skate_menu.h"
#include "skate_menu_internal.h"
#include "Engine/Vfs/initfs.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Extension/Assets/live_mods.h"
#include "Extension/Assets/mod_browser.h"
#include "Extension/BoardBreak/board_break.h"
#include "Extension/UI/skate_theme.h"
#include "Engine/Core/Log/logging.h"

#include <Windows.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <format>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>
#include <stdexcept>
#include <string>
#include <vector>

namespace dingosdk::overlay {
namespace {
// What the Mods folder holds, read again on a thread of its own every `period` while a page asks
// for it; the page draws the last that was read. These pages are drawn on the game's render
// thread, and reading the folder there (every mod's files, a whole folder's fingerprint for an
// excluded one) held each frame up for tens of milliseconds to seconds, every 1.5 s (2026-10-15).
template<class T> class FolderRead {
public:
    template<class Read> std::shared_ptr<const T> get(Read read, ULONGLONG period) {
        const auto now = GetTickCount64();
        if (now >= next_.load() && !reading_.exchange(true)) {
            next_ = now + period;
            try {
                std::thread([this, read] {
                    std::shared_ptr<const T> fresh;
                    try { fresh = std::make_shared<const T>(read()); } catch (...) {}
                    if (fresh) {
                        std::lock_guard lock(mutex_);
                        value_ = std::move(fresh);
                    }
                    reading_ = false;
                }).detach();
            } catch (...) { reading_ = false; }
        }
        std::lock_guard lock(mutex_);
        return value_;
    }
    // What the page itself has just changed, shown until the next read.
    void set(T value) {
        std::lock_guard lock(mutex_);
        value_ = std::make_shared<const T>(std::move(value));
    }
    void again() { next_ = 0; }
private:
    std::mutex mutex_;
    std::shared_ptr<const T> value_;
    std::atomic<bool> reading_{};
    std::atomic<ULONGLONG> next_{};
};
struct DeckScan {
    std::vector<std::string> installed, titles; // each installed deck's name for people
    std::string chosen;
};
}
namespace menu {
// Board Break's decks (Extension/BoardBreak/board_break.h). The game has one deck model and each
// deck is a mod's copy of it, so picking one applies the mods with that deck first and loads the
// level again. A button for each deck the feature was made with, greyed out while it is not
// installed, and for any other deck that is; the Mods folder is read again every 1.5 s while the
// card shows. A deck goes by the name its deck.ini gives it (label), else by its folder's.
void board_break_deck_card(SkateMenu& menu, const Model& model, const CallbacksV3& callbacks) {
    static auto* scans = new FolderRead<DeckScan>; // process lifetime, as the threads that fill it
    const auto scan = scans->get([] {
        DeckScan read;
        read.installed = mods::installed_board_break_decks();
        read.chosen = mods::wanted_board_break_deck();
        for (const auto& deck : read.installed) read.titles.push_back(board_break::deck_label(deck));
        return read;
    }, 1500);
    if (!scan) return;
    const auto& installed = scan->installed;
    const auto& titles = scan->titles;
    const auto& chosen = scan->chosen;
    if (!model.board_break.available || installed.empty()) return;
    const auto there = [&](const std::string& deck) { return std::find(installed.begin(), installed.end(), deck) != installed.end(); };
    // The known decks in their own order, then the others that are installed: the numbers are the
    // ones `boardbreak deck` prints.
    std::vector<std::string> decks(mods::board_break_decks.begin(), mods::board_break_decks.end());
    for (const auto& deck : installed)
        if (std::find(decks.begin(), decks.end(), deck) == decks.end()) decks.push_back(deck);
    const auto label = [&](const std::string& deck) {
        std::string name;
        if (const auto at = std::find(installed.begin(), installed.end(), deck);
            at != installed.end() && static_cast<std::size_t>(at - installed.begin()) < titles.size())
            name = titles[static_cast<std::size_t>(at - installed.begin())];
        else {
            name = deck;
            bool start = true;
            for (auto& c : name) {
                if (c == '_') c = ' ';
                else if (start && c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
                start = c == ' ';
            }
        }
        const auto place = std::find(decks.begin(), decks.end(), deck) - decks.begin() + 1;
        return std::to_string(place) + " " + name;
    };
    const auto in_play = mods::board_break_in_play();
    begin_card(menu, "board-break-decks", "BOARD BREAK DECK", in_play.empty() ? "none in the game" : label(in_play).c_str());
    const bool busy = live_mods::busy();
    const float gap = ImGui::GetStyle().ItemSpacing.x;
    const float width = (ImGui::GetContentRegionAvail().x - gap * 3) / 4;
    for (std::size_t i = 0; i < decks.size(); ++i) {
        if (i % 4) ImGui::SameLine();
        const auto& deck = decks[i];
        ImGui::PushID(static_cast<int>(i));
        ImGui::BeginDisabled(!there(deck) || busy || deck == in_play || !callbacks.queue_console_command);
        if (ImGui::Button((label(deck) + (deck == in_play ? " *" : "")).c_str(), ImVec2(width, 0)))
            send_console(menu, callbacks, "boardbreak deck " + deck);
        ImGui::EndDisabled();
        ImGui::PopID();
    }
    if (busy) note("Applying mods...");
    else if (!in_play.empty() && chosen != in_play && there(chosen))
        note((label(chosen) + " is chosen and shows once the level has loaded again.").c_str());
    note("The deck every board in the game has, and the one that breaks. Picking another loads the level again; "
         "decks that are not installed are greyed out.");
    end_card();
}
}
namespace {
std::filesystem::path game_directory() {
    std::array<wchar_t, 32768> executable{};
    const auto length = GetModuleFileNameW(nullptr, executable.data(), static_cast<DWORD>(executable.size()));
    if (!length || length >= executable.size()) throw std::runtime_error("Cannot locate the game directory.");
    return std::filesystem::path(std::wstring(executable.data(), length)).parent_path();
}
// What a mod brings, as the native Mods page words it.
std::string mod_contents(const mods::Mod& mod) {
    std::string text = mod.provides_layout && mod.provides_levels ? "Maps + game data"
                     : mod.provides_levels ? "Maps" : mod.provides_layout ? "Game data" : "";
    if (!mod.park_maps.empty()) text += text.empty() ? "Parks" : " + parks";
    return text.empty() ? "Nothing to load" : text;
}
// Every mod folder and what mods.json says about it, re-read every 1.5 s while
// the card is open, so a folder added or an edit made outside the game shows
// up. Enabling or disabling saves mods.json; "Apply now" merges the change in
// while the game runs (it takes effect at the next level load), including a
// mod folder added since launch.
// The Mods folder as INSTALLED MODS and GET MODS see it.
FolderRead<mods::ModList>& installed_scans() {
    static auto* scans = new FolderRead<mods::ModList>; // process lifetime, as the threads that fill it
    return *scans;
}
std::shared_ptr<const mods::ModList> installed_list() {
    return installed_scans().get([] { return mods::scan_mods(mods::engine_data_root()); }, 1500);
}
void draw_installed_mods(SkateMenu& menu) {
    auto* scans = &installed_scans();
    static std::string action_error;
    const auto scanned = installed_list();
    if (!scanned) {
        menu::note("Reading the Mods folder...");
        return;
    }
    const auto& list = *scanned;
    const auto& launch = mods::catalog();
    const auto applied = live_mods::applied_mods();
    // A Board Break deck pack is in effect when one of the decks inside it is (mod_catalog.h).
    const auto in_effect = [&](const std::string& name) {
        return std::ranges::any_of(applied, [&](const std::string& other) {
            return _stricmp(other.c_str(), name.c_str()) == 0 || _stricmp(mods::board_break_pack_of(other).c_str(), name.c_str()) == 0;
        });
    };
    const auto excluded = [&](const std::string& name) {
        return std::ranges::any_of(launch.excluded, [&](const mods::Mod& mod) { return _stricmp(mod.name.c_str(), name.c_str()) == 0; });
    };
    // Board Break's other decks and other mods that bring a deck model: enabled, not loaded, and
    // nothing about them waits to be applied.
    const auto aside = mods::set_aside_for_board_break();
    const auto set_aside = [&](const mods::ModEntry& entry) {
        return entry.enabled && !in_effect(entry.mod.name) &&
               std::ranges::any_of(aside, [&](const std::string& other) { return _stricmp(other.c_str(), entry.mod.name.c_str()) == 0; });
    };
    std::size_t loaded{}, pending{};
    for (const auto& entry : list.entries) {
        const bool effect = in_effect(entry.mod.name);
        if (effect) ++loaded;
        if (entry.enabled != effect && !excluded(entry.mod.name) && !set_aside(entry)) ++pending;
    }
    // Mods still in effect whose folder has been deleted: applying unloads them.
    std::vector<std::string> removed;
    for (const auto& name : applied)
        if (mods::board_break_pack_of(name).empty() &&
            std::ranges::none_of(list.entries, [&](const mods::ModEntry& entry) { return _stricmp(entry.mod.name.c_str(), name.c_str()) == 0; }))
            removed.push_back(name);
    pending += removed.size();
    loaded += removed.size();
    std::string count = std::to_string(loaded) + " loaded";
    if (!launch.excluded.empty()) count += ", " + std::to_string(launch.excluded.size()) + " not loaded";
    menu::begin_card(menu, "installed-mods", "INSTALLED MODS", count.c_str());

    // Unapplied changes first, with the button that applies them.
    if (live_mods::busy()) {
        menu::note("Applying mods...");
    } else if (pending) {
        menu::warn((std::to_string(pending) + " change(s) not applied yet.").c_str());
        // One button, labelled by what the changes do to the level being played:
        // maps only just need applying; anything the level shows needs it
        // reloaded; taking away the map being played sends the player to San Van.
        const auto effect = live_mods::preview(list);
        // (Not while a mod from GET MODS is going into the Mods folder.)
        ImGui::BeginDisabled(mod_browser::busy());
        if (ImGui::Button(live_mods::apply_label(effect), ImVec2(-FLT_MIN, 0))) {
            action_error.clear();
            menu::feedback(menu, live_mods::apply(effect == live_mods::ApplyEffect::reload_level).c_str());
        }
        ImGui::EndDisabled();
    }
    if (const auto last = live_mods::status(); !last.empty()) menu::note(("Last apply: " + last).c_str());

    auto* draw = ImGui::GetWindowDrawList();
    // A mod's tile; the button on its right enables or disables it.
    const auto tile = [&](int index, const std::string& title, const std::string& byline, const char* status,
                          ImU32 colour, const std::string& contents, const std::string& hover, const char* action) {
        bool clicked{};
        ImGui::PushID(index);
        const float button_width = action ? menu::px(96) : 0.f;
        const auto top = ImGui::GetCursorScreenPos();
        const float width = ImGui::GetContentRegionAvail().x - (action ? button_width + menu::px(8) : 0.f);
        const float height = menu::px(54), pad = menu::px(14);
        skate_theme::rough_rect(draw, top, ImVec2(top.x + width, top.y + height), skate_theme::tile_grey,
                                static_cast<unsigned>(index + 151), menu::px(1));
        float right = top.x + width - pad;
        const auto contents_size = menu.body->CalcTextSizeA(menu::px(14), FLT_MAX, 0, contents.c_str());
        right -= contents_size.x;
        draw->AddText(menu.body, menu::px(14), ImVec2(right, top.y + (height - contents_size.y) * .5f),
                      skate_theme::grey_text, contents.c_str());
        right -= menu::px(14);
        const float status_width = menu.bold->CalcTextSizeA(menu::px(12), FLT_MAX, 0, status).x + menu::px(14);
        right -= status_width;
        ImGui::SetCursorScreenPos(ImVec2(right, top.y + (height - ImGui::GetFrameHeight()) * .5f));
        menu::tag(menu, status, colour);
        right -= menu::px(14);
        ImGui::SetCursorScreenPos(ImVec2(top.x + pad, top.y + menu::px(8)));
        ImGui::PushClipRect(ImGui::GetCursorScreenPos(), ImVec2(right, top.y + height), true);
        ImGui::BeginGroup();
        ImGui::PushFont(menu.bold);
        ImGui::TextUnformatted(title.c_str());
        ImGui::PopFont();
        ImGui::PushStyleColor(ImGuiCol_Text, skate_theme::grey_text);
        ImGui::TextUnformatted(byline.c_str());
        ImGui::PopStyleColor();
        ImGui::EndGroup();
        ImGui::PopClipRect();
        ImGui::SetCursorScreenPos(top);
        ImGui::Dummy(ImVec2(width, height));
        if (!hover.empty() && ImGui::IsItemHovered()) {
            ImGui::BeginTooltip();
            ImGui::PushTextWrapPos(ImGui::GetFontSize() * 28);
            ImGui::TextUnformatted(hover.c_str());
            ImGui::PopTextWrapPos();
            ImGui::EndTooltip();
        }
        if (action) {
            ImGui::SameLine(0, menu::px(8));
            ImGui::SetCursorScreenPos(ImVec2(top.x + width + menu::px(8), top.y + (height - ImGui::GetFrameHeight()) * .5f));
            clicked = ImGui::Button(action, ImVec2(button_width, 0));
            ImGui::SetCursorScreenPos(ImVec2(top.x, top.y + height + ImGui::GetStyle().ItemSpacing.y));
            ImGui::Dummy(ImVec2(0, 0));
        }
        ImGui::PopID();
        return clicked;
    };
    const auto byline = [](const mods::Mod& mod) {
        std::string text = mod.author.empty() ? "Unknown author" : "by " + mod.author;
        if (!mod.version.empty()) text += "  -  v" + mod.version;
        return text;
    };
    std::optional<std::size_t> toggle;
    int index{};
    for (std::size_t i = 0; i < list.entries.size(); ++i) {
        const auto& entry = list.entries[i];
        const auto& mod = entry.mod;
        const bool effect = in_effect(mod.name);
        const char* status = "DISABLED";
        ImU32 colour = skate_theme::tile_light;
        std::string hover = mod.description;
        if (excluded(mod.name)) {
            status = "NOT LOADED";
            colour = skate_theme::danger;
            hover = "Not loaded: it could not be merged cleanly, so none of it is used.\n\n"
                    "Reinstall the whole mod folder, or rebuild it with a current ReSkate Studio.";
        } else if (set_aside(entry)) {
            status = "SET ASIDE";
            hover = "Set aside while a Board Break deck is the one in the game: the game has one deck model, so one "
                    "mod's deck loads at a time. Pick another deck under BOARD BREAK DECK, or disable Board Break's decks.";
        } else if (entry.enabled && effect) {
            status = "LOADED";
            colour = skate_theme::good;
        } else if (entry.enabled) {
            status = "APPLY TO LOAD";
            colour = skate_theme::danger;
        } else if (effect) {
            status = "APPLY TO UNLOAD";
            colour = skate_theme::danger;
        }
        if (tile(index++, mod.title.empty() ? mod.name : mod.title, byline(mod), status, colour, mod_contents(mod), hover,
                 entry.enabled ? "Disable" : "Enable"))
            toggle = i;
    }
    for (const auto& name : removed)
        tile(index++, name, "Folder deleted", "APPLY TO UNLOAD", skate_theme::danger, "",
             "This mod's folder is gone, but it stays loaded until you apply.", nullptr);
    if (toggle && list.issue.empty()) {
        try {
            auto entries = list.entries;
            entries[*toggle].enabled = !entries[*toggle].enabled;
            mods::save_mod_order(list.root, entries);
            auto shown = list;
            shown.entries = std::move(entries);
            scans->set(std::move(shown));
            scans->again();
            action_error.clear();
        } catch (const std::exception& error) { action_error = error.what(); }
    }
    if (!index) menu::note(list.present ? "No mods installed yet." : "No Mods folder yet. Create one next to Skate.exe.");
    if (!list.issue.empty()) menu::warn(("mods.json problem: " + list.issue).c_str());
    if (!action_error.empty()) menu::warn(("Could not save mods.json: " + action_error).c_str());
    menu::note("Order mods in the launcher's Mods panel. Maps, loading screens and cosmetics change at the next level load; "
               "other changes to game settings or the UI take effect after a restart.");
    menu::end_card();
}
std::string megabytes(std::uint64_t bytes) {
    const auto mb = static_cast<double>(bytes) / (1024.0 * 1024.0);
    return mb >= 1024.0 ? std::format("{:.2f} GB", mb / 1024.0) : mb >= 1.0 ? std::format("{:.0f} MB", mb)
                                                                          : std::format("{} KB", std::max<std::uint64_t>(1, bytes / 1024));
}
// ReSkate's Thunderstore community (Extension/Assets/mod_browser.h): a search box, the packages that
// match, and Install or Update on each. The listing is read in the background the first time the
// card shows and kept; an install runs on a thread of its own and shows here how far it is. Once it
// is in, the mod is switched on, and the Apply button here (the same one INSTALLED MODS has) loads it;
// the level is never reloaded without the player asking.
void draw_get_mods(SkateMenu& menu) {
    static std::array<char, 96> query{};
    const auto listing = mod_browser::listing();
    const auto scanned = installed_list();
    const auto job = mod_browser::view();
    // A finished install: the Mods folder is read again, so its tile and the Apply button show it.
    static mod_browser::Stage last_stage{};
    if (job.stage != last_stage && (job.stage == mod_browser::Stage::done || job.stage == mod_browser::Stage::failed))
        installed_scans().again();
    last_stage = job.stage;

    const auto subtitle = !listing ? std::string("reading Thunderstore...")
                                   : std::to_string(listing->packages.size()) + " on Thunderstore";
    menu::begin_card(menu, "get-mods", "GET MODS", subtitle.c_str());

    // What is being installed, or what came of it.
    using Stage = mod_browser::Stage;
    if (mod_browser::busy()) {
        const auto name = job.title + (job.version.empty() ? std::string() : " v" + job.version);
        std::string status;
        float share = -1.f;
        switch (job.stage) {
        case Stage::checking: status = "Looking at " + name + " on Thunderstore..."; break;
        case Stage::downloading:
            status = "Downloading " + name + "  -  " + (job.total ? megabytes(job.received) + " of " + megabytes(job.total) : megabytes(job.received));
            if (job.total) share = static_cast<float>(static_cast<double>(job.received) / static_cast<double>(job.total));
            break;
        case Stage::installing:
            status = "Installing " + name + " into Mods...";
            share = job.installed;
            break;
        default: status = "Switching " + name + " on..."; break;
        }
        menu::note(status.c_str());
        // (Moving back and forth while the size or the progress is not known.)
        ImGui::ProgressBar(share >= 0 ? std::clamp(share, 0.f, 1.f) : -static_cast<float>(ImGui::GetTime()), ImVec2(-FLT_MIN, 0), "");
        ImGui::BeginDisabled(job.stage == Stage::enabling);
        if (ImGui::Button("Cancel", ImVec2(-FLT_MIN, 0))) mod_browser::cancel();
        ImGui::EndDisabled();
    } else if (job.stage == Stage::done || job.stage == Stage::failed) {
        if (job.stage == Stage::failed) menu::warn(job.message.c_str());
        else menu::note(job.message.c_str());
        const auto applied = live_mods::applied_mods();
        const bool in_effect = std::ranges::any_of(applied, [&](const std::string& name) { return _stricmp(name.c_str(), job.folder.c_str()) == 0; });
        if (live_mods::busy()) {
            const auto now = live_mods::progress();
            menu::note(now.total ? std::format("Applying mods... ({} of {}: {})", now.done, now.total, now.step).c_str() : "Applying mods...");
        } else if (job.stage == Stage::done && !in_effect) {
            // The same button as INSTALLED MODS: it applies every change waiting, worded by what that does.
            // (Once the folder has been read again with the mod in it, so the wording counts it.)
            const bool listed = scanned && std::ranges::any_of(scanned->entries, [&](const mods::ModEntry& entry) {
                return entry.enabled && _stricmp(entry.mod.name.c_str(), job.folder.c_str()) == 0;
            });
            if (!listed) {
                menu::note("Reading the Mods folder...");
            } else {
                const auto effect = live_mods::preview(*scanned);
                if (ImGui::Button(live_mods::apply_label(effect), ImVec2(-FLT_MIN, 0)))
                    menu::feedback(menu, live_mods::apply(effect == live_mods::ApplyEffect::reload_level).c_str());
            }
        } else if (job.stage == Stage::done) {
            menu::note("It is loaded. Maps show in the map list; other changes show at the next level load.");
        }
        if (ImGui::Button("OK", ImVec2(-FLT_MIN, 0))) mod_browser::dismiss();
    }

    // Search and refresh on one row.
    const auto& style = ImGui::GetStyle();
    const float refresh_width = ImGui::CalcTextSize("Refreshing...").x + style.FramePadding.x * 2;
    ImGui::SetNextItemWidth(std::max(menu::px(120), ImGui::GetContentRegionAvail().x - refresh_width - style.ItemSpacing.x));
    ImGui::InputTextWithHint("##get-mods-search", "Search mods by name, author or category", query.data(), query.size());
    ImGui::SameLine();
    ImGui::BeginDisabled(mod_browser::reading());
    if (ImGui::Button(mod_browser::reading() ? "Refreshing...###get-mods-refresh" : "Refresh###get-mods-refresh", ImVec2(refresh_width, 0)))
        mod_browser::listing(true);
    ImGui::EndDisabled();
    if (!listing) {
        menu::note("Reading ReSkate's mods on Thunderstore...");
        menu::end_card();
        return;
    }
    if (!listing->error.empty()) menu::warn(("Thunderstore could not be read: " + listing->error).c_str());

    // What matches, worked out again only when the query, the listing or the Mods folder changes.
    thunderstore_listing::InstalledMods installed;
    if (scanned)
        for (const auto& entry : scanned->entries)
            installed[thunderstore_listing::folder_key(entry.mod.name)] = {entry.mod.version, entry.enabled};
    static struct {
        std::shared_ptr<const mod_browser::Listing> listing;
        std::shared_ptr<const mods::ModList> scanned;
        std::string query;
        std::vector<const thunderstore_listing::Package*> rows;
    } shown;
    if (shown.listing != listing || shown.scanned != scanned || shown.query != query.data()) {
        shown.listing = listing;
        shown.scanned = scanned;
        shown.query = query.data();
        shown.rows = thunderstore_listing::search(listing->packages, shown.query, installed);
    }
    const auto& rows = shown.rows;
    if (rows.empty()) {
        menu::note(listing->packages.empty() ? "No mods on Thunderstore yet." : "No mod matches that search.");
        menu::end_card();
        return;
    }

    const auto blocked = mod_browser::blocked();
    const float row_height = menu::px(66), button_width = menu::px(132), pad = menu::px(12);
    // (Each row is its height and the spacing after it, which the clipper skips rows by.)
    const float row_step = row_height + style.ItemSpacing.y;
    ImGui::BeginChild("get-mods-list", ImVec2(0, std::min(row_step * static_cast<float>(rows.size()), menu::px(400))));
    auto* draw = ImGui::GetWindowDrawList();
    ImGuiListClipper clipper;
    clipper.Begin(static_cast<int>(rows.size()), row_step);
    while (clipper.Step())
        for (int i = clipper.DisplayStart; i < clipper.DisplayEnd; ++i) {
            const auto& package = *rows[static_cast<std::size_t>(i)];
            ImGui::PushID(package.full_name.c_str());
            const auto top = ImGui::GetCursorScreenPos();
            const float width = ImGui::GetContentRegionAvail().x;
            skate_theme::rough_rect(draw, top, ImVec2(top.x + width, top.y + row_height - menu::px(4)), skate_theme::tile_grey,
                                    static_cast<unsigned>(i + 311), menu::px(1));
            // Name, then author, version and categories, then what it says of itself.
            ImGui::PushClipRect(top, ImVec2(top.x + width - button_width - pad * 2, top.y + row_height), true);
            ImGui::SetCursorScreenPos(ImVec2(top.x + pad, top.y + menu::px(6)));
            ImGui::BeginGroup();
            ImGui::PushFont(menu.bold);
            ImGui::TextUnformatted(package.title().c_str());
            ImGui::PopFont();
            std::string byline = "by " + package.owner + "  -  v" + package.version;
            for (std::size_t c = 0; c < package.categories.size() && c < 3; ++c) byline += (c ? ", " : "  -  ") + package.categories[c];
            if (package.file_size) byline += "  -  " + megabytes(package.file_size);
            ImGui::PushStyleColor(ImGuiCol_Text, skate_theme::grey_text);
            ImGui::TextUnformatted(byline.c_str());
            ImGui::TextUnformatted(package.description.c_str());
            ImGui::PopStyleColor();
            ImGui::EndGroup();
            ImGui::PopClipRect();
            ImGui::SetCursorScreenPos(top);
            ImGui::Dummy(ImVec2(width - button_width - pad * 2, row_height - menu::px(4)));
            if (!package.description.empty() && ImGui::IsItemHovered()) {
                ImGui::BeginTooltip();
                ImGui::PushTextWrapPos(ImGui::GetFontSize() * 28);
                ImGui::TextUnformatted(package.description.c_str());
                ImGui::PopTextWrapPos();
                ImGui::EndTooltip();
            }
            // Its action on the right.
            const auto action = thunderstore_listing::action(package, installed);
            ImGui::SetCursorScreenPos(ImVec2(top.x + width - button_width - pad, top.y + (row_height - menu::px(4) - ImGui::GetFrameHeight()) * .5f));
            if (action == thunderstore_listing::Action::installed) {
                menu::tag(menu, "INSTALLED", skate_theme::good);
            } else {
                const auto missing = thunderstore_listing::missing_dependencies(package, installed);
                const auto why = !blocked.empty() ? blocked
                               : !missing.empty() ? "It needs " + missing.front() + " first: install it from the launcher, which brings what a mod needs."
                                                  : std::string();
                ImGui::BeginDisabled(!why.empty());
                const auto label = action == thunderstore_listing::Action::update ? "Update to v" + package.version : std::string("Install");
                if (ImGui::Button(label.c_str(), ImVec2(button_width, 0))) mod_browser::install(package);
                ImGui::EndDisabled();
                if (!why.empty() && ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) ImGui::SetTooltip("%s", why.c_str());
            }
            ImGui::SetCursorScreenPos(ImVec2(top.x, top.y + row_height));
            ImGui::Dummy(ImVec2(0, 0));
            ImGui::PopID();
        }
    clipper.End();
    ImGui::EndChild();
    menu::note("Mods from ReSkate's Thunderstore community. Only mods made of game data, maps and parks install from here; "
               "the launcher installs the rest, and what a mod needs. Maps, loading screens and cosmetics load when applied; "
               "other changes to game settings or the UI take effect after a restart.");
    menu::end_card();
}
void draw_custom_scripts(SkateMenu& menu) {
    menu::begin_card(menu, "custom-scripts", "CUSTOM SCRIPTS");
    menu::note("Drop .lua files or folders with init.lua into scripts/Custom/. Scripts and saved changes load on the next launch.");
    if (ImGui::Button("Refresh script list", ImVec2(-FLT_MIN, 0))) menu.custom_scripts_scanned = false;
    if (!menu.custom_scripts_scanned) {
        menu.custom_scripts_scanned = true;
        menu.custom_script_rows.clear();
        menu.custom_script_error.clear();
        try { menu.custom_script_rows = custom_scripts::discover(game_directory()); }
        catch (const std::exception& error) { menu.custom_script_error = error.what(); }
    }
    if (!menu.loose_files_saved) menu::warn("Turn on loose Lua / config files in the launcher to load custom scripts.");
    menu::note(custom_scripts::status("").c_str());
    if (!menu.custom_script_error.empty()) menu::warn(("Script settings error: " + menu.custom_script_error).c_str());
    if (menu.custom_script_rows.empty()) {
        menu::note("No custom scripts found. Names starting with _ or . are skipped.");
        menu::end_card();
        return;
    }
    std::optional<std::pair<std::size_t, std::size_t>> move;
    if (ImGui::BeginTable("custom_scripts", 4, ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("On", ImGuiTableColumnFlags_WidthFixed, menu::px(32));
        ImGui::TableSetupColumn("Script", ImGuiTableColumnFlags_WidthStretch, 1.0f);
        ImGui::TableSetupColumn("Order", ImGuiTableColumnFlags_WidthFixed, menu::px(100));
        ImGui::TableSetupColumn("This session", ImGuiTableColumnFlags_WidthStretch, 1.0f);
        ImGui::TableHeadersRow();
        for (std::size_t index = 0; index < menu.custom_script_rows.size(); ++index) {
            auto& script = menu.custom_script_rows[index];
            ImGui::PushID(script.id.c_str());
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            bool enabled = script.enabled;
            if (ImGui::Checkbox("##enabled", &enabled)) {
                try {
                    custom_scripts::save_enabled(game_directory(), script.id, enabled);
                    script.enabled = enabled;
                    menu.custom_script_error.clear();
                    menu::feedback(menu, "Script selection saved for the next launch.");
                } catch (const std::exception& error) { menu.custom_script_error = error.what(); }
            }
            ImGui::TableNextColumn();
            ImGui::TextWrapped("%s", script.id.c_str());
            ImGui::TableNextColumn();
            ImGui::BeginDisabled(index == 0);
            if (ImGui::SmallButton("Up")) move = {{index, index - 1}};
            ImGui::EndDisabled();
            ImGui::SameLine();
            ImGui::BeginDisabled(index + 1 == menu.custom_script_rows.size());
            if (ImGui::SmallButton("Down")) move = {{index, index + 1}};
            ImGui::EndDisabled();
            ImGui::TableNextColumn();
            ImGui::TextWrapped("%s", custom_scripts::status(script.id).c_str());
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (move) {
        try {
            auto ordered = menu.custom_script_rows;
            std::swap(ordered[move->first], ordered[move->second]);
            custom_scripts::save_order(game_directory(), ordered);
            menu.custom_script_rows = std::move(ordered);
            menu.custom_script_error.clear();
            menu::feedback(menu, "Script order saved for the next launch.");
        } catch (const std::exception& error) { menu.custom_script_error = error.what(); }
    }
    menu::end_card();
}
}
void draw_modding_menu(SkateMenu& menu, int tab, const Model& model, const CallbacksV3& callbacks) {
    // Loose files are switched in the launcher; custom scripts only need to know
    // whether they are on.
    if (!menu.loose_files_settings_loaded) {
        menu.loose_files_settings_loaded = true;
        try { menu.loose_files_saved = initfs::loose_files_preference(game_directory()); }
        catch (const std::exception&) {}
    }
    if (tab == 1) draw_custom_scripts(menu);
    else {
        menu::board_break_deck_card(menu, model, callbacks);
        draw_installed_mods(menu);
        draw_get_mods(menu);
    }
}
}
