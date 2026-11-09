// CX-WALL (owner ruling X-1) at the gateway: the conversation's computer channel routes take a
// channel token and nothing else; the token is learned from EE's own mint answer, bound to its
// conversation and the minting key's claims, renewed and forgotten in step with EE, and never
// accepted on any other route nor written to a log line. Real sockets; EE is a recording stub.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { loadConfig } from "../src/policy.mjs";
import { compileConsoleRouteManifest, MANIFEST_PATH } from "../src/manifest.mjs";
import { createChannelBindings } from "../src/channel-token/bindings.mjs";
import { redactSecrets } from "../src/channel-token/redact.mjs";
import { stderrSink } from "../src/refusal-log.mjs";
import { cloudEnv, json, mintAssertion, startGateway } from "./helpers.mjs";

const TERMINAL = "0123abcd-0123-4abc-8def-0123456789ab";
const tokenOf = (fill) => `cct_${fill.repeat(43).slice(0, 43)}`;
const A = tokenOf("a");
const base = (conversation) => `/workspace/conversations/${conversation}/computer`;
const events = (conversation, token) => `${base(conversation)}/terminals/${TERMINAL}/events?token=${token}&cursor=0`;
const input = (conversation, token) => `${base(conversation)}/terminals/${TERMINAL}/input?token=${token}`;
const raw = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));

/** A stub EE: the mint answers `token` for `c-1`; channel routes answer by `channel`. */
function stubEe({ token = A, ttlMs = 60_000, channel } = {}) {
  return (url, init) => {
    const { pathname } = new URL(url);
    if (init.method === "POST" && pathname.endsWith("/computer/channel")) {
      const conversationId = pathname.split("/")[3];
      return json({ schemaVersion: "omega-conversation-computer/v1", channel: {
        token, conversationId, computerId: "cmp_1", generation: "cmp_1", expiresAt: new Date(Date.now() + ttlMs).toISOString(), ttlSeconds: 60, renewEverySeconds: 20,
      } }, 201);
    }
    if (channel) return channel(url, init);
    return json({ schemaVersion: "omega-conversation-computer/v1", acceptedBytes: 1 });
  };
}

async function gatewayWith(t, upstream, { log, refusalLog } = {}) {
  const config = loadConfig(cloudEnv(t));
  const lines = [];
  const started = await startGateway(t, {
    config, handler: { upstream },
    log: log ?? ((record) => lines.push(JSON.stringify(record))),
    refusalLog: refusalLog ?? ((entry) => lines.push(JSON.stringify(entry))),
  });
  const call = (method, target, { key = true, headers = {}, body } = {}) => fetch(`${started.base}${target}`, {
    method, headers: { ...(key ? { authorization: `Bearer ${mintAssertion()}` } : {}), ...(body ? { "content-type": "application/json" } : {}), ...headers }, body,
  });
  return { ...started, call, lines, config };
}

test("W2 compiles the credential field strictly and binds every channel route to exactly one mint", () => {
  const manifest = compileConsoleRouteManifest(raw);
  const channel = manifest.routes.filter((route) => route.credential === "channel").map((route) => `${route.method} ${route.path}`).sort();
  assert.deepEqual(channel, [
    "GET /workspace/conversations/{id}/computer/terminals/{terminal}/events",
    "POST /workspace/conversations/{id}/computer/terminals/{terminal}/close",
    "POST /workspace/conversations/{id}/computer/terminals/{terminal}/input",
    "POST /workspace/conversations/{id}/computer/terminals/{terminal}/resize",
  ]);
  assert.deepEqual(manifest.routes.filter((route) => route.credential === "channel-mint").map((route) => route.path), ["/workspace/conversations/{id}/computer/channel"]);
  const broken = (mutate) => () => { const copy = structuredClone(raw); mutate(copy); return compileConsoleRouteManifest(copy); };
  const at = (copy, method, path) => copy.routes.find((route) => route.method === method && route.path === path);
  assert.throws(broken((m) => { m.routes[0].credential = "cookie"; }), /unknown credential/);
  assert.throws(broken((m) => { m.routes[0].credential = "key"; }), /unknown credential/);
  assert.throws(broken((m) => { m.eeOnly[0].credential = "channel"; }), /wrong manifest list/);
  assert.throws(broken((m) => { m.routes[0].credential = "channel"; }), /exactly one channel mint/);
  assert.throws(broken((m) => { delete at(m, "POST", "/workspace/conversations/{id}/computer/channel").credential; }), /exactly one channel mint/);
  assert.throws(broken((m) => { at(m, "POST", "/workspace/conversations/{id}/computer/channel").method = "PUT"; }), /must be a POST/);
  assert.throws(broken((m) => { for (const route of m.routes) if (route.credential === "channel") delete route.credential; }), /binds no channel route/);
  assert.throws(broken((m) => { m.routes[0].rate = "channel"; }), /channel rate class/);
  assert.throws(broken((m) => { at(m, "POST", "/workspace/conversations/{id}/computer/terminals/{terminal}/input").rate = "write"; }), /channel rate class/);
});

