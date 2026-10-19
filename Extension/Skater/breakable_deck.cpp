#include "breakable_deck.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Extension/Assets/live_mods.h"
#include <Windows.h>
#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <fstream>
#include <map>
#include <mutex>

namespace dingosdk::breakable_deck {
namespace {
// What a deck says of itself, in its folder's deck.ini: `axle` for its trucks (1 = a standard board's;
// more on a wider deck) and `label`, its name in the menus.
struct Setup {
    float axle = 1.0f;
    std::string label;
};
std::string trimmed(std::string_view text) {
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.front()))) text.remove_prefix(1);
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) text.remove_suffix(1);
    return std::string(text);
}
Setup read_setup(std::string_view deck) {
    Setup out;
    if (deck.empty()) return out;
    try {
        const auto folder = mods::board_break_deck_folder(deck);
        if (folder.empty()) return out;
        std::ifstream in(folder / L"deck.ini");
        std::string line;
        while (std::getline(in, line)) {
            if (const auto hash = line.find_first_of("#;"); hash != std::string::npos) line.erase(hash);
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            auto key = trimmed(std::string_view(line).substr(0, eq));
            for (auto& c : key) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            const auto value = trimmed(std::string_view(line).substr(eq + 1));
            float amount{};
            const auto parsed = std::from_chars(value.data(), value.data() + value.size(), amount);
            const bool whole = parsed.ec == std::errc{} && parsed.ptr == value.data() + value.size();
            if (key == "axle" && whole && std::isfinite(amount)) out.axle = std::clamp(amount, 0.8f, 1.6f);
            else if (key == "label") out.label = value;
        }
    } catch (...) {}
    return out;
}
// The same, kept a minute per deck: the menus ask every frame, and each look lists every mod's folder.
Setup setup_of(std::string_view deck) {
    struct Kept {
        Setup setup;
        std::uint64_t at{};
    };
    static std::mutex mutex;
    static auto* kept = new std::map<std::string, Kept, std::less<>>;
    const auto now = GetTickCount64();
    {
        std::lock_guard lock(mutex);
        if (const auto at = kept->find(deck); at != kept->end() && now - at->second.at < 60000) return at->second.setup;
    }
    auto setup = read_setup(deck);
    std::lock_guard lock(mutex);
    (*kept)[std::string(deck)] = {setup, now};
    return setup;
}
// The deck `wanted` means, of those installed: a name, a number in the list (from 1), or "next"
// for the one after the deck in the game. Empty when there is no such deck.
std::string meant(std::string wanted, const std::string& in_game, const std::vector<std::string>& installed) {
    for (auto& c : wanted) c = c == ' ' ? '_' : static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (installed.empty()) return {};
    if (wanted.empty() || wanted == "next") {
        const auto now = std::find(installed.begin(), installed.end(), in_game);
        return now == installed.end() || now + 1 == installed.end() ? installed.front() : *(now + 1);
    }
    if (std::all_of(wanted.begin(), wanted.end(), [](char c) { return c >= '0' && c <= '9'; }) && wanted.size() <= 3) {
        // The decks the packs were made with keep their numbers whether they are installed or not;
        // the others follow, in the order they are listed.
        const auto number = static_cast<std::size_t>(std::stoi(wanted));
        const auto& known = mods::board_break_decks;
        if (number >= 1 && number <= known.size()) return std::string(known[number - 1]);
        std::size_t next = known.size();
        for (const auto& deck : installed)
            if (std::find(known.begin(), known.end(), deck) == known.end() && ++next == number) return deck;
        return {};
    }
    return wanted;
}
std::mutex wanted_mutex;
std::string wanted_deck; // asked for, done at the next tick
}

std::string in_play() noexcept { return mods::board_break_in_play(); }
std::string label(std::string_view deck) {
    if (deck.empty()) return "None";
    if (auto title = setup_of(deck).label; !title.empty()) return title;
    std::string name(deck);
    bool start = true;
    for (auto& c : name) {
        if (c == '_') c = ' ';
        else if (start) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        start = c == ' ';
    }
    return name;
}
float axle(std::string_view deck) { return deck.empty() ? 1.0f : setup_of(deck).axle; }

std::string choose(std::string wanted) {
    const auto installed = mods::installed_board_break_decks();
    if (installed.empty()) return "None of the breakable deck packs is installed.";
    const auto now = in_play();
    const auto chosen = meant(std::move(wanted), now, installed);
    if (chosen.empty()) return "There is no such deck.";
    if (std::find(installed.begin(), installed.end(), chosen) == installed.end())
        return "The " + label(chosen) + " deck is not installed: its mod (Board_Break_" + chosen + ") is not in the Mods folder.";
    if (chosen == now) return "The " + label(chosen) + " deck is the one in the game.";
    {
        std::lock_guard lock(wanted_mutex);
        wanted_deck = chosen;
    }
    return "The " + label(chosen) + " deck: the mods are applied and the level loads again with it.";
}
std::string list() {
    const auto installed = mods::installed_board_break_decks();
    const auto& known = mods::board_break_decks;
    std::string out;
    std::size_t number{};
    const auto add = [&](std::string_view name, bool there) {
        out += (++number > 1 ? ", " : "") + std::to_string(number) + " " + label(name) + (there ? "" : " (not installed)");
    };
    for (const auto name : known) add(name, std::find(installed.begin(), installed.end(), name) != installed.end());
    for (const auto& name : installed)
        if (std::find(known.begin(), known.end(), name) == known.end()) add(name, true);
    return out;
}
void tick() noexcept {
    try {
        std::string wanted;
        {
            std::lock_guard lock(wanted_mutex);
            wanted.swap(wanted_deck);
        }
        if (!wanted.empty() && wanted != in_play() && mods::save_board_break_deck(wanted)) (void)live_mods::apply(true);
    } catch (...) {}
}
}
