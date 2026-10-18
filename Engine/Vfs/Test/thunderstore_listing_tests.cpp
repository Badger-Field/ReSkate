// ReSkate's Thunderstore community as the MODS page lists it (Engine/Vfs/thunderstore_listing.h):
// reading the listing, searching it, versions, dependencies and what a package may hold.
#include "Engine/Vfs/thunderstore_listing.h"

#include <miniz.h>

#include <iostream>
#include <string>
#include <vector>

using namespace dingosdk;
namespace ts = thunderstore_listing;

namespace {
int failures = 0;
void check(bool condition, const char* what) {
    if (condition) return;
    std::cerr << "FAIL: " << what << '\n';
    ++failures;
}
template<class F> std::string error_of(F&& run) {
    try { run(); } catch (const std::exception& failure) { return failure.what(); }
    return {};
}

// A gzip member holding `text` in one stored (uncompressed) deflate block.
std::string gzip(std::string_view text, bool with_name = false) {
    std::string out{'\x1f', '\x8b', '\x08', static_cast<char>(with_name ? 0x08 : 0), 0, 0, 0, 0, 0, '\xff'};
    if (with_name) out += std::string("listing.json") + '\0';
    const auto length = static_cast<unsigned>(text.size());
    out += '\x01';
    out += static_cast<char>(length & 0xff);
    out += static_cast<char>(length >> 8 & 0xff);
    out += static_cast<char>(~length & 0xff);
    out += static_cast<char>(~length >> 8 & 0xff);
    out += text;
    const auto crc = mz_crc32(MZ_CRC32_INIT, reinterpret_cast<const unsigned char*>(text.data()), text.size());
    for (int i = 0; i < 4; ++i) out += static_cast<char>(crc >> (8 * i) & 0xff);
    for (int i = 0; i < 4; ++i) out += static_cast<char>(length >> (8 * i) & 0xff);
    return out;
}

// As /c/reskate/api/v1/package/ answers (the launcher's tests read the same shape).
constexpr char listing[] = R"([
  {"name": "Desert_Springs", "full_name": "Zee-Desert_Springs", "owner": "Zee",
   "date_updated": "2026-10-02T09:00:00Z", "rating_score": 4, "is_pinned": false, "is_deprecated": false,
   "has_nsfw_content": false, "categories": ["Mods", "Maps"],
   "versions": [
     {"version_number": "1.9.0", "description": "Old", "downloads": 10, "is_active": true, "file_size": 700,
      "icon": "https://gcdn.thunderstore.io/live/repository/icons/Zee-Desert_Springs-1.9.0.png", "dependencies": []},
     {"version_number": "1.10.0", "description": "A desert\npark.", "downloads": 5, "is_active": true, "file_size": 5368709120,
      "icon": "https://gcdn.thunderstore.io/live/repository/icons/Zee-Desert_Springs-1.10.0.png",
      "dependencies": ["Zee-Desert_Textures-1.2.0"]},
     {"version_number": "2.0.0", "description": "Pulled", "is_active": false}
   ]},
  {"name": "Bad Name", "full_name": "Zee-Bad Name", "owner": "Zee", "versions": [{"version_number": "1.0.0"}]},
  {"name": "No_Versions", "full_name": "Zee-No_Versions", "owner": "Zee", "versions": []},
  {"name": "Odd_Version", "full_name": "Zee-Odd_Version", "owner": "Zee", "versions": [{"version_number": "latest"}]},
  {"name": "Mismatch", "full_name": "Other-Thing", "owner": "Zee", "versions": [{"version_number": "1.0.0"}]},
  "not a package",
  {"name": "Low_Cam", "full_name": "SunJay-Low_Cam", "owner": "SunJay", "is_deprecated": true,
   "date_updated": "2026-09-01T00:00:00Z",
   "versions": [{"version_number": "0.1.0", "description": "Low camera", "icon": "http://not-https/icon.png"}]},
  {"name": "Spicy", "full_name": "Ann-Spicy", "owner": "Ann", "has_nsfw_content": true,
   "versions": [{"version_number": "1.0.0"}]},
  {"name": "Spicier", "full_name": "Ann-Spicier", "owner": "Ann", "categories": ["nsfw"],
   "versions": [{"version_number": "1.0.0"}]},
  {"name": "Gold_Deck", "full_name": "Kay-Gold_Deck", "owner": "Kay", "categories": ["Cosmetics"],
   "date_updated": "2026-10-03T00:00:00Z",
   "versions": [{"version_number": "3.0.0", "description": "A gold deck", "icon": "https://evil.example/thunderstore.io/x.png"}]},
  {"name": "Old_Pin", "full_name": "Kay-Old_Pin", "owner": "Kay", "is_pinned": true, "date_updated": "2020-01-01T00:00:00Z",
   "versions": [{"version_number": "1.0.0", "description": "Pinned"}]}
])";
} // namespace

