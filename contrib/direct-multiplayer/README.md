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
- **Signaling and lobbies:** a small stateless service hosted by Badger-Field. It turns a join
  code into the host, passes ICE messages between peers, and lists public lobbies. On the game
  side it is a second implementation of `LobbyApi` (`Extension/Multiplayer/Steam/steam_lobbies.h`).
- **Identity:** each player has an Ed25519 key in their ReSkate profile. The host verifies a signed
  challenge when a player joins, so no one can pose as another player. The key maps to a 64-bit
  player ID outside the SteamID range, so the existing roles, bans and party code keep working.
- **Steam mode is unchanged.** The direct transport is used when ReSkate runs in offline mode,
  with Steam closed or signed out.

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
2. Split `SteamTransport` into a transport interface with a Steam backend and a direct (GNS)
   backend. The direct one uses custom signaling through the lobby service.
3. Lobby/signaling service with a `LobbyApi` backend for it.
4. Identity: profile key, join challenge, player IDs.
5. Menu: Host / Join by code in offline mode. Re-enable multiplayer and voice when offline.
6. Linux tests: two `SteamTransport`-backed sessions over GNS through the mock NAT; then a
   Windows test with two PCs.
