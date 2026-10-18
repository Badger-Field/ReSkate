// The probe asks the player outside the game only: Plex, the Spotify Connect device and YouTube
// Music live in ReSkate.dll with all they need, so here they are always off.
#include "Extension/Music/plex_web.h"
#include "Extension/Music/spotify_device.h"
#include "Extension/Music/youtube_music.h"

namespace dingosdk {
plex_web::NowPlaying plex_web::now_playing() { return {}; }
bool plex_web::control(Action) { return false; }
bool spotify_device::connected() noexcept { return false; }
external_player::Track spotify_device::now() { return {}; }
std::uint64_t spotify_device::revision() noexcept { return 0; }
void spotify_device::send(external_player::Command) noexcept {}
bool youtube_music::on() noexcept { return false; }
external_player::Track youtube_music::now() { return {}; }
std::uint64_t youtube_music::revision() noexcept { return 0; }
void youtube_music::send(external_player::Command) noexcept {}
}
