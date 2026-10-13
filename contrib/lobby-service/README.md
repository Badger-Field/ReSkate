# ReSkate lobby service

What players need from a server to play directly with each other, on Cloudflare Workers (free
plan) with one Durable Object:

- **Signaling.** While two players' games find a route to each other (GameNetworkingSockets'
  ICE), they exchange a handful of small messages through here. Each player keeps one WebSocket
  open. A text message `steamid:<to> <hex>` from one arrives at the other as
  `steamid:<from> <hex>`, the line protocol of GameNetworkingSockets' trivial signaling server.
- **The public lobby list.** A host sends `{"op":"host","lobby":{...}}` (a JSON object of at
  most 2000 characters) and republishes it each minute; `{"op":"list"}` returns the rows. A
  lobby goes when its host disconnects or stops republishing for two minutes.

Join codes need nothing here: a ReSkate invite (`<host id>-<secret>`) already names its host.

## Who a player is

A player is their Ed25519 key:

1. `GET /v1/challenge` returns `{"challenge": "..."}`, valid for 60 seconds.
2. They sign `reskate-lobby-v1:<challenge>` with their key.
3. They connect to `/v1/connect?key=<public key hex>&sig=<signature hex>&challenge=<challenge>`.

Their player ID is derived from the key: a Steam-shaped ID in Steam's development universe, with
the first 4 bytes of SHA-256(key) as the account number. This matches `direct_player_id` in
`Extension/Multiplayer/Direct/direct_signaling.h`. Nobody can receive another player's signals,
and real Steam IDs can't be claimed. The first message on a connection is
`{"op":"hello","id":"<player id>"}`.

Each player may send 400 messages per 10 seconds. A second connection with the same key replaces
the first.

## Run and test locally

```
npm install
npm test        # runs the service with wrangler dev and drives it as games do
npm run dev     # http://localhost:8787
```

## Deploy

```
npx wrangler login
npx wrangler secret put CHALLENGE_SECRET    # any long random string
npx wrangler deploy
```

Until `CHALLENGE_SECRET` is set, the deployed service refuses players (HTTP 503) and
`/v1/health` reports `{"ok":false}`. A development secret is used only with `DEV=1`, which
the tests pass and `npm run dev` reads from `.dev.vars`. To use your own domain, add a route or custom domain for `reskate-lobby`
in the Cloudflare dashboard, for example `lobby.example.com`.