int main() {
    // ------------------------------------------------ where it is read from
    check(ts::listing_index_url("reskate") == "https://thunderstore.io/c/reskate/api/v1/package-listing-index/", "the index, as the launcher reads it");
    check(ts::listing_url("reskate") == "https://thunderstore.io/c/reskate/api/v1/package/", "and the plain listing");
    check(ts::community("") == "reskate" && ts::community("test-community2") == "test-community2", "the community, or another for testing");
    check(ts::community("Bad/../x") == "reskate" && ts::community("UPPER") == "reskate", "anything else is not a community");

    // ------------------------------------------------ the listing
    const auto packages = ts::parse_listing(listing);
    check(packages.size() == 6, "malformed rows, mismatched names and rows with no usable version are skipped");
    const auto find = [&](std::string_view full) -> const ts::Package* {
        for (const auto& package : packages)
            if (package.full_name == full) return &package;
        return nullptr;
    };
    const auto* desert = find("Zee-Desert_Springs");
    check(desert && desert->version == "1.10.0", "the newest active version is 1.10.0, not 1.9.0 or the inactive 2.0.0");
    check(desert && desert->description == "A desert park." && desert->file_size == 5368709120ull, "its description on one line, and its size");
    check(desert && desert->downloads == 15 && desert->dependencies.size() == 1, "downloads of every active version, and its dependencies");
    check(desert && desert->title() == "Desert Springs" && desert->folder() == "Zee-Desert_Springs", "its title and its folder");
    check(desert && desert->icon.starts_with("https://gcdn.thunderstore.io/"), "an icon on Thunderstore's own host is kept");
    check(find("SunJay-Low_Cam") && find("SunJay-Low_Cam")->icon.empty(), "an icon that is not https is dropped");
    check(find("Kay-Gold_Deck") && find("Kay-Gold_Deck")->icon.empty(), "and one anywhere else");
    check(!error_of([] { ts::parse_listing("{}"); }).empty(), "a listing that is not an array is an error");
    check(!error_of([] { ts::parse_listing("[1,"); }).empty(), "as is one that is not JSON");
    {
        ts::Package longest;
        longest.owner = std::string(40, 'a');
        longest.name = std::string(40, 'b');
        longest.full_name = longest.owner + "-" + longest.name;
        check(longest.folder().size() == 64, "a folder name is cut to 64 characters, as the launcher cuts it");
    }

    // ------------------------------------------------ the index and gzip
    const auto urls = ts::parse_index(R"(["https://gcdn.thunderstore.io/live/community/reskate/chunk-1.json.gz"])");
    check(urls.size() == 1, "the index lists its chunks");
    check(!error_of([] { ts::parse_index(R"(["http://gcdn.thunderstore.io/a.gz"])"); }).empty(), "a chunk that is not https is refused");
    check(!error_of([] { ts::parse_index(R"(["https://evil.example/a.gz"])"); }).empty(), "and one elsewhere");
    check(!error_of([] { ts::parse_index(R"(["https://thunderstore.io.evil.example/a.gz"])"); }).empty(), "and one that only starts like it");
    check(!error_of([] { ts::parse_index(R"(["https://x@thunderstore.io/a.gz"])"); }).empty(), "and one with a user in it");
    check(ts::gunzip(gzip("[1,2]"), 100) == "[1,2]" && ts::gunzip(gzip("[3]", true), 100) == "[3]", "a gzip stream unpacks, named or not");
    check(ts::gunzip("[4]", 100) == "[4]", "text that is not gzip comes back as it is");
    check(!error_of([] { ts::gunzip(gzip("0123456789"), 5); }).empty(), "more than the limit is refused");
    {
        auto damaged = gzip("[1,2,3]");
        damaged[damaged.size() - 9] ^= 1;
        check(!error_of([&] { ts::gunzip(damaged, 100); }).empty(), "a damaged stream is refused");
        check(!error_of([&] { ts::gunzip(damaged.substr(0, 12), 100); }).empty(), "as is a cut one");
    }

    // ------------------------------------------------ versions
    check(ts::compare_versions("1.10.0", "1.9.0") > 0 && ts::compare_versions("1.9.0", "1.10.0") < 0, "components compare as numbers");
    check(ts::compare_versions("1.0.0", "1.0.0") == 0 && ts::compare_versions("01.0.0", "1.0.0") == 0, "leading zeros do not count");
    check(ts::compare_versions("2.0.0", "10.0.0") < 0 && ts::compare_versions("1.0.1", "1.0") > 0, "a longer number, and more parts");

    // ------------------------------------------------ installed, update, search
    ts::InstalledMods installed{{ts::folder_key("ZEE-Desert_Springs"), {"1.9.0", true}}, {ts::folder_key("SunJay-Low_Cam"), {"0.1.0", false}}};
    check(desert && ts::action(*desert, installed) == ts::Action::update, "an older version installed (any case) is an update");
    installed[ts::folder_key("Zee-Desert_Springs")].version = "1.10.0";
    check(desert && ts::action(*desert, installed) == ts::Action::installed, "the newest installed is installed");
    installed[ts::folder_key("Zee-Desert_Springs")].version = "";
    check(desert && ts::action(*desert, installed) == ts::Action::installed, "one with no version is left alone");
    installed[ts::folder_key("Zee-Desert_Springs")].version = "1.9.0";
    check(ts::action(*find("Kay-Gold_Deck"), installed) == ts::Action::install, "one not here is installed");

    const auto names = [](const std::vector<const ts::Package*>& rows) {
        std::string out;
        for (const auto* row : rows) out += (out.empty() ? "" : ",") + row->full_name;
        return out;
    };
    check(names(ts::search(packages, "", installed)) == "Kay-Old_Pin,Kay-Gold_Deck,Zee-Desert_Springs,SunJay-Low_Cam",
          "pinned first, then the most recently updated; never NSFW; deprecated only when installed");
    check(names(ts::search(packages, "", {})) == "Kay-Old_Pin,Kay-Gold_Deck,Zee-Desert_Springs", "a deprecated one not installed is not listed");
    check(names(ts::search(packages, "DESERT", installed)) == "Zee-Desert_Springs", "search ignores case");
    check(names(ts::search(packages, "cosmetics", installed)) == "Kay-Gold_Deck", "and finds a category");
    check(names(ts::search(packages, "kay gold", installed)) == "Kay-Gold_Deck", "every word has to be found");
    check(names(ts::search(packages, "desert springs", installed)) == "Zee-Desert_Springs", "in the title as the site shows it");
    check(ts::search(packages, "spicy", installed).empty() && ts::search(packages, "ann", installed).empty(), "NSFW is never found");

    // ------------------------------------------------ dependencies
    check(desert && ts::missing_dependencies(*desert, installed) == std::vector<std::string>{"Zee-Desert_Textures-1.2.0"}, "a dependency not here is missing");
    installed[ts::folder_key("Zee-Desert_Textures")] = {"1.1.9", true};
    check(desert && ts::missing_dependencies(*desert, installed).size() == 1, "as is one at an older version");
    installed[ts::folder_key("Zee-Desert_Textures")] = {"1.2.0", false};
    check(desert && ts::missing_dependencies(*desert, installed).empty(), "one at that version or newer is not");

    // ------------------------------------------------ what a package may hold
    const auto may = [](std::vector<std::string> files) { return ts::refuse_contents(files).empty(); };
    check(may({"manifest.json", "icon.png", "README.md", "CHANGELOG.md", "layout.toc", "Win32/levels/plaza.toc",
               "Win32/levels/plaza.sb", "Win32/levels/cas_01.cas", "Win32/globals.toc", "reskate-levels.json",
               "reskate-build.json", ".reskate-studio-patch", "LICENSE", "screenshots/one.jpg"}),
          "a ReSkate Studio map, with its package files");
    check(may({"manifest.json", "parks/san_van.park.json"}), "a park mod");
    check(may({"layout.toc", "Win32/items.toc", "Win32/cas_02.cas"}), "game data alone");
    check(!may({}), "nothing at all is refused");
    check(!may({"manifest.json", "icon.png", "README.md"}), "as is a package with nothing to load");
    check(!may({"layout.toc", "ReSkate.dll"}), "a program is refused");
    check(!may({"layout.toc", "Win32/hook.dll"}), "wherever it is");
    check(!may({"layout.toc", "scripts/init.lua"}), "a script is refused");
    check(!may({"layout.toc", "run.bat"}) && !may({"layout.toc", "x.exe"}) && !may({"layout.toc", "x.ps1"}), "and other programs");
    check(!may({"layout.toc", "reskate-music.json"}), "music playlists, which need a restart, are left to the launcher");
    check(!may({"layout.toc", "decks/gold/layout.toc"}) && !may({"layout.toc", "deck.ini"}), "as are Board Break's decks");
    check(!may({"layout.toc", "Win32/levels/x.json"}), "anything in Win32 but game data is refused");
    check(!may({"layout.toc", "parks/deep/x.park.json"}) && !may({"layout.toc", "parks/x.json"}), "a park only where parks go");
    check(!may({"layout.toc", "../outside.toc"}) && !may({"layout.toc", "Win32/../../x.toc"}), "no way out of the folder");
    check(!may({"layout.toc", "/abs.png"}) && !may({"layout.toc", "C:/x.png"}) && !may({"layout.toc", "a.png:stream"}),
          "nor an absolute path, a drive or a stream");
    check(!may({"layout.toc", "Win32\\levels\\x.toc"}), "nor a backslash");
    check(!may({"layout.toc", "Win32//x.toc"}) && !may({"layout.toc", "./x.png"}) && !may({"layout.toc", "x.png."}),
          "nor an empty part, a dot or a trailing dot");
    check(!may({"layout.toc", "caf\xc3\xa9.png"}) && !may({"layout.toc", std::string("a\x01.png")}), "nor a name that is not plain ASCII");
    check(!may({"layout.toc", "a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q.png"}), "nor one buried deeper than any mod");
    check(ts::refuse_contents(std::vector<std::string>{"layout.toc", "evil.dll"}).find("evil.dll") != std::string::npos,
          "the reason names the file");
    check(!may({".png"}) && !may({"layout.toc", "Win32/.toc"}), "an ending is not a name");

    // ------------------------------------------------ one line
    check(ts::one_line("  a\n\tb  c ", 40) == "a b c", "controls and runs of spaces become one space");
    check(ts::one_line("abcdefgh", 4) == "abcd...", "cut with an ellipsis");
    check(ts::one_line("ab\xc3\xa9", 3) == "ab...", "never inside a character");

    if (failures) {
        std::cerr << failures << " failure(s)\n";
        return 1;
    }
    std::cout << "thunderstore listing tests passed\n";
    return 0;
}
