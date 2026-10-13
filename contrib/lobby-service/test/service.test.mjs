// Runs the service with `wrangler dev` (Cloudflare's own runtime, workerd, with the Durable
// Object on SQLite as deployed) and drives it as players' games do.  npm test
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { request as httpRequest } from "node:http";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = 8000 + Math.floor(Math.random() * 1000);
const BASE = `http://127.0.0.1:${PORT}`;
const AGENT = { "User-Agent": "ReSkate-Lobby/1" }; // what the game sends
let dev, state;

before(async () => {
  // Wrangler's own script under this Node, not `npx`, so it starts the same way on Windows.
  const root = fileURLToPath(new URL("..", import.meta.url));
  const wrangler = fileURLToPath(new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url));
  // A fresh Durable Object every run: the service remembers players and limits per address.
  state = mkdtempSync(join(tmpdir(), "reskate-lobby-test-"));
  dev = spawn(process.execPath, [wrangler, "dev", "--port", String(PORT), "--ip", "127.0.0.1", "--var", "DEV:1",
    "--persist-to", state], {
    cwd: root,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32", // its own process group, to stop it with its children
    // Offline: no usage metrics, and no fetch of Cloudflare's request metadata (request.cf).
    env: { ...process.env, WRANGLER_SEND_METRICS: "false", CLOUDFLARE_CF_FETCH_ENABLED: "false" },
  });
  let log = "";
  dev.stdout.on("data", (d) => (log += d));
  dev.stderr.on("data", (d) => (log += d));
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${BASE}/v1/health`)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`wrangler dev did not start:\n${log}`);
});

after(async () => {
  const exited = new Promise((resolve) => (dev.exitCode !== null || dev.signalCode !== null ? resolve() : dev.once("exit", resolve)));
  try {
    if (process.platform === "win32") spawn("taskkill", ["/pid", String(dev.pid), "/T", "/F"]);
    else process.kill(-dev.pid);
  } catch {}
  await Promise.race([exited, new Promise((r) => setTimeout(r, 10_000))]);
  // workerd may let go of its files a moment after wrangler exits (on Windows especially).
  for (let i = 0; i < 10; i++) {
    try { rmSync(state, { recursive: true, force: true }); break; } catch { await new Promise((r) => setTimeout(r, 300)); }
  }
});

// A player: an Ed25519 key, and their ID as the game and the service derive it.
function player() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = Buffer.from(publicKey.export({ format: "jwk" }).x, "base64url");
  const digest = createHash("sha256").update(raw).digest();
  const account = digest.readUInt32BE(0) || 1;
  const id = ((4n << 56n) | (1n << 52n) | (1n << 32n) | BigInt(account)).toString();
  return { raw, privateKey, id };
}

async function connectUrl(p, { badSignature = false, challenge = null } = {}) {
  challenge ??= (await (await fetch(`${BASE}/v1/challenge`, { headers: AGENT })).json()).challenge;
  let signature = sign(null, Buffer.from(`reskate-lobby-v1:${challenge}`), p.privateKey);
  if (badSignature) signature = Buffer.from(signature.map((b, i) => (i === 0 ? b ^ 1 : b)));
  const query = new URLSearchParams({ key: p.raw.toString("hex"), sig: signature.toString("hex"), challenge });
  return `ws://127.0.0.1:${PORT}/v1/connect?${query}`;
}

// A connection that collects what arrives; next() waits for the next message matching `want`.
// `address`: the network the player is on (development only: X-Test-Address).
async function connect(p, { address } = {}) {
  const ws = new WebSocket(await connectUrl(p), { headers: address ? { ...AGENT, "X-Test-Address": address } : AGENT });
  const inbox = [];
  const waiters = [];
  ws.addEventListener("message", (event) => {
    const text = String(event.data);
    const waiter = waiters.findIndex((w) => w.want(text));
    if (waiter >= 0) waiters.splice(waiter, 1)[0].resolve(text);
    else inbox.push(text);
  });
  const closed = new Promise((resolve) => ws.addEventListener("close", (e) => resolve(e.code)));
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  const next = (want = () => true, ms = 5000) => {
    const found = inbox.findIndex(want);
    if (found >= 0) return Promise.resolve(inbox.splice(found, 1)[0]);
    return new Promise((resolve, reject) => {
      const waiter = { want, resolve };
      waiters.push(waiter);
      setTimeout(() => {
        const at = waiters.indexOf(waiter);
        if (at >= 0) { waiters.splice(at, 1); reject(new Error("timed out waiting for a message")); }
      }, ms);
    });
  };
  const op = (name) => (text) => text.startsWith("{") && JSON.parse(text).op === name;
  return { ws, next, op, closed, send: (x) => ws.send(typeof x === "string" ? x : JSON.stringify(x)) };
}

