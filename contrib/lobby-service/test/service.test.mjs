// Runs the service with `wrangler dev` (Cloudflare's own runtime, workerd, with the Durable
// Object on SQLite as deployed) and drives it as players' games do.  npm test
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { request as httpRequest } from "node:http";
import { fileURLToPath } from "node:url";

const PORT = 8000 + Math.floor(Math.random() * 1000);
const BASE = `http://127.0.0.1:${PORT}`;
let dev;

before(async () => {
  dev = spawn("npx", ["wrangler", "dev", "--port", String(PORT), "--ip", "127.0.0.1"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
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

after(() => {
  try { process.kill(-dev.pid); } catch {}
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

async function connectUrl(p, { badSignature = false } = {}) {
  const { challenge } = await (await fetch(`${BASE}/v1/challenge`)).json();
  let signature = sign(null, Buffer.from(`reskate-lobby-v1:${challenge}`), p.privateKey);
  if (badSignature) signature = Buffer.from(signature.map((b, i) => (i === 0 ? b ^ 1 : b)));
  const query = new URLSearchParams({ key: p.raw.toString("hex"), sig: signature.toString("hex"), challenge });
  return `ws://127.0.0.1:${PORT}/v1/connect?${query}`;
}

// A connection that collects what arrives; next() waits for the next message matching `want`.
async function connect(p) {
  const ws = new WebSocket(await connectUrl(p));
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
  assert.deepEqual(await (await fetch(`${BASE}/v1/health`)).json(), { ok: true });
});

// The status a WebSocket upgrade request gets (fetch() will not send an Upgrade header).
const upgradeStatus = (url) => new Promise((resolve, reject) => {
  const request = httpRequest(url, { headers: { Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==" } });
  request.on("response", (response) => { response.resume(); resolve(response.statusCode); });
  request.on("upgrade", (response, socket) => { socket.destroy(); resolve(response.statusCode); });
  request.on("error", reject);
  request.end();
});

test("a wrong signature or a stale challenge is refused", async () => {
  const p = player();
  const url = new URL((await connectUrl(p, { badSignature: true })).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(url), 401);
  const forged = new URL(url);
  forged.searchParams.set("challenge", `${Date.now() - 120_000}.00.00`);
  assert.equal(await upgradeStatus(forged), 401);
  const good = new URL((await connectUrl(p)).replace("ws:", "http:"));
  assert.equal(await upgradeStatus(good), 101);
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

  // To someone who is not here: the sender is told, nothing else happens.
  a.send(`steamid:${player().id} 00`);
  await a.next(a.op("offline"));
  a.ws.close();
  b.ws.close();
});

test("public lobbies are listed while their host is here", async () => {
  const host = player(), browser = player();
  const h = await connect(host), v = await connect(browser);
  h.send({ op: "host", lobby: { name: "Tuesday sesh", map: "bam", players: 2, max: 8, password: false } });
  await h.next(h.op("hosted"));
  v.send({ op: "list" });
  let rows = JSON.parse(await v.next(v.op("lobbies"))).rows;
  assert.ok(rows.some((r) => r.host === host.id && r.name === "Tuesday sesh" && r.max === 8));

  h.send({ op: "host", lobby: { name: "x".repeat(3000) } });
  await h.next(h.op("error"));

  const started = Date.now();
  h.ws.close();
  await h.closed;
  assert.ok(Date.now() - started < 2000, "the service answers a close at once");
  await new Promise((r) => setTimeout(r, 300));
  v.send({ op: "list" });
  rows = JSON.parse(await v.next(v.op("lobbies"))).rows;
  assert.ok(!rows.some((r) => r.host === host.id), "a host's lobby leaves with them");
  v.ws.close();
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
