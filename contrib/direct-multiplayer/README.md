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
- **Steam mode is unchanged.** The direct transport is used when ReSkate runs in offline mode
  and `GameNetworkingSockets.dll` is beside `ReSkate.dll` (see "Playing without Steam").

## Playing without Steam

Work in progress: hosting with a join code, and on the home network, work; Steam friends and
voice chat still need Steam.

1. Put `GameNetworkingSockets.dll` beside `ReSkate.dll` in the skate. folder. It is Valve's
   [GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets) at `d534c19`,
   built as one Windows x64 DLL with ICE on and everything linked in (vcpkg triplet
   `x64-windows-static`, static CRT): it needs nothing but Windows. ReSkate loads only the
   build whose SHA-256 is in `direct_play.cpp`.
2. Start ReSkate with Steam closed (or tick **Always without Steam** in the launcher's Settings).
   The Multiplayer pages appear as they do with Steam.
3. Multiplayer, then **Your name**: the name other skaters see. Until it is set it is "Skater"
   and four digits.
4. The host: **Host**, then **Home network** (anyone on the same network sees it in Servers) or
   **Join code only** (share the code from Session).
5. Everyone else: **Servers**, then Join, for a game on your network (marked
   **ON YOUR NETWORK**). For anyone else's, paste their code into the join box.

The console does the same: `mp name <name>`, `mp host home 8` or `mp host code 8`, `mp join <code>`.

## Security and privacy

Players connect straight to each other, so a player who joins a game can see the host's IP
address (and the host theirs). Everything else is built so that nobody else can:

- **No public games without Steam.** A public listing would give the address to anyone. They
  come back once there is a relay (TURN) that hides it.
- **Only invited players reach a game at all.** Every signal carries a token derived from the
  session's secret and its password (`direct_join_token`). Without it, the host's networking
  library never sees the signal and finds no route, so no address is exchanged. Kicked and
  banned players are refused at the same point. The lobby service carries the tokens but
  cannot work out the code or the password from them. `direct_transport_tests` runs an intruder
  who knows the host's ID but not the code, and checks that it never connects.
- **Guests reach each other through the host.** Two guests never link directly, so a guest
  never learns another guest's address.
- **Home network games are found on the network itself** (`lan_discovery.h`). They are not
  matched by shared IP address, which strangers behind carrier-grade NAT, a campus or a VPN
  share. Questions and answers come from private addresses only. Anyone on the same Wi-Fi can
  see such a game, so it is for home networks, not school or public Wi-Fi.
- **The lobby service never says whether a player is online.** It has limits:
  - signals a second, per player and per pair of players;
  - signal size;
  - how often a player lists;
  - new keys per network, per IPv6 /48 and overall.
  Its challenge MAC is checked in constant time.
- **Names:** a player's name is shown with bad words masked. "ReSkate", "Server" and the like
  are marked as a player's.
- **The player's key** is kept encrypted for their Windows user (DPAPI). Any lobby address set
  with `RESKATE_LOBBY_URL` must be https, except one on the same PC.

Not done yet: players do not yet prove their key to each other. A player's identity rests on
the lobby service, which checks each key when the player connects. So whoever runs the lobby
service can impersonate a player, and a player can come back under a new key after a ban (a
password stops that). Both are next.

Where things are kept:

- The player's key and name: `%LOCALAPPDATA%\ReSkate\profiles\offline\direct-player.txt`.
  The key is who the player is to the lobby service: keep it private.
- The lobby service: `default_lobby_url` in `Extension/Multiplayer/Direct/direct_lobby.h`; the
  environment variable `RESKATE_LOBBY_URL` overrides it (a local `wrangler dev`, for one).
- STUN: Cloudflare's and Google's public servers. There is no TURN relay yet, so two players
  who are both behind strict (symmetric) NAT cannot connect.

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
5. Done: offline mode opens the direct transport (`direct_play.h`), host and join by code, names
   from each player's hello and the host's roster, and the Multiplayer pages. Still to do: a
   menu field for the name, the lobby browser over the lobby service, voice without Steam,
   TURN, and shipping the DLL in releases.
6. Linux tests: two `SteamTransport`-backed sessions over GNS through the mock NAT; then a
   Windows test with two PCs.
