// Run only on the disposable cloud stack. Every request target is loopback.
import assert from "node:assert/strict";
import { request as httpsRequest } from "node:https";
import { request as httpRequest } from "node:http";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
const ca = readFileSync(process.env.WALK_MOCK_CA);
async function call(port, path, body, headers = {}, secure = true) {
  const payload =
    body === undefined
      ? undefined
      : typeof body === "string"
        ? body
        : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = (secure ? httpsRequest : httpRequest)(
      {
        hostname: "127.0.0.1",
        port,
        path,
        ca,
        method: payload === undefined ? "GET" : "POST",
        headers: {
          ...(payload !== undefined
            ? {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(payload),
              }
            : {}),
          ...headers,
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => (raw += c));
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            headers: res.headers,
            raw,
            body: res.headers["content-type"]?.includes("application/json")
              ? JSON.parse(raw)
              : undefined,
          }),
        );
      },
    );
    req.setTimeout(10000, () => req.destroy(new Error("smoke timeout")));
    req.on("error", reject);
    req.end(payload);
  });
}
const form = (port, path, values) =>
  call(port, path, new URLSearchParams(values).toString(), {
    "Content-Type": "application/x-www-form-urlencoded",
  });
const healthy = await Promise.all(
  [
    [28082, "/__health", false],
    [28443, "/__health", true],
    [28084, "/__health", false],
    [28085, "/__health", false],
    [28086, "/__health", false],
  ].map(
    async ([port, path, tls]) =>
      (await call(port, path, undefined, {}, tls)).status,
  ),
);
assert.deepEqual(healthy, [200, 200, 200, 200, 200]);
for (const [path, body] of [
  ["/v1/models", undefined],
  ["/v1/chat/completions", { stream: false }],
  ["/v1/chat/completions", { stream: true }],
  ["/v1/embeddings", { input: "fixture" }],
  ["/model/v1/models", undefined],
  ["/model/v1/embed", { input_type: "text", text: "fixture" }],
  ["/v1/messages", { model: "claude-fixture" }],
  ["/v1/messages", { model: "claude-fixture", stream: true }],
  ["/v1beta/models/gemini-fixture:generateContent", {}],
  ["/v1beta/models/gemini-fixture:streamGenerateContent", {}],
  ["/v1beta/models/gemini-image:generateContent", {}],
  ["/v1beta/models/lyria-clip:predict", {}],
  ["/v1beta/models/lyria-pro:predict", {}],
  [
    "/s/smoke-router/cost=2,served=served-fixture/v1/chat/completions",
    { stream: true },
  ],
  [
    "/s/smoke-thinking/thinking=1/v1beta/models/gemini-fixture:streamGenerateContent",
    {},
  ],
  ["/s/smoke-image/image=1/v1/chat/completions", {}],
])
  assert.equal((await call(28082, path, body, {}, false)).status, 200, path);
for (const mode of ["absent", "input", "output", "zero"])
  assert.equal(
    (
      await call(
        28082,
        `/s/presence-${mode}/usage=${mode}/v1/messages`,
        { stream: true },
        {},
        false,
      )
    ).status,
    200,
  );
assert.equal(
  (
    await call(
      28082,
      "/s/smoke-fail/failures=1/v1/chat/completions",
      {},
      {},
      false,
    )
  ).status,
  503,
);
assert.equal(
  (
    await call(
      28082,
      "/s/smoke-fail/failures=1/v1/chat/completions",
      {},
      {},
      false,
    )
  ).status,
  200,
);
assert.equal(
  (await call(28082, "/__requests/smoke-fail", undefined, {}, false)).body
    .requests.length,
  2,
);

const registration = await call(28443, "/register", {
  redirect_uris: ["http://localhost:9999/callback"],
  token_endpoint_auth_method: "none",
  grant_types: ["authorization_code", "refresh_token"],
});
assert.equal(registration.status, 201);
const client = registration.body.client_id,
  verifier = "v".repeat(48),
  challenge = createHash("sha256").update(verifier).digest("base64url");
const meta = (await call(28443, "/.well-known/oauth-authorization-server"))
  .body;
