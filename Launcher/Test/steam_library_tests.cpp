// Finding skate. in Steam's own files (steam_library.h): library folders, the install folder,
// and a manifest that tries to point outside steamapps/common.
#include "Launcher/steam_library.h"

#include <cstdio>

using namespace dingosdk::launcher_app::steam_library;

int main() {
    int failures = 0;
    const auto check = [&](bool ok, const char *what) {
        if (!ok) {
            ++failures;
            std::fprintf(stderr, "FAIL: %s\n", what);
        }
    };
    const char *vdf = R"("libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"apps"
		{
			"228980"		"420305420"
		}
	}
	"1"
	{
		"path"		"D:\\SteamLibrary"
		"apps"
		{
			"3354750"		"15032385536"
		}
	}
})";
    const auto folders = library_folders(vdf);
    check(folders.size() == 2 && folders[0] == R"(C:\Program Files (x86)\Steam)" && folders[1] == R"(D:\SteamLibrary)",
          "library folders, unescaped");
    const char *acf = R"("AppState"
{
	"appid"		"3354750"
	"name"		"skate."
	"installdir"		"skate"
})";
    check(manifest_value(acf, "installdir") == "skate", "the install folder");
    check(!manifest_value(acf, "nothere"), "a missing key");
    check(plain_folder_name("skate") && plain_folder_name("skate."), "plain names");
    check(!plain_folder_name("..") && !plain_folder_name("..\\..\\Windows") && !plain_folder_name("C:\\x") &&
              !plain_folder_name("a/b") && !plain_folder_name(""),
          "a manifest cannot point outside steamapps/common");
    check(library_folders("").empty() && library_folders("\"path\"").empty(), "nothing in, nothing out");
    std::printf("%s\n", failures ? "FAILED" : "Steam library: folders, install folder, path checks passed.");
    return failures ? 1 : 0;
}
