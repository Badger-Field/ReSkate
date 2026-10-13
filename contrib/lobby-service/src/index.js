// ReSkate lobby service: what players need from a server to play directly with each other.
//
// 1. Signaling. While two players find a route to each other (GameNetworkingSockets' ICE), their
//    games exchange a handful of small messages. Each player keeps one WebSocket open here, and
//    a line "<to> <hex>" from one arrives at the other as "<from> <hex>": the line protocol of
//    GameNetworkingSockets' trivial signaling server, one WebSocket text message a line.
// 2. The public lobby list: hosts publish a row, anyone can list them.
//
// Who a player is comes from their own key, not from what they claim: they fetch a challenge,
// sign it with their Ed25519 key and connect with key and signature. Their player ID is derived
// from the key (direct_player_id in Extension/Multiplayer/Direct/direct_signaling.h), so nobody
// can take another player's signals.
//
// Join codes need nothing here: a ReSkate invite already names its host's player ID.
import { DurableObject } from "cloudflare:workers";

const CHALLENGE_LIFETIME_MS = 60_000;
const LOBBY_LIFETIME_MS = 120_000; // a host republishes every minute
const MAX_MESSAGE = 40_000; // characters: a signal is a few KB as hex
const MAX_LOBBY = 2_000; // characters of a lobby row
const RATE_WINDOW_MS = 10_000;
const RATE_MAX = 400; // messages a player may send in a window
const PLAYER_RETENTION_MS = 30 * 24 * 3600_000; // an ID's owner is kept this long after its last connection
const NEW_KEYS_PER_HOUR = 20; // new player keys one address may introduce an hour
// Local development and tests only (DEV=1 in .dev.vars or --var DEV:1): a deployed service
// without CHALLENGE_SECRET refuses players rather than sign with a value that is public here.
const DEV_SECRET = "reskate-local-development-only";

// ---------------------------------------------------------------- identity

const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (text) => {
  if (typeof text !== "string" || text.length % 2 || !/^[0-9a-f]*$/i.test(text)) return null;
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(text.substr(i * 2, 2), 16);
  return out;
};

// A direct player's ID: a Steam-shaped individual account in Steam's development universe
// (universe 4, type 1, instance 1), its account number the first 4 bytes of SHA-256(key).
export async function playerId(publicKey) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", publicKey));
  let account = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
  if (!account) account = 1;
  return ((4n << 56n) | (1n << 52n) | (1n << 32n) | BigInt(account)).toString();
}

async function hmac(secret, text) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text)));
}

// "<time>.<random>.<mac>": the service can check it is its own and recent without storing it.
async function makeChallenge(secret, now) {
  const body = `${now}.${hex(crypto.getRandomValues(new Uint8Array(16)))}`;
  return `${body}.${await hmac(secret, body)}`;
}

async function checkChallenge(secret, challenge, now) {
  const parts = typeof challenge === "string" ? challenge.split(".") : [];
  if (parts.length !== 3) return false;
  const [time, random, mac] = parts;
  const issued = Number(time);
  if (!Number.isSafeInteger(issued) || now - issued > CHALLENGE_LIFETIME_MS || issued - now > 5_000) return false;
  return (await hmac(secret, `${time}.${random}`)) === mac;
}

// What a player signs: the challenge, under a label so the signature means nothing elsewhere.
export const signedText = (challenge) => `reskate-lobby-v1:${challenge}`;

