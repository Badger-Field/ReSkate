#include "server_words.h"
#include "Engine/Core/Text/word_filter.h"
#include "Extension/Multiplayer/Net/protocol.h"
#include <fstream>
#include <sstream>
#include <string_view>
#include <vector>

namespace dingosdk::server {
namespace {
// As many words of as many bytes as one of the team's lists holds.
constexpr std::size_t most_words = 5000, longest_word = 64;

constexpr std::string_view filtered_help =
    "# Words this server filters in chat, beside the ReSkate team's list.\n"
    "# One word to a line. Messages with them are still passed on: players who have their chat filter on\n"
    "# see the word as stars. The first 256 words of up to 32 letters are sent to players' games.\n"
    "# Read when the server starts and by the console command: words reload\n";
constexpr std::string_view blocked_help =
    "# Words that are not allowed in this server's chat at all, beside the ReSkate team's list.\n"
    "# One word to a line. A message with one is not passed on and its player is warned\n"
    "# (see word_warnings in the README).\n"
    "# Read when the server starts and by the console command: words reload\n";

std::vector<std::string> read(const std::filesystem::path &file, std::string_view help, std::string &problem) {
    std::vector<std::string> words;
    std::error_code error;
    if (!std::filesystem::exists(file, error)) {
        std::filesystem::create_directories(file.parent_path(), error);
        std::ofstream out(file, std::ios::binary);
        if (!(out << help)) problem = "could not write " + file.filename().string();
        return words;
    }
    std::ifstream in(file, std::ios::binary);
    if (!in) {
        problem = "could not read " + file.filename().string();
        return words;
    }
    for (std::string line; std::getline(in, line) && words.size() < most_words;) {
        if (const auto comment = line.find('#'); comment != std::string::npos) line.erase(comment);
        const auto first = line.find_first_not_of(" \t\r\xEF\xBB\xBF"), last = line.find_last_not_of(" \t\r");
        if (first == std::string::npos) continue;
        line = line.substr(first, last - first + 1);
        if (line.size() <= longest_word) words.push_back(std::move(line));
    }
    return words;
}
} // namespace

std::filesystem::path filtered_words_file(const ServerConfig &c) { return c.file.parent_path() / "data" / "words_filtered.txt"; }
std::filesystem::path blocked_words_file(const ServerConfig &c) { return c.file.parent_path() / "data" / "words_blocked.txt"; }

OwnWords use_own_words(const ServerConfig &config) {
    OwnWords result;
    const auto filtered = read(filtered_words_file(config), filtered_help, result.problem);
    const auto blocked = read(blocked_words_file(config), blocked_help, result.problem);
    result.filtered = filtered.size();
    result.blocked = blocked.size();
    for (const auto &word : filtered)
        if (result.sent.size() < multiplayer::max_server_words && multiplayer::valid_server_word(word)) result.sent.push_back(word);
    text::set_own_words(filtered, blocked, config.global_words);
    return result;
}

std::string own_words_text(const ServerConfig &config, const OwnWords &words) {
    std::ostringstream out;
    out << "Chat words: " << (config.global_words ? "the ReSkate team's lists are used"
                                                : "the ReSkate team's lists are off (\"use_global_word_filter\": false)")
        << "; this server filters " << words.filtered << " word(s) of its own (data/words_filtered.txt, " << words.sent.size()
        << " sent to players) and blocks " << words.blocked << " (data/words_blocked.txt).";
    if (!words.problem.empty()) out << " Problem: " << words.problem << ".";
    return out.str();
}
} // namespace dingosdk::server