assert.equal(
  new URL(meta.registration_endpoint).hostname,
  "mcp.e2e-public.test",
);
assert.equal(
  (await call(28443, "/.well-known/oauth-protected-resource/mcp")).status,
  200,
);
const auth = {
  client_id: client,
  redirect_uri: "http://localhost:9999/callback",
  response_type: "code",
  resource: "https://mcp.e2e-public.test:8443/mcp",
  scope: "mcp",
  state: "fixture",
  login_hint: "smoke-member",
  code_challenge: challenge,
  code_challenge_method: "S256",
};
assert.equal(
  (await call(28443, `/authorize?${new URLSearchParams(auth)}`)).status,
  200,
);
const authorized = await form(28443, "/authorize", {
  ...auth,
  decision: "approve",
});
const code = new URL(authorized.headers.location).searchParams.get("code");
const token = await form(28443, "/token", {
  client_id: client,
  grant_type: "authorization_code",
  code,
  redirect_uri: auth.redirect_uri,
  code_verifier: verifier,
});
assert.equal(token.status, 200);
assert.ok(token.body.refresh_token);
const initialized = await call(
  28443,
  "/mcp",
  {
    jsonrpc: "2.0",
    id: 0,
    method: "initialize",
    params: {
      protocolVersion: "2025-11-25",
      capabilities: {},
      clientInfo: { name: "walk-smoke", version: "1" },
    },
  },
  {
    Authorization: `Bearer ${token.body.access_token}`,
    Accept: "application/json, text/event-stream",
  },
);
assert.equal(initialized.status, 200);
assert.ok(initialized.headers["mcp-session-id"]);
const tools = await call(
  28443,
  "/mcp",
  { jsonrpc: "2.0", id: 1, method: "tools/list" },
  {
    Authorization: `Bearer ${token.body.access_token}`,
    Accept: "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
    "Mcp-Session-Id": initialized.headers["mcp-session-id"],
  },
);
assert.equal(tools.status, 200);
assert.equal(
  (await call(28443, "/__control/oauth/lifetime", { access_ttl_seconds: 5 }))
    .status,
  200,
);
assert.equal(
  (
    await form(28443, "/revoke", {
      client_id: client,
      token: token.body.refresh_token,
      token_type_hint: "refresh_token",
    })
  ).status,
  200,
);
assert.equal(
  (await call(28443, "/__control/oauth/received")).body.revocations.length,
  1,
);

for (const provider of ["google", "microsoft"]) {
  const path =
    provider === "google"
      ? "/google/.well-known/openid-configuration"
      : "/microsoft/common/v2.0/.well-known/openid-configuration";
  const metadata = (await call(28445, path)).body;
  const endpoint = (key) => new URL(metadata[key]).pathname;
  const credentials = {
    client_id: `e2e-${provider}-client`,
    client_secret: `e2e-${provider}-secret-not-real`,
  };
  const q = { ...auth, client_id: credentials.client_id };
  delete q.resource;
  assert.equal(
    (
      await call(
        28445,
        `${endpoint("authorization_endpoint")}?${new URLSearchParams(q)}`,
      )
    ).status,
    200,
  );
  const redirect = await form(28445, endpoint("authorization_endpoint"), {
    ...q,
    decision: "approve",
  });
  const issued = await form(28445, endpoint("token_endpoint"), {
    ...credentials,
    grant_type: "authorization_code",
    code: new URL(redirect.headers.location).searchParams.get("code"),
    redirect_uri: q.redirect_uri,
    code_verifier: verifier,
  });
  assert.equal(issued.status, 200);
  assert.equal(
    (
      await call(28445, endpoint("userinfo_endpoint"), undefined, {
        Authorization: `Bearer ${issued.body.access_token}`,
      })
    ).body.sub,
    "smoke-member",
  );
  assert.equal((await call(28445, endpoint("jwks_uri"))).body.keys.length, 1);
  assert.equal(
    (
      await form(28445, endpoint("revocation_endpoint"), {
        ...credentials,
        token: issued.body.access_token,
      })
    ).status,
    200,
  );
}
assert.equal(
  (await call(28086, "/__received", undefined, {}, false)).body.revocations
    .length,
  2,
);

const trace = {
  resourceSpans: [
    {
      scopeSpans: [
        {
          spans: [
            {
              name: "smoke-tool",
              attributes: [
                {
                  key: "tool.input",
                  value: { stringValue: "synthetic content" },
                },
              ],
            },
          ],
        },
      ],
    },
  ],
};
assert.equal((await call(28418, "/v1/traces", trace, {}, false)).status, 200);
assert.equal(
  (await call(28084, "/__received", undefined, {}, false)).body.batches[0]
    .resourceSpans[0].scopeSpans[0].spans[0].name,
  "smoke-tool",
);
assert.equal(
  (await call(28084, "/__failure", { count: 1, status: 503 }, {}, false))
    .status,
  200,
);
assert.equal((await call(28418, "/v1/traces", trace, {}, false)).status, 503);
assert.equal((await call(28418, "/v1/traces", trace, {}, false)).status, 200);
const siem = "e2e-siem-fixture-not-a-secret";
assert.equal(
  (
    await call(
      28446,
      "/services/collector/event?channel=smoke",
      { event: { id: "a" } },
      { Authorization: "Splunk wrong" },
    )
  ).status,
  403,
);
assert.equal(
  (
    await call(
      28446,
      "/services/collector/event?channel=smoke",
      { event: { id: "a" } },
      { Authorization: `Splunk ${siem}` },
    )
  ).body.code,
  0,
);
assert.equal(
  (await call(28085, "/__failure/smoke", { count: 1, status: 503 }, {}, false))
    .status,
  200,
);
assert.equal(
  (
    await call(
      28446,
      "/json/smoke",
      { id: "b" },
      { Authorization: `Bearer ${siem}` },
    )
  ).status,
  503,
);
assert.equal(
  (
    await call(
      28446,
      "/json/smoke",
      { id: "b" },
      { Authorization: `Bearer ${siem}` },
    )
  ).status,
  200,
);
assert.deepEqual(
  (await call(28085, "/__received/smoke", undefined, {}, false)).body.events,
  [{ event: { id: "a" } }, { id: "b" }],
);
console.log(
  "PASS all mock endpoints; TLS verified against the ephemeral fixture CA; targets loopback only",
);