test("a minted token admits its conversation's channel routes, with the token alone, and EE sees no assertion", async (t) => {
  const { call, calls } = await gatewayWith(t, stubEe());
  const minted = await call("POST", `${base("c-1")}/channel`);
  assert.equal(minted.status, 201);
  assert.equal((await minted.json()).channel.token, A, "EE's answer reaches the browser unchanged");
  const before = calls.upstream.length;
  const sent = await call("POST", input("c-1", A), { key: false, body: JSON.stringify({ data: "bHM=" }) });
  assert.equal(sent.status, 200);
  const forwarded = calls.upstream.at(-1);
  assert.equal(calls.upstream.length, before + 1);
  assert.equal(new URL(forwarded.url).pathname, `${base("c-1")}/terminals/${TERMINAL}/input`);
  assert.equal(new URL(forwarded.url).searchParams.get("token"), A);
  assert.equal(forwarded.init.headers.authorization, undefined, "no host assertion travels with a channel request");
  assert.equal(forwarded.init.body.toString(), JSON.stringify({ data: "bHM=" }));
  assert.equal(calls.exchange.length, 0);
});

test("a host key is never admitted on a channel route, and is never spent there", async (t) => {
  const { call, calls } = await gatewayWith(t, stubEe());
  await call("POST", `${base("c-1")}/channel`);
  const upstream = calls.upstream.length;
  for (const authorization of [`Bearer ${mintAssertion()}`, "Bearer futureagi-browser-token", `Bearer ${A}`, "Basic dTpw"]) {
    for (const target of [events("c-1", A), input("c-1", A), `${base("c-1")}/terminals/${TERMINAL}/events`]) {
      const answer = await call(target.includes("/input") ? "POST" : "GET", target, { key: false, headers: { authorization } });
      assert.equal(answer.status, 401, `${authorization.slice(0, 16)} ${target}`);
      assert.deepEqual(await answer.json().then(({ error, reason }) => ({ error, reason })), { error: "channel_token_invalid", reason: "credential_on_channel_route" });
    }
  }
  assert.equal(calls.upstream.length, upstream, "nothing reached EE");
  assert.equal(calls.exchange.length, 0, "no browser token was exchanged");
});

test("a token is refused for another conversation, when unknown or malformed, and after it expires", async (t) => {
  const { call, calls } = await gatewayWith(t, stubEe({ ttlMs: 1_500 }));
  await call("POST", `${base("c-1")}/channel`);
  const upstream = calls.upstream.length;
  const other = await call("GET", events("c-2", A), { key: false });
  assert.equal(other.status, 404);
  assert.equal((await other.json()).reason, "channel_token_other_scope");
  assert.equal((await call("GET", events("c-1", tokenOf("b")), { key: false })).status, 401);
  for (const target of [`${base("c-1")}/terminals/${TERMINAL}/events?token=cct_short`, `${events("c-1", A)}&token=${A}`, `${base("c-1")}/terminals/${TERMINAL}/events`]) {
    const answer = await call("GET", target, { key: false });
    assert.equal(answer.status, 401, target);
    assert.equal((await answer.json()).reason, "malformed_channel_token");
  }
  assert.equal((await call("GET", `${events("c-1", A)}&TOKEN=x`, { key: false })).status, 400);
  assert.equal((await call("GET", `${events("c-1", A)}&access_token=x`, { key: false })).status, 400);
  assert.equal(calls.upstream.length, upstream, "none of them reached EE");
  assert.equal((await call("POST", input("c-1", A), { key: false, body: "{}" })).status, 200);
  await new Promise((resolve) => setTimeout(resolve, 1_600));
  const expired = await call("POST", input("c-1", A), { key: false, body: "{}" });
  assert.equal(expired.status, 401);
  assert.equal((await expired.json()).reason, "expired_channel_token");
  assert.equal(calls.upstream.length, upstream + 1);
});

