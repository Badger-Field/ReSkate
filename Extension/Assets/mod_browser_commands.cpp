#include "mod_browser.h"
#include "live_mods.h"
#include "Extension/Console/commands.h"
#include "Extension/UI/Overlay/modding_menu.h"
#include "Extension/UI/Overlay/overlay.h"
#include <algorithm>

// The MODS page's GET MODS card from the console (mod_browser.h), for a test harness that can only
// send commands: the same listing, search, rows, checks and Apply as the card, and nothing of their
// own. They read nothing from the disk or the network themselves: the listing and the Mods folder
// are the card's, read on threads of their own, so a first command may only start the reading.
namespace dingosdk::console {
namespace {
namespace listing_ = thunderstore_listing;

const char* stage_name(mod_browser::Stage stage) {
    switch (stage) {
    case mod_browser::Stage::checking: return "checking";
    case mod_browser::Stage::downloading: return "downloading";
    case mod_browser::Stage::installing: return "installing";
    case mod_browser::Stage::enabling: return "enabling";
    case mod_browser::Stage::done: return "done";
    case mod_browser::Stage::failed: return "failed";
    default: return "idle";
    }
}

// The listing and the Mods folder as last read, or why they are not there yet.
struct Snapshot {
    std::shared_ptr<const mod_browser::Listing> listing;
    std::shared_ptr<const mods::ModList> folder;
    std::shared_ptr<const std::vector<mods::BuiltinPack>> officials;
    std::string waiting;
    std::span<const mods::BuiltinPack> official() const {
        return officials ? std::span<const mods::BuiltinPack>(*officials) : std::span<const mods::BuiltinPack>();
    }
};
Snapshot snapshot() {
    Snapshot now;
    now.listing = mod_browser::listing(); // (read again here when it is ten minutes old)
    now.folder = overlay::mods_folder();
    now.officials = now.folder ? overlay::official_mods(now.folder->root) : nullptr;
    if (!now.listing) now.waiting = "Reading ReSkate's mods on Thunderstore; run it again in a moment.";
    else if (!now.folder) now.waiting = "Reading the Mods folder; run it again in a moment.";
    return now;
}
} // namespace

void register_mod_browser_commands(Commands &registry) {
    auto text = argument("text", Type::text, true);
    text.rest = true;
    auto browse = action("mods browse", "Open MODS > GET MODS searching for this text (none: every mod), and list the first 10 "
                         "that match: Owner-Name, version, size, and installable, update, installed or why it is refused",
                         Group::console, {std::move(text)});
    browse.run = [](const Model &, const Values &values, const Output &out) {
        const auto query = values.empty() ? std::string() : std::get<std::string>(values[0]);
        mod_browser::set_query(query);
        overlay::menu_open_flag().store(true);
        mod_browser::show();
        const auto now = snapshot();
        if (!now.waiting.empty()) return out(now.waiting);
        if (!now.listing->error.empty()) out("Thunderstore could not be read (" + now.listing->error + "); this is the last list read.");
        const auto rows = mod_browser::rows(*now.listing, query, *now.folder, now.official());
        out(std::to_string(rows.size()) + (rows.size() == 1 ? " mod matches" : " mods match") +
            (query.empty() ? std::string(".") : " \"" + query + "\"."));
        for (std::size_t i = 0; i < rows.size() && i < 10; ++i)
            out(listing_::row_text(*rows[i].package, rows[i].action, mod_browser::refusal(rows[i])));
    };
    registry.add(std::move(browse));

    auto name = argument("Owner-Name");
    name.complete = [](const Model &, auto) {
        std::vector<std::string> names;
        if (const auto listing = mod_browser::listing())
            for (const auto &package : listing->packages) names.push_back(package.full_name);
        return names;
    };
    auto install = action("mods install", "Install a mod from GET MODS, or its update, as its Install button does: "
                          "mods status shows how far it is", Group::console, {std::move(name)});
    install.run = [](const Model &, const Values &values, const Output &out) {
        const auto wanted = std::get<std::string>(values[0]);
        const auto now = snapshot();
        if (!now.waiting.empty()) return out(now.waiting);
        const auto *package = listing_::find(now.listing->packages, wanted);
        if (!package) return out("refused: There is no mod named " + wanted + " on Thunderstore (mods browse lists them).");
        // Its row, as the card has it; one the card does not list is not installed from here.
        const auto rows = mod_browser::rows(*now.listing, {}, *now.folder, now.official());
        const auto row = std::find_if(rows.begin(), rows.end(), [&](const mod_browser::Row &other) { return other.package == package; });
        if (row == rows.end())
            return out("refused: " + package->full_name +
                       (listing_::nsfw(*package) ? " is marked NSFW."
                        : mod_browser::kept(package->folder(), now.official()) ? " is an official mod: it is installed on the launcher's OFFICIAL tab."
                                                                                : " is deprecated."));
        if (const auto refused = mod_browser::install(*row); !refused.empty()) return out("refused: " + refused);
        out("Installing " + package->full_name + " v" + package->version + "; mods status shows how far it is.");
    };
    registry.add(std::move(install));

    auto cancel = action("mods cancel", "Stop the install from GET MODS, while it is not in the Mods folder yet", Group::console);
    cancel.run = [](const Model &, const Values &, const Output &out) {
        const auto stage = mod_browser::view().stage;
        if (stage != mod_browser::Stage::checking && stage != mod_browser::Stage::downloading && stage != mod_browser::Stage::installing)
            return out(std::string("Nothing to cancel (") + stage_name(stage) + ").");
        mod_browser::cancel();
        out("Cancelling; mods status says when it has stopped.");
    };
    registry.add(std::move(cancel));

    auto then = argument("reload", Type::text, true);
    then.choices = {"reload"};
    auto apply = action("mods apply", "Apply Mods/ and mods.json now, as the MODS page's Apply button does: the level loads again "
                        "only when what changed shows in it, or always with 'reload'", Group::console, {std::move(then)});
    apply.run = [](const Model &, const Values &values, const Output &out) {
        if (!values.empty()) {
            if (mod_browser::busy()) return out("A mod is being installed: apply once it is in.");
            return out(live_mods::apply(true));
        }
        const auto folder = overlay::mods_folder();
        if (!folder) return out("Reading the Mods folder; run it again in a moment.");
        out(mod_browser::apply(*folder));
    };
    registry.add(std::move(apply));

    auto status = action("mods status", "What GET MODS is doing: the install's stage and bytes, and the last result and error",
                         Group::console);
    status.run = [](const Model &, const Values &, const Output &out) {
        const auto job = mod_browser::view();
        std::string line = std::string("Stage: ") + stage_name(job.stage);
        if (job.stage != mod_browser::Stage::idle) line += " (" + job.folder + (job.version.empty() ? "" : " v" + job.version) + ")";
        if (job.stage == mod_browser::Stage::downloading)
            line += ", " + listing_::size_text(job.received) + (job.total ? " of " + listing_::size_text(job.total) : std::string());
        if (job.stage == mod_browser::Stage::installing) line += ", " + std::to_string(static_cast<int>(job.installed * 100)) + "% written";
        out(line);
        if (live_mods::busy()) out("Mods are being applied.");
        const auto listing = mod_browser::listing();
        out(!listing ? std::string("Listing: reading Thunderstore.")
                     : "Listing: " + std::to_string(listing->packages.size()) + " mods" +
                           (listing->error.empty() ? std::string(".") : "; the last read failed: " + listing->error) +
                           (mod_browser::reading() ? " Reading it again." : ""));
        out("Last result: " + (job.last_result.empty() ? std::string("none") : job.last_result));
        out("Last error: " + (job.last_error.empty() ? std::string("none") : job.last_error));
    };
    registry.add(std::move(status));
}
} // namespace dingosdk::console
