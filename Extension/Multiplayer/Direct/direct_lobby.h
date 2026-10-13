#pragma once
#include "direct_identity.h"
#include "direct_signaling.h"

#include <memory>
#include <string>

// Signaling through the ReSkate lobby service (contrib/lobby-service): a WebSocket to it, opened
// with a signed challenge so the service knows which player this is. A signal is one text
// message, "steamid:<to> <hex>" out and "steamid:<from> <hex>" in.
//
// `url` is the service's base address: "https://skate.ops.rodeo" in play, or
// "http://127.0.0.1:8787" for a local `wrangler dev`. On Windows any http(s) address works
// (WinHTTP, with the system's TLS); elsewhere only plain http, for tests. It runs on its own
// threads and reconnects by itself, with a growing pause, until the transport is destroyed.
namespace dingosdk::multiplayer {
inline constexpr char default_lobby_url[] = "https://skate.ops.rodeo";

std::unique_ptr<DirectSignaling> make_lobby_signaling(std::string url, PlayerKey key);
} // namespace dingosdk::multiplayer