test("the token is never accepted on any other route, as a query or as a bearer", async (t) => {
  const { call, calls } = await gatewayWith(t, stubEe());
  await call("POST", `${base("c-1")}/channel`);
  const upstream = calls.upstream.length;
  for (const [method, path] of [["GET", base("c-1")], ["POST", `${base("c-1")}/channel`], ["GET", `${base("c-1")}/terminals`],
    ["GET", "/workspace/conversations/c-1/events"], ["GET", "/workspace/usage"], ["PUT", `${base("c-1")}/files/a.txt`]]) {
    const query = await call(method, `${path}?token=${A}`, { key: false });
    assert.equal(query.status, 400, `${method} ${path}?token`);
    assert.equal((await query.json()).error, "credential_in_query");
    const bearer = await call(method, path, { key: false, headers: { authorization: `Bearer ${A}` } });
    assert.equal(bearer.status, 401, `${method} ${path} bearer`);
    assert.deepEqual(await bearer.json().then(({ error, reason }) => ({ error, reason })), { error: "channel_token_invalid", reason: "channel_token_as_bearer" });
  }
  assert.equal(calls.upstream.length, upstream);
  assert.equal(calls.exchange.length, 0, "a channel token is never sent to the session exchange");
});

test("a renewal on the token's own stream keeps it; EE's refusal or the stream's end forgets it", async (t) => {
  let phase = "renew";
  const channel = (url) => {
    if (url.includes("/events")) {
      const frames = phase === "renew"
        ? `event: ready\ndata: {}\n\nevent: output\nid: 3\ndata: {"data":"${"QQ".repeat(4000)}"}\n\nevent: channel\ndata: {"expiresAt":"${new Date(Date.now() + 60_000).toISOString()}"}\n\n`
        : 'event: end\ndata: {"code":"computer_stopped","message":"The run ended."}\n\n';
      return new Response(frames, { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    return phase === "refuse" ? json({ error: { code: "channel_token_invalid" } }, 401) : json({ acceptedBytes: 1 });
  };
  const { call, calls } = await gatewayWith(t, stubEe({ ttlMs: 1_500, channel }));
  await call("POST", `${base("c-1")}/channel`);
  const stream = await call("GET", events("c-1", A), { key: false });
  const text = await stream.text();
  assert.match(text, /event: channel/);
  assert.ok(text.includes("QQ".repeat(4000)), "output passes through unbuffered and whole");
  await new Promise((resolve) => setTimeout(resolve, 1_600));
  assert.equal((await call("POST", input("c-1", A), { key: false, body: "{}" })).status, 200, "renewed past the mint's expiry");
  phase = "refuse";
  assert.equal((await call("POST", input("c-1", A), { key: false, body: "{}" })).status, 401);
  const upstream = calls.upstream.length;
  const after = await call("POST", input("c-1", A), { key: false, body: "{}" });
  assert.equal((await after.json()).reason, "unknown_channel_token");
  assert.equal(calls.upstream.length, upstream, "forgotten: refused at the gateway");
  // A second token, ended by its stream.
  phase = "renew";
  const { call: call2 } = await gatewayWith(t, stubEe({ token: tokenOf("c"), channel: (url) => (phase = "end", channel(url)) }));
  await call2("POST", `${base("c-3")}/channel`);
  phase = "end";
  await (await call2("GET", events("c-3", tokenOf("c")), { key: false })).text();
  assert.equal((await (await call2("POST", input("c-3", tokenOf("c")), { key: false, body: "{}" })).json()).reason, "unknown_channel_token");
});

test("a channel request is limited by the minting key's own bucket, at a shell's pace (the channel class)", async (t) => {
  const { call } = await gatewayWith(t, stubEe());
  await call("POST", `${base("c-1")}/channel`);
  // Typing: 60 inputs in about 3 s (20 a second, a brisk typist) all pass; the write class
  // (5/s, burst 10) would have refused most of them and cut the shell.
  const typed = [];
  for (let i = 0; i < 60; i += 1) {
    typed.push((await call("POST", input("c-1", A), { key: false, body: "{}" })).status);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.deepEqual([...new Set(typed)], [200], typed.join(","));
  // A flood is still limited, by the minting key's own user and workspace bucket.
  const flood = await Promise.all(Array.from({ length: 160 }, () => call("POST", input("c-1", A), { key: false, body: "{}" }).then((r) => r.status)));
  assert.ok(flood.includes(429), "the channel class (40/s, burst 120) refuses a flood");
});

test("no token value reaches a gateway log line, and both default sinks redact one", async (t) => {
  const { call, lines } = await gatewayWith(t, stubEe());
  await call("POST", `${base("c-1")}/channel`);
  await call("GET", events("c-1", A), { key: false });
  await call("GET", events("c-2", A), { key: false });
  await call("GET", `/workspace/usage?token=${A}`, { key: false });
  await call("GET", events("c-1", A), { key: false, headers: { authorization: "Bearer x" } });
  assert.equal(lines.length, 8, "five access lines and three refusal lines");
  assert.equal(lines.filter((line) => line.includes(A) || /token=[^\[]/.test(line)).length, 0);
  const written = [];
  const write = process.stderr.write;
  process.stderr.write = (chunk) => { written.push(String(chunk)); return true; };
  try {
    stderrSink(JSON.stringify({ path: `/x?token=${A}`, raw: A }));
  } finally {
    process.stderr.write = write;
  }
  assert.equal(written.join("").includes(A), false);
  assert.match(redactSecrets(`GET /a?cursor=1&token=${A}&b=2`), /cursor=1&token=\[redacted\]&b=2/);
});

test("the bindings store keeps digests only, honours expiry and renewal, and stays bounded", () => {
  let now = 1_000_000;
  const store = createChannelBindings({ now: () => now, maxEntries: 3 });
  const at = (ms) => new Date(ms).toISOString();
  assert.equal(store.remember({ token: A, prefix: "/p/{id}", bound: { id: "c-1" }, expiresAt: at(now + 60_000), claims: { sub: "u", secret: "no" } }), true);
  assert.equal(store.remember({ token: tokenOf("x"), prefix: "/p/{id}", bound: { id: "c-1" }, expiresAt: at(now - 1), claims: {} }), false);
  assert.deepEqual(store.find(A).claims, { sub: "u" });
  assert.equal(JSON.stringify([...Object.values(store.find(A))]).includes(A), false);
  now += 61_000;
  assert.deepEqual(store.find(A), { expired: true });
  assert.equal(store.find(A), undefined);
  store.remember({ token: A, prefix: "/p/{id}", bound: { id: "c-1" }, expiresAt: at(now + 1_000), claims: {} });
  store.renew(A, at(now + 60_000));
  now += 30_000;
  assert.ok(store.find(A));
  store.forget(A);
  assert.equal(store.find(A), undefined);
  for (const fill of ["d", "e", "f", "g"]) store.remember({ token: tokenOf(fill), prefix: "/p/{id}", bound: { id: "c" }, expiresAt: at(now + 60_000), claims: {} });
  assert.ok(store.size() <= 3);
  assert.equal(store.find(tokenOf("d")), undefined, "the oldest went first");
});

test("one principal holds at most 8 bindings per computer, as EE does; the oldest goes and nobody else's (X-1 review F-6)", () => {
  let now = 2_000_000;
  const store = createChannelBindings({ now: () => now, maxEntries: 100 });
  const at = (ms) => new Date(ms).toISOString();
  const mint = (fill, sub, computerId) => store.remember({ token: tokenOf(fill), prefix: "/p/{id}", bound: { id: "c-1" }, computerId, expiresAt: at(now + 60_000), claims: { sub, org_id: "o", workspace_id: "w" } });
  // Another person's binding on the same computer, and the same person's on another computer, first.
  assert.equal(mint("Z", "other", "cmp_1"), true);
  assert.equal(mint("Y", "u", "cmp_2"), true);
  const mine = "abcdefghi".split("");
  for (const fill of mine) {
    assert.equal(mint(fill, "u", "cmp_1"), true);
    now += 10;
  }
  assert.equal(store.heldBy({ sub: "u", org_id: "o", workspace_id: "w" }, "cmp_1"), 8);
  assert.equal(store.find(tokenOf("a")), undefined, "the ninth mint retired the oldest, as EE's ninth mint does");
  for (const fill of mine.slice(1)) assert.ok(store.find(tokenOf(fill))?.claims, `binding ${fill} stays`);
  assert.ok(store.find(tokenOf("Z")), "another person's binding on the same computer stays");
  assert.ok(store.find(tokenOf("Y")), "the same person's binding on another computer stays");
  assert.equal(store.size(), 10);
  // Forgetting and expiry leave the per-principal index in step: a freed slot is reused, nothing older is dropped.
  store.forget(tokenOf("b"));
  assert.equal(store.heldBy({ sub: "u", org_id: "o", workspace_id: "w" }, "cmp_1"), 7);
  assert.equal(mint("j", "u", "cmp_1"), true);
  assert.ok(store.find(tokenOf("c")), "a slot freed by forget is used before the oldest is retired");
  now += 61_000;
  assert.deepEqual(store.find(tokenOf("c")), { expired: true });
  assert.equal(store.heldBy({ sub: "u", org_id: "o", workspace_id: "w" }, "cmp_1"), 7);
  for (const fill of "klmnopqr".split("")) mint(fill, "u", "cmp_1");
  assert.equal(store.heldBy({ sub: "u", org_id: "o", workspace_id: "w" }, "cmp_1"), 8);
  assert.ok(store.find(tokenOf("r")) && store.find(tokenOf("k")));
  // A token's value is never part of what an entry holds.
  assert.equal(JSON.stringify(store.find(tokenOf("r"))).includes(tokenOf("r")), false);
});