test("health", async () => {
  // With DEV=1 the development secret stands in; deployed, CHALLENGE_SECRET must be set.
  assert.deepEqual(await (await fetch(`${BASE}/v1/health`)).json(), { ok: true });
});

// The status a WebSocket upgrade request gets (fetch() will not send an Upgrade header).
const upgradeStatus = (url, agent = AGENT) => new Promise((resolve, reject) => {
  const request = httpRequest(url, { headers: { ...agent, Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==" } });
  request.on("response", (response) => { response.resume(); resolve(response.statusCode); });
  request.on("upgrade", (response, socket) => { socket.destroy(); resolve(response.statusCode); });
  request.on("error", reject);
  request.end();
});

test("anything but the game gets a 404", async () => {
  assert.equal((await fetch(`${BASE}/v1/challenge`)).status, 404);
  assert.equal((await fetch(`${BASE}/v1/challenge`, { headers: { "User-Agent": "Mozilla/5.0" } })).status, 404);
  assert.equal((await fetch(`${BASE}/wp-login.php`, { headers: AGENT })).status, 404);
  const p = player();
  const url = new URL((await connectUrl(p)).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(url, {}), 404);
  assert.equal(await upgradeStatus(url), 101); // the same link from the game still works
});

test("a wrong signature or a stale challenge is refused", async () => {
  const p = player();
  const url = new URL((await connectUrl(p, { badSignature: true })).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(url), 401);
  // A challenge the service did make (right MAC, the development secret), correctly signed,
  // but two minutes old: refused for its age alone.
  const body = `${Date.now() - 120_000}.${randomBytes(16).toString("hex")}`;
  const stale = `${body}.${createHmac("sha256", "reskate-local-development-only").update(body).digest("hex")}`;
  assert.equal(await upgradeStatus(new URL((await connectUrl(p, { challenge: stale })).replace("ws:", "http:"))), 401);
  const good = new URL((await connectUrl(p)).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(good), 101);
  // The same signed challenge again: refused, so a captured link cannot take the player over.
  assert.equal(await upgradeStatus(good), 401);
});

test("players are who their key says, and signals go between them", async () => {
  const alice = player(), bob = player();
  const a = await connect(alice), b = await connect(bob);
  assert.equal(JSON.parse(await a.next(a.op("hello"))).id, alice.id);
  assert.equal(JSON.parse(await b.next(b.op("hello"))).id, bob.id);

  a.send(`steamid:${bob.id} 0a0bff`);
  assert.equal(await b.next((t) => t.startsWith("steamid:")), `steamid:${alice.id} 0a0bff`);
  b.send(`steamid:${alice.id} 01`);
  assert.equal(await a.next((t) => t.startsWith("steamid:")), `steamid:${bob.id} 01`);

  // To someone who is not here: dropped, and the sender is not told (whether a player is
  // online is nobody's business). A ping still answers, so the silence is not a dead socket.
  a.send(`steamid:${player().id} 00`);
  a.send({ op: "ping" });
  await a.next(a.op("pong"));
  await assert.rejects(a.next((t) => t.includes("offline"), 300));

  // Too long a signal is dropped; so is a flood from one player to another.
  a.send(`steamid:${bob.id} ${"ab".repeat(6_001)}`);
  for (let i = 0; i < 80; i++) a.send(`steamid:${bob.id} ${(i % 256).toString(16).padStart(2, "0")}`);
  a.send({ op: "ping" });
  await a.next(a.op("pong"));
  await new Promise((r) => setTimeout(r, 300));
  const got = [];
  for (;;) {
    try { got.push(await b.next((t) => t.startsWith("steamid:"), 100)); } catch { break; }
  }
  assert.ok(got.every((t) => t.length < 100), "the long signal was dropped");
  assert.equal(got.length, 59, "60 signals a window from one player to another (one went before)");
  a.ws.close();
  b.ws.close();
});

test("a player lists at most every few seconds", async () => {
  const p = await connect(player());
  await p.next(p.op("hello"));
  p.send({ op: "list" });
  await p.next(p.op("lobbies"));
  p.send({ op: "list" });
  assert.match(JSON.parse(await p.next(p.op("error"))).error, /too often/);
  p.ws.close();
});

test("public lobbies are listed while their host is here", async () => {
  const host = player(), browser = player();
  const h = await connect(host), v = await connect(browser);
  // A row naming someone else as host is still listed under who published it.
  h.send({ op: "host", lobby: { name: "Tuesday sesh", map: "bam", players: 2, max: 8, password: false, host: browser.id } });
  await h.next(h.op("hosted"));
  v.send({ op: "list" });
  let rows = JSON.parse(await v.next(v.op("lobbies"))).rows;
  assert.ok(rows.some((r) => r.host === host.id && r.name === "Tuesday sesh" && r.max === 8));
  assert.ok(!rows.some((r) => r.host === browser.id));

  h.send({ op: "host", lobby: { name: "x".repeat(3000) } });
  await h.next(h.op("error"));

  const started = Date.now();
  h.ws.close();
  await h.closed;
  assert.ok(Date.now() - started < 2000, "the service answers a close at once");
  await new Promise((r) => setTimeout(r, 300));
  const later = await connect(player()); // a player lists at most every few seconds
  await later.next(later.op("hello"));
  later.send({ op: "list" });
  rows = JSON.parse(await later.next(later.op("lobbies"))).rows;
  assert.ok(!rows.some((r) => r.host === host.id), "a host's lobby leaves with them, list cache and all");
  later.ws.close();
  v.ws.close();
});

test("only public games are listed, whoever shares the host's address", async () => {
  // Players behind one address (a home, or strangers behind carrier-grade NAT) are not told
  // about each other's games: a game that is not public is refused, not listed.
  const dad = player(), son = player(), phone = player();
  const d = await connect(dad, { address: "203.0.113.9" });
  const s = await connect(son, { address: "203.0.113.9" });
  const p = await connect(phone, { address: "198.51.100.7" });
  for (const c of [d, s, p]) await c.next(c.op("hello"));
  d.send({ op: "host", lobby: { name: "Dad's park" }, public: false });
  assert.equal(JSON.parse(await d.next(d.op("error"))).error, "Only public games are listed.");
  p.send({ op: "host", lobby: { name: "Open session" } }); // public by default
  await p.next(p.op("hosted"));
  s.send({ op: "list" });
  const seen = JSON.parse(await s.next(s.op("lobbies"))).rows;
  assert.ok(!seen.some((r) => r.host === dad.id), "the non-public game is not listed, even to the same address");
  assert.deepEqual(seen.find((r) => r.host === phone.id), { name: "Open session", host: phone.id });
  assert.ok(!JSON.stringify(seen).includes("198.51") && !JSON.stringify(seen).includes("203.0"), "no addresses");
  for (const c of [d, s, p]) c.ws.close();
});

test("a second connection replaces the first", async () => {
  const p = player();
  const first = await connect(p);
  await first.next(first.op("hello"));
  const second = await connect(p);
  await second.next(second.op("hello"));
  assert.equal(await first.closed, 4000);
  second.ws.close();
});

test("a player who floods the service is disconnected, binary frames included", async () => {
  const p = player();
  const flood = await connect(p);
  await flood.next(flood.op("hello"));
  for (let i = 0; i < 401; i++) flood.ws.send(new Uint8Array([i & 255]));
  assert.equal(await flood.closed, 4008);
});

test("two keys that share a player ID: the second is refused", async () => {
  // 32-bit IDs collide by the birthday bound after about 77,000 keys: make a real collision.
  const seen = new Map();
  let pair;
  while (!pair) {
    const p = player();
    const other = seen.get(p.id);
    if (other) pair = [other, p];
    else seen.set(p.id, p);
  }
  const [first, second] = pair;
  const a = await connect(first);
  await a.next(a.op("hello"));
  const url = new URL((await connectUrl(second)).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(url), 409);
  a.ws.close();
});

// Last: it uses up this address's new-player allowance for the hour.
test("one address can bring only so many new players an hour", async () => {
  const statuses = [];
  for (let i = 0; i < 25; i++) {
    const url = new URL((await connectUrl(player())).replace("ws:", "http:"));
    statuses.push(await upgradeStatus(url));
  }
  // The earlier tests already brought some of the 20; the rest get in, then 429.
  const firstRefused = statuses.indexOf(429);
  assert.ok(firstRefused > 0, `some got in before the limit: ${statuses}`);
  assert.ok(statuses.slice(0, firstRefused).every((s) => s === 101));
  assert.ok(statuses.slice(firstRefused).every((s) => s === 429));
});
