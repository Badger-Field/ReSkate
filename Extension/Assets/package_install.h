#pragma once
#include "Engine/Vfs/https_download.h"
#include "Engine/Vfs/thunderstore_package.h"
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <span>
#include <string>

// One Thunderstore package fetched and installed into Mods while the game runs: what a map a
// session needs (map_download.h) and a mod picked on the MODS page (mod_browser.h) have in common.
// Always from thunderstore.io, whatever a listing or a host says; installed by the launcher's own
// installer (Launcher/mod_manager.h), with its checks on every path in the archive. Worker threads
// only: these wait on the network and the disk.
namespace dingosdk::package_install {
inline constexpr wchar_t agent[] = L"ReSkate";
// <data root>/Mods, which the game reads.
std::filesystem::path mods_root();

// A file in the temporary folder for one package's archive, removed with this.
class Archive {
public:
    explicit Archive(std::string_view folder);
    ~Archive();
    Archive(const Archive&) = delete;
    Archive& operator=(const Archive&) = delete;
    const std::filesystem::path& path() const { return path_; }
    void remove() const noexcept;
private:
    std::filesystem::path path_;
};

struct Downloaded {
    https::Download result;
    std::string version;   // the one that came
};
// Downloads `version` of the package into `archive`; when Thunderstore no longer has it (404) and
// `fallback` is another version, that one instead. `watch` sees how far it is and can stop it.
Downloaded download(const thunderstore_package::Name& name, const std::string& version, const std::string& fallback,
                    const Archive& archive, std::uint64_t most_bytes, std::uint32_t seconds, https::Watch& watch);

using Accept = std::function<std::string(std::span<const std::string> files)>;
// Installs the archive as Mods/<Owner-Name>, replacing the installed one when asked (it goes to the
// Recycle Bin first). `accept` is asked about every file before anything is written. Throws a
// message for the player; a refused or failed install leaves the Mods folder as it was.
void install(const Archive& archive, const thunderstore_package::Name& name, bool replace,
             const std::function<void(float)>& progress, const std::atomic<bool>& stop, const Accept& accept = {});

// Why the installed folder Mods/<folder> is not what `accept` allows, or empty: every file in it,
// read again from the disk after the install. A link or junction anywhere in it is refused too.
std::string check_installed(const std::string& folder, const Accept& accept);
}
