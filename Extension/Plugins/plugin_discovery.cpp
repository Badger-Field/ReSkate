#include "plugin_discovery.h"
#include "plugin_manifest.h"
#include "Engine/Vfs/mod_list.h"
#include <algorithm>
#include <array>
#include <fstream>

namespace dingosdk::plugins {
namespace {
constexpr std::array<std::string_view, 22> runnable{".exe", ".dll", ".sys", ".scr", ".com", ".cpl", ".msi", ".msp",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".pif", ".reg"};
constexpr std::size_t most_files = 20000;

// path::string() throws on names outside the ANSI code page; a name here is only shown or compared.
std::string ascii_lowercase(const std::filesystem::path& path) {
    std::string text;
    for (const auto c : path.generic_wstring())
        text += c < 0x80 ? static_cast<char>(c >= L'A' && c <= L'Z' ? c + 32 : c) : '?';
    return text;
}
}

std::string check_package_files(const std::filesystem::path& directory) {
    std::error_code code;
    std::size_t files = 0;
    for (std::filesystem::recursive_directory_iterator it(directory, std::filesystem::directory_options::skip_permission_denied, code), end;
         !code && it != end; it.increment(code)) {
        std::error_code entry_code;
        if (!it->is_regular_file(entry_code)) continue;
        if (++files > most_files) return "it holds more than 20000 files";
        const auto shown = ascii_lowercase(std::filesystem::relative(it->path(), directory, entry_code));
        const auto extension = ascii_lowercase(it->path().extension());
        bool program = std::find(runnable.begin(), runnable.end(), extension) != runnable.end();
        if (!program) {
            std::ifstream in(it->path(), std::ios::binary);
            char magic[2]{};
            program = in.read(magic, 2) && magic[0] == 'M' && magic[1] == 'Z';
        }
        if (program) return "it holds " + shown + ", a program or a script Windows runs. A plugin never ships one";
    }
    return {};
}

std::vector<Candidate> discover(const std::filesystem::path& data_root, FileChecks& checks) {
    std::vector<Candidate> result;
    const auto list = mods::scan_mods(data_root);
    // A mods.json the game cannot read loads no mod at all, plugins included.
    if (!list.present || !list.issue.empty()) return result;
    FileChecks seen;
    for (const auto& entry : list.entries) {
        if (!entry.enabled) continue;
        const auto& directory = entry.mod.directory;
        std::error_code code;
        if (!std::filesystem::is_regular_file(directory / manifest_file, code)) continue;
        Candidate candidate{entry.mod.name, directory, mods::mod_fingerprint(directory), {}};
        const auto key = candidate.id + "|" + candidate.fingerprint;
        const auto known = checks.find(key);
        candidate.refusal = known != checks.end() ? known->second : check_package_files(directory);
        seen.emplace(key, candidate.refusal);
        result.push_back(std::move(candidate));
    }
    checks = std::move(seen);
    return result;
}
}
