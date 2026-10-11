#pragma once

#include <cstdint>

namespace dingosdk::profile_runtime {
using MusicSelectNext = std::int32_t (__fastcall *)(std::uintptr_t queue, std::uintptr_t playlist_asset);
using MusicChannelPlaylistBind = std::uintptr_t (__fastcall *)(std::uintptr_t channel_pointer,
                                                               std::uintptr_t playlist_id_pointer);
using MusicCandidateAdmission = bool (__fastcall *)(std::uintptr_t manager,
    std::uintptr_t candidate_guid, std::uintptr_t context, std::uint32_t channel_key);

// Validate the supported native selector/setter contracts and seed the lock-free
// preference read used by the audio selection hook.
bool initialize_music_playback(std::uintptr_t base, bool shuffle) noexcept;
void activate_music_playback() noexcept;
void deactivate_music_playback() noexcept;
bool music_playback_available() noexcept;
MusicSelectNext& music_select_next_original() noexcept;
std::int32_t __fastcall music_select_next_hook(std::uintptr_t queue, std::uintptr_t playlist_asset);
MusicChannelPlaylistBind& music_channel_playlist_bind_original() noexcept;
std::uintptr_t __fastcall music_channel_playlist_bind_hook(std::uintptr_t channel_pointer,
                                                            std::uintptr_t playlist_id_pointer);
MusicCandidateAdmission& music_candidate_admission_original() noexcept;
bool __fastcall music_candidate_admission_hook(std::uintptr_t manager,
    std::uintptr_t candidate_guid, std::uintptr_t context, std::uint32_t channel_key);

bool music_shuffle_enabled() noexcept;
void set_music_shuffle_enabled(bool enabled) noexcept;
} // namespace dingosdk::profile_runtime
