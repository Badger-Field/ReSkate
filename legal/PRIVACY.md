# Privacy Policy

Version 1, 2026-10-18

This policy covers the Badger Field edition of ReSkate ("ReSkate"), published by Badger Field ("we", "us"): the launcher, the add-on that runs inside the game, the dedicated server, the map builder and real-place tools, and the lobby service we run for direct multiplayer.

**In short:** ReSkate has no accounts, no telemetry, no analytics, no ads and no crash uploads. We learn about bugs when you choose to report them. The only thing we run that sees you is the lobby service, and only while you play online without Steam.

## What we don't collect

- **No telemetry or analytics.** ReSkate doesn't send us how you play, what you click, or performance data.
- **No crash uploads.** ReSkate never sends crash reports. When something goes wrong, the session's log (`ReSkate.log` in the `logs` folder beside the game, opened with "Open logs" in the launcher's settings) and any crash dump the game writes stay on your PC. You can attach them to a bug report if you want to.
- **No accounts.** We don't ask for your name, email or Steam login.

## Bug reports you send

We rely on bug reports you choose to send, normally as an issue on GitHub (https://github.com/Badger-Field/ReSkate/issues).

- Issues on GitHub are **public**, and GitHub's own privacy policy applies to them.
- Read logs and crash reports before you attach them. They can contain file paths with your Windows user name, the Steam IDs and names of players from your session, and the mods you use. Remove anything you don't want public.
- What you send us is covered by "What you send us" in the Terms of Use.

## The lobby service (direct multiplayer)

When you play online without Steam, your game connects to our lobby service, which runs on Cloudflare Workers. It helps two games find each other and lists public sessions.

- **Your player ID:** your game makes a key on your PC. The service keeps your public key and the player ID made from it for 30 days after you were last seen, so nobody else can take your ID. It is not linked to your Steam account or your name.
- **Public lobbies:** if you host a public session, its listing (the details your game publishes, such as the host's player name, the map, the player count and how to join) is shown to anyone who lists lobbies. It is removed when you stop hosting, or within 2 minutes if your game disappears.
- **Connection messages:** the messages two games exchange to find a route to each other pass through the service and are not stored.
- **Your network address:** to limit abuse, the service counts new player keys per network address (for IPv6, per /64) for the current hour only. Older counts are deleted. Cloudflare sees your IP address when you connect, as any website host does, and handles it under its own privacy policy.
- **Other players:** players in your session see your player name, skater, where you are and what you do, chat, placed objects and parks. In a direct session they may see your IP address, as with any peer-to-peer game.

If you play offline, the lobby service is not used.

## Services run by others

ReSkate talks to these services for features you use. They are not run by us, and their own privacy policies apply.

| Service | When | What it gets |
| --- | --- | --- |
| Steam (Valve) | Steam multiplayer, lobbies, friends | Your session and lobby activity |
| The original ReSkate project's servers (api.reskate.dev) | At start and every 10 minutes while you play, and in the launcher | Plain downloads of public lists (player tags, banned Steam IDs, servers, the chat word filter, official mods). Nothing about you is sent, but the server sees your IP address |
| Discord | "Discord status" on (the default; turn off in the launcher or in game) | Through the Discord app on your PC: the map, whether you are in a session, and a join code for open sessions |
| Thunderstore | Browsing, installing and updating mods, and map downloads in multiplayer | Which mods you download. A profile share code uploads your mod list, only when you press COPY CODE |
| GitHub | Update checks, the news feed and game data cache downloads | Plain downloads. GitHub sees your IP address |
| Spotify, Plex, YouTube Music, online radio | Only if you set them up | Your sign-in and playback requests, sent directly from your PC |
| EA's image server | Fast-travel artwork | Image downloads only |

While you play through ReSkate, it blocks skate. from reaching EA's online services (accounts, telemetry, error reporting, ads), so the game doesn't send EA its usual data.

## Real places and the map builder

The map builder and real-place tools work from public datasets: USGS elevation and LiDAR, OpenStreetMap, and openly licensed street-level imagery. They are used for public places only.

- The tools work on your PC. They don't send us anything.
- We use them to make levels and datasets of public places. These are built from the public sources, not from anything about you, and are not meant to identify anyone. Street-level imagery comes already blurred by its provider, and we don't model private homes.
- We may publish, license or sell the levels and datasets we make (see the Terms of Use). They don't contain personal information collected by us.

## What stays on your PC

Your profile, progress, outfits, settings, player key, music sign-ins, mods and crash reports are stored on your PC and are not sent to us. They are kept in `%LOCALAPPDATA%\ReSkate` and beside the game. Delete those folders to remove them.

## Where we are

We are based in British Columbia, Canada. The lobby service runs on Cloudflare's network, which may handle connections in other countries.

## Children

ReSkate is for people old enough to play skate. under EA's terms and its age rating where they live. We don't knowingly collect anything about children below that age. If you think a child has sent us something, contact us and we will delete it.

## Your choices

- Play offline to stay off the lobby service.
- Turn off Discord status in the launcher or in game.
- Ask us to delete your lobby player ID, or anything you sent us, by opening an issue. Never post your key; tell us the player name you use online.

## Changes and contact

When this policy changes, the version and date above change, the change is listed in the release notes, and the launcher asks you to agree again.

Questions and requests: https://github.com/Badger-Field/ReSkate/issues.
