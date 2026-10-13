#pragma once
#include "direct_identity.h"
#include "direct_signaling.h"

#include <memory>
#include <string>

// Signaling through the ReSkate lobby service (contrib/lobby-service): a WebSocket to it, opened
// with a signed challenge so the service knows which player this is. A signal is one text
// message, "steamid:<to> <hex>" out and "steamid:<from> <hex>" in.
//
// `url` is the service's base address: default_lobby_url in play, or
// "http://127.0.0.1:8787" for a local `wrangler dev`. On Windows any http(s) address works
// (WinHTTP, with the system's TLS); elsewhere only plain http, for tests. It runs on its own
// threads and reconnects by itself, with a growing pause, until the transport is destroyed.
namespace dingosdk::multiplayer {
// workers.dev until skate.ops.rodeo is live behind its firewall rules
// (contrib/lobby-service/README.md); then "https://skate.ops.rodeo".
inline constexpr char default_lobby_url[] = "https://reskate-lobby.reskate-lobby-service.workers.dev";
// Every request to the service carries this User-Agent. The Worker answers anything without it
// with a bare 404 before doing any work, and on the custom domain a Cloudflare firewall rule
// blocks it before the Worker runs at all (contrib/lobby-service/README.md).
inline constexpr char lobby_user_agent[] = "ReSkate-Lobby/1";

std::unique_ptr<DirectSignaling> make_lobby_signaling(std::string url, PlayerKey key);
} // namespace dingosdk::multiplayer