async function verify(key, signature, challenge) {
  try {
    const publicKey = await crypto.subtle.importKey("raw", key, { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, publicKey, signature, new TextEncoder().encode(signedText(challenge)));
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- worker

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const secret = env.CHALLENGE_SECRET || (env.DEV === "1" ? DEV_SECRET : null);
    if (url.pathname === "/v1/health") return Response.json({ ok: Boolean(secret) });
    if (!secret) return new Response("The service has no CHALLENGE_SECRET yet.", { status: 503 });
    if (url.pathname === "/v1/challenge") return Response.json({ challenge: await makeChallenge(secret, Date.now()) });
    if (url.pathname !== "/v1/connect") return new Response("Not found", { status: 404 });
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") return new Response("Expected a WebSocket", { status: 426 });

    const key = unhex(url.searchParams.get("key"));
    const signature = unhex(url.searchParams.get("sig"));
    const challenge = url.searchParams.get("challenge");
    if (!key || key.length !== 32 || !signature || signature.length !== 64) return new Response("Bad key or signature", { status: 400 });
    if (!(await checkChallenge(secret, challenge, Date.now()))) return new Response("Challenge expired or not ours", { status: 401 });
    if (!(await verify(key, signature, challenge))) return new Response("Signature does not match the key", { status: 401 });

    const id = await playerId(key);
    const forwarded = new Request(request);
    forwarded.headers.set("X-Player", id);
    forwarded.headers.set("X-Player-Key", hex(key));
    forwarded.headers.set("X-Challenge", challenge);
    // One hub for everyone: signaling only flows while players connect, and a lobby list is
    // small. Shard by region if it ever gets busy.
    return env.HUB.get(env.HUB.idFromName("hub")).fetch(forwarded);
  },
};

// ---------------------------------------------------------------- hub

export class Hub extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS lobbies (host TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER NOT NULL)");
    // A player ID is 32 bits of the key's hash, so two keys can share one: the first key to
    // connect as an ID owns it, and another key is refused (its game makes a new key).
    this.sql.exec("CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, key TEXT NOT NULL, seen INTEGER NOT NULL)");
    // Each challenge signs one connection: one captured with its signature cannot be replayed.
    this.sql.exec("CREATE TABLE IF NOT EXISTS used_challenges (challenge TEXT PRIMARY KEY, used INTEGER NOT NULL)");
    // New keys per address per hour: every key that connects is kept (for ID ownership), so
    // the number one address can introduce is limited, and old ones are pruned.
    this.sql.exec("CREATE TABLE IF NOT EXISTS admissions (address TEXT NOT NULL, hour INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (address, hour))");
  }

  async fetch(request) {
    const id = request.headers.get("X-Player");
    const key = request.headers.get("X-Player-Key");
    const challenge = request.headers.get("X-Challenge");
    if (!id || !key || !challenge) return new Response("No player", { status: 400 });
    const now = Date.now();
    this.sql.exec("DELETE FROM used_challenges WHERE used < ?", now - 2 * CHALLENGE_LIFETIME_MS);
    if (this.sql.exec("SELECT 1 FROM used_challenges WHERE challenge = ?", challenge).toArray().length)
      return new Response("Challenge already used", { status: 401 });
    this.sql.exec("INSERT INTO used_challenges (challenge, used) VALUES (?, ?)", challenge, now);
    // Owners not seen for a month give their ID up; a returning player simply owns it again.
    this.sql.exec("DELETE FROM players WHERE seen < ?", now - PLAYER_RETENTION_MS);
    const owner = this.sql.exec("SELECT key FROM players WHERE id = ?", id).toArray()[0];
    if (owner && owner.key !== key)
      return new Response("This key's player ID belongs to another key: make a new key.", { status: 409 });
    if (!owner) {
      const address = request.headers.get("CF-Connecting-IP") || "local";
      const hour = Math.floor(now / 3600_000);
      this.sql.exec("DELETE FROM admissions WHERE hour < ?", hour);
      const used = this.sql.exec("SELECT count FROM admissions WHERE address = ? AND hour = ?", address, hour).toArray()[0]?.count ?? 0;
      if (used >= NEW_KEYS_PER_HOUR) return new Response("Too many new players from this address; try again later.", { status: 429 });
      this.sql.exec("INSERT OR REPLACE INTO admissions (address, hour, count) VALUES (?, ?, ?)", address, hour, used + 1);
    }
    this.sql.exec("INSERT OR REPLACE INTO players (id, key, seen) VALUES (?, ?, ?)", id, key, now);
    // A player has one connection: a new one (a restart, a reconnect) replaces the old.
    for (const old of this.ctx.getWebSockets(id)) {
      try { old.close(4000, "Replaced by a newer connection"); } catch {}
    }
    const [client, server] = Object.values(new WebSocketPair());
    // Hibernation: idle connections cost nothing while players wait in a lobby.
    this.ctx.acceptWebSocket(server, [id]);
    server.serializeAttachment({ id, window: Date.now(), count: 0 });
    server.send(JSON.stringify({ op: "hello", id }));
    return new Response(null, { status: 101, webSocket: client });
  }

  webSocketMessage(ws, message) {
    const me = ws.deserializeAttachment();
    if (typeof message !== "string" || message.length > MAX_MESSAGE) return;
    const now = Date.now();
    if (now - me.window > RATE_WINDOW_MS) { me.window = now; me.count = 0; }
    if (++me.count > RATE_MAX) {
      ws.serializeAttachment(me);
      if (me.count === RATE_MAX + 1) ws.send(JSON.stringify({ op: "error", error: "Too many messages; slow down." }));
      return;
    }
    ws.serializeAttachment(me);
    if (message.startsWith("{")) return this.control(ws, me.id, message, now);
    this.signal(ws, me.id, message);
  }

  // "steamid:<to> <hex>" in, "steamid:<from> <hex>" out.
  signal(ws, from, line) {
    const match = /^steamid:(\d{1,20}) ([0-9a-f]+)\r?\n?$/i.exec(line);
    if (!match || match[2].length % 2) return;
    const targets = this.ctx.getWebSockets(match[1]);
    if (!targets.length) {
      ws.send(JSON.stringify({ op: "offline", id: match[1] }));
      return;
    }
    for (const target of targets) {
      try { target.send(`steamid:${from} ${match[2]}`); } catch {}
    }
  }

  control(ws, id, text, now) {
    let request;
    try { request = JSON.parse(text); } catch { return; }
    const reply = (value) => ws.send(JSON.stringify(value));
    switch (request?.op) {
      case "ping":
        return reply({ op: "pong" });
      case "host": {
        const row = request.lobby;
        const data = JSON.stringify(row ?? null);
        if (!row || typeof row !== "object" || Array.isArray(row) || data.length > MAX_LOBBY)
          return reply({ op: "error", error: "A lobby row is a JSON object of at most 2000 characters." });
        this.sql.exec("INSERT OR REPLACE INTO lobbies (host, data, updated) VALUES (?, ?, ?)", id, data, now);
        return reply({ op: "hosted" });
      }
      case "unhost":
        this.sql.exec("DELETE FROM lobbies WHERE host = ?", id);
        return reply({ op: "unhosted" });
      case "list": {
        this.sql.exec("DELETE FROM lobbies WHERE updated < ?", now - LOBBY_LIFETIME_MS);
        const rows = this.sql.exec("SELECT host, data FROM lobbies ORDER BY updated DESC LIMIT 200").toArray()
          // The host is who published the row, whatever the row itself says.
          .map(({ host, data }) => ({ ...JSON.parse(data), host }));
        return reply({ op: "lobbies", rows });
      }
      default:
        return reply({ op: "error", error: "Unknown request." });
    }
  }

  webSocketClose(ws, code) {
    // Finish the closing handshake, so the player's game sees the close at once.
    try { ws.close(code === 1005 ? 1000 : code, "Bye"); } catch {}
    this.left(ws);
  }
  webSocketError(ws) { this.left(ws); }

  left(ws) {
    const { id } = ws.deserializeAttachment() ?? {};
    // Its lobby goes with it, unless the player has already reconnected.
    if (id && !this.ctx.getWebSockets(id).some((other) => other !== ws)) this.sql.exec("DELETE FROM lobbies WHERE host = ?", id);
  }
}
