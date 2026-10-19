#include "package_install.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Vfs/mod_list.h"
#include "Engine/Vfs/mod_catalog.h"
#include "Launcher/mod_manager.h"
#include <Windows.h>
#include <vector>

namespace dingosdk::package_install {
namespace fs = std::filesystem;
namespace package = thunderstore_package;

fs::path mods_root() { return mods::engine_data_root() / mods::mods_folder; }

Archive::Archive(std::string_view folder) {
    wchar_t temp[MAX_PATH + 1]{};
    const auto length = GetTempPathW(MAX_PATH, temp);
    path_ = fs::path(length ? std::wstring(temp, length) : L".") /
            (L"ReSkate-package-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::wstring(folder.begin(), folder.end()) + L".zip");
    remove();
}
Archive::~Archive() { remove(); }
void Archive::remove() const noexcept {
    std::error_code error;
    fs::remove(path_, error);
}

Downloaded download(const package::Name& name, const std::string& version, const std::string& fallback,
                    const Archive& archive, std::uint64_t most_bytes, std::uint32_t seconds, https::Watch& watch) {
    watch.received.store(0);
    watch.total.store(0);
    const auto get = [&](const std::string& selected) {
        // (Only the download's own time limit stops it, not a deadline of whoever waits on it.)
        watch.deadline_us.store(0);
        return https::get(package::download_url(name, selected), archive.path(), most_bytes, seconds, agent, &watch);
    };
    Downloaded out{get(version), version};
    // The version asked for can be gone from Thunderstore: the newest is the same mod.
    if (!out.result.ok && out.result.http_status == 404 && !fallback.empty() && fallback != version && !watch.cancel.load()) {
        archive.remove();
        logging::log(logging::Level::info, logging::Channel::assets,
            "Package download: {} {} is not on Thunderstore any more; getting {}.", name.folder(), version, fallback);
        out = {get(fallback), fallback};
    }
    if (!out.result.ok) archive.remove();
    return out;
}

void install(const Archive& archive, const package::Name& name, bool replace, const std::function<void(float)>& progress,
             const std::atomic<bool>& stop, const Accept& accept) {
    launcher_mods::InstallOptions options{name.folder(), name.owner, true, {}};
    if (accept) options.accept = accept;
    launcher_mods::install(mods_root(), archive.path(), replace, progress ? progress : [](float) {}, stop, options);
}

std::string check_installed(const std::string& folder, const Accept& accept) {
    const auto root = mods_root() / folder;
    std::vector<std::string> files;
    std::error_code error;
    // (Links are not followed: one is refused where it is.)
    for (fs::recursive_directory_iterator at(root, fs::directory_options::none, error), end; !error && at != end; at.increment(error)) {
        const auto attributes = GetFileAttributesW(at->path().c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) || at->is_symlink(error))
            return "Its package installed a link (" + mods::ascii_path(fs::relative(at->path(), root, error)) + ").";
        if (at->is_directory(error)) continue;
        if (!at->is_regular_file(error)) return "Its package installed something that is not a file.";
        auto relative = mods::ascii_path(fs::relative(at->path(), root, error));
        if (error || relative.empty()) return "Its package installed a file with a name no mod uses.";
        files.push_back(std::move(relative));
    }
    if (error) return "Its installed folder could not be read: " + error.message();
    return accept ? accept(files) : std::string();
}
}
