# Direct multiplayer (no Steam)

Goal: one player hosts and friends join by code. Players connect to each other directly, with no
Steam and no EA app. This is work in progress on the `direct-multiplayer` branch.

## Design

- **Transport:** Valve's open-source [GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets)
  (GNS, BSD-3) in place of the copy inside Steam's `steam_api64.dll`. It is the same library: the
  same encryption, reliable and unreliable messages, and lanes.
- **NAT traversal:** GNS's built-in ICE client, with no external dependencies. It uses public
  STUN to find each player's address. TURN relays a connection only when both routers are
  symmetric NATs.
- **Signaling and lobbies:** the lobby service in `contrib/lobby-service`, hosted by Badger-Field
  on Cloudflare: a Worker in front of one Durable Object with its own SQLite store. It passes ICE
  messages between connected players and lists public lobbies. A join code already contains the
  host's player ID, so the service never has to look one up. The game side, a second
  implementation of `LobbyApi` (`Extension/Multiplayer/Steam/steam_lobbies.h`), is not written yet.
- **Identity:** each player has an Ed25519 key (`Extension/Multiplayer/Direct/direct_identity.h`).
  The lobby service checks a signed challenge when a player connects. The player ID is a valid
  SteamID in the development universe (4), with the account number taken from the key's SHA-256,
  because GNS accepts only SteamID identities. The existing roles, bans and party code keep
  working. Still to come: keeping the key in the ReSkate profile, and the host checking it too.
- **Steam mode is unchanged.** The plan is for the direct transport to be used when ReSkate runs
  in offline mode, with Steam closed or signed out. That game integration is not written yet:
  so far `open_direct` is used only by the tests.

## Spike results

All tests ran on Linux, building GNS at commit `d534c19` (2 Oct 2026).

**Build.** GNS builds in under a minute with the system protobuf (3.21) and OpenSSL (3.0); ICE
support is on by default (`ENABLE_ICE`).

**NAT traversal.** GNS's own `tests/test_p2p.py` simulates home routers: a mock NAT with its own
STUN/TURN and signaling servers. The sandbox has no IPv6, so the IPv6 cases were removed and the
rest ran on IPv4.

| Scenario | Result |
|---|---|
| symmetric NAT on both sides (TURN relay) | pass |
| full-cone, restricted-cone, port-restricted-cone NAT | pass |
| one side public, the other full-cone; full-cone against symmetric | pass |
| same LAN; same LAN through a shared gateway (hairpin) | pass |
| no public IP, symmetric NAT (falls back to relay) | pass |
| multiple adapters, one disabled; fast public plus slow NATed adapter | pass |
| full-cone NAT with 10% packet loss | pass |
| real network, no mock (4 cases, incl. 30% signaling loss and 50% duplicates) | connected; the test expected a `local` route label, and the sandbox's address (192.0.2.2, TEST-NET) is not a private range, so it reported `udp` |

**API match.** ReSkate's transport (`steam_transport.cpp`) calls 37 Steam exports. Every
networking call it makes exists in GNS under the same flat name and interface version
(`SteamNetworkingSockets_LibV12`, matching the game's v012), including `ConnectByIPAddress`,
`CreateListenSocketIP`, `ConfigureConnectionLanes`, `SendMessages` and `GetIdentity`. GNS also
has `ConnectP2PCustomSignaling` / `ReceivedP2PCustomSignal` for our own signaling. What GNS
lacks is Steam client glue: interface accessors, `SteamFriends`, `SteamUtils`, and Steam's relay
network (SDR). The direct transport replaces those with the GNS accessors, profile names, and
ICE.

## Next

1. Build GNS for Windows x64: vcpkg `gamenetworkingsockets[ice]`, or CMake with protobuf/OpenSSL
   from vcpkg. Ship `GameNetworkingSockets.dll` beside `ReSkate.dll`.
2. Done: `SteamTransport::open_direct` runs over GNS with custom signaling, and the lobby
   service (`contrib/lobby-service`) with its client (`direct_lobby.h`) carries the signals.
   Still to do: a `LobbyApi` backend on the service, so public lobbies show in the game.
3. Done: player keys and IDs (`direct_identity.h`), checked by the lobby service. Still to do:
   keeping the key in the ReSkate profile, and the host checking a joining player's key.
4. Menu: Host / Join by code in offline mode. Re-enable multiplayer and voice when offline.
5. Linux tests: two `SteamTransport`-backed sessions over GNS through the mock NAT; then a
   Windows test with two PCs.
