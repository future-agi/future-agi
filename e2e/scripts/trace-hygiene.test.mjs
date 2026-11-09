// 708 add.78: the trace scrubber and post-processor (e2e/lib/trace-hygiene/),
// checked by the INDEPENDENT auditor (no stack). Every planted value is
// generated at run time.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  MARKER,
  scrubTraceFile,
  scrubTraceSet,
  sweepTraceArchives,
} from "../lib/trace-hygiene/archive.mjs";
import {
  EXEMPT_ROUTES,
  SENSITIVE_ROUTES,
  sensitiveUrl,
} from "../lib/trace-hygiene/routes.mjs";
import { readZip, writeZip } from "../lib/trace-hygiene/zip.mjs";
import { auditBuffer } from "./evidence-audit/audit.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const hex = (n) => randomBytes(n).toString("hex");
const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = () =>
  `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ user_id: hex(8), exp: 9e9 })}.${randomBytes(32).toString("base64url")}`;
const pw = () => `Pw-${hex(5)}!${hex(3)}`;
const jsonl = (events) =>
  events.map((e) => JSON.stringify(e)).join("\n") + "\n";
const until = (make, ok) => {
  for (;;) {
    const v = make();
    if (ok(v)) return v;
  }
};
const opaque = () =>
  until(
    () => randomBytes(32).toString("base64url"),
    (v) =>
      /\d/.test(v) &&
      /[a-z]/.test(v) &&
      /[A-Z]/.test(v) &&
      (v.match(/[-_]/g) ?? []).length <= 3,
  );

/** A synthetic trace with every leak class DSX reported and a few more. */
function fixture() {
  const v = {
    access: jwt(),
    refresh: jwt(),
    password: pw(),
    invitePassword: pw(),
    apiKey: hex(16),
    secretKey: hex(16),
    inviteToken: `${hex(3)}-${hex(16)}`,
    cookie: randomBytes(24).toString("base64url"),
    csrf: hex(16),
    bodyToken: hex(16),
    beacon: opaque(),
    beaconBody: opaque(),
  };
  const uid = Buffer.from(randomUUID()).toString("base64url");
  const sha = () => `${hex(20)}.json`;
  const s = {
    req: sha(),
    resp: sha(),
    keys: sha(),
    info: sha(),
    att: hex(20),
    beacon: `${hex(20)}.txt`,
  };
  const email = `e2e-${hex(3)}@futureagi.com`;
  const context = [
    {
      type: "context-options",
      options: {
        extraHTTPHeaders: { Authorization: `Bearer ${v.access}` },
        httpCredentials: { username: "u", password: v.password },
      },
    },
    {
      type: "before",
      callId: "call@1",
      class: "BrowserContext",
      method: "addInitScript",
      params: {
        source: `(s => localStorage.setItem('accessToken', s.access))(${JSON.stringify({ access: v.access, refresh: v.refresh })})`,
      },
    },
    {
      type: "before",
      callId: "call@2",
      class: "APIRequestContext",
      method: "fetch",
      title: `POST "/accounts/accept-invitation/${uid}/${v.inviteToken}/"`,
      params: {
        url: `http://localhost:8100/accounts/accept-invitation/${uid}/${v.inviteToken}/`,
        method: "POST",
        headers: [{ name: "Authorization", value: `Bearer ${v.access}` }],
        jsonData: JSON.stringify({
          new_password: v.invitePassword,
          repeat_password: v.invitePassword,
        }),
      },
    },
    {
      type: "before",
      callId: "call@3",
      class: "APIRequestContext",
      method: "fetch",
      params: {
        url: "http://localhost:8100/sdk/api/v1/get-evals/",
        headers: [
          { name: "X-Api-Key", value: v.apiKey },
          { name: "X-Secret-Key", value: v.secretKey },
        ],
      },
    },
    {
      type: "before",
      callId: "call@4",
      class: "Frame",
      method: "fill",
      title: `Fill "${v.password}"`,
      params: { selector: "internal:label=/password/i", value: v.password },
    },
    {
      type: "frame-snapshot",
      snapshot: {
        callId: "call@4",
        snapshotName: "input@call@4",
        frameUrl: `http://localhost:3100/auth/jwt/invitation/accept/${uid}/${v.inviteToken}`,
        html: [
          "HTML",
          {},
          [
            "BODY",
            {},
            [
              "INPUT",
              {
                type: "password",
                __playwright_value_: v.password,
                __playwright_target__: "",
              },
            ],
            ["INPUT", { type: "email", __playwright_value_: email }],
          ],
        ],
      },
    },
    { type: "console", text: `token=${v.access}` },
  ];
  const network = [
    {
      type: "resource-snapshot",
      snapshot: {
        request: {
          url: "http://localhost:8100/accounts/token/",
          method: "POST",
          headers: [{ name: "Content-Type", value: "application/json" }],
          cookies: [],
          postData: { mimeType: "application/json", _sha1: s.req },
        },
        response: {
          status: 200,
          headers: [
            { name: "Set-Cookie", value: `sessionid=${v.cookie}; HttpOnly` },
          ],
          cookies: [{ name: "sessionid", value: v.cookie }],
          content: { mimeType: "application/json", _sha1: s.resp },
        },
      },
    },
    {
      type: "resource-snapshot",
      snapshot: {
        request: {
          url: "http://localhost:8100/accounts/keys/",
          method: "GET",
          headers: [{ name: "Authorization", value: `Bearer ${v.access}` }],
        },
        response: {
          status: 200,
          headers: [],
          content: { mimeType: "application/json", _sha1: s.keys },
        },
      },
    },
    {
      type: "resource-snapshot",
      snapshot: {
        request: {
          url: "http://localhost:8100/accounts/user-info/",
          method: "GET",
          headers: [{ name: "Authorization", value: `Bearer ${v.access}` }],
          cookies: [{ name: "csrftoken", value: v.csrf }],
        },
        response: {
          status: 200,
          headers: [],
          content: { mimeType: "application/json", _sha1: s.info },
        },
      },
    },
    // A third-party beacon (analytics): its query and body are never evidence.
    {
      type: "resource-snapshot",
      snapshot: {
        request: {
          url: `https://beacon.example.test/e/?s=${v.beacon}&v=2`,
          method: "POST",
          headers: [],
          queryString: [{ name: "s", value: v.beacon }],
          postData: { mimeType: "text/plain", _sha1: s.beacon },
        },
        response: {
          status: 0,
          headers: [],
          content: { size: -1, mimeType: "x-unknown" },
        },
      },
    },
  ];
  const runner = [
    {
      type: "before",
      callId: "s1",
      method: "pw:api",
      title: `Fill "${v.password}" getByLabel(/password/i)`,
      params: { selector: "internal:label=/password/i", value: v.password },
    },
    {
      type: "before",
      callId: "s2",
      method: "pw:api",
      title: 'POST "/accounts/token/"',
      params: {
        url: "http://localhost:8100/accounts/token/",
        postData: Buffer.from(
          JSON.stringify({ email, password: v.password }),
        ).toString("base64"),
      },
    },
    {
      type: "before",
      callId: "s3",
      method: "pw:api",
      params: {
        url: "http://localhost:8100/accounts/workspaces/",
        jsonData: JSON.stringify({ name: "ws", token: v.bodyToken }),
      },
    },
    { type: "stdout", text: `Authorization: Bearer ${v.access}\n` },
    {
      type: "after",
      callId: "s3",
      attachments: [
        {
          name: "scope-api-receipt",
          contentType: "application/json",
          sha1: s.att,
        },
      ],
    },
    { type: "error", message: `expected ${v.access} to be falsy` },
  ];
  const entries = [
    { name: "test.trace", data: Buffer.from(jsonl(runner)) },
    { name: "0-trace.trace", data: Buffer.from(jsonl(context)) },
    { name: "0-trace.network", data: Buffer.from(jsonl(network)) },
    {
      name: "0-trace.stacks",
      data: Buffer.from(
        JSON.stringify({ files: ["/w/e2e/harness/x.spec.ts"], stacks: [] }),
      ),
    },
    {
      name: `resources/${s.req}`,
      data: Buffer.from(
        JSON.stringify({ email, password: v.password, remember_me: true }),
      ),
    },
    {
      name: `resources/${s.resp}`,
      data: Buffer.from(
        JSON.stringify({ access: v.access, refresh: v.refresh }),
      ),
    },
    {
      name: `resources/${s.keys}`,
      data: Buffer.from(
        JSON.stringify({
          data: { api_key: v.apiKey, secret_key: v.secretKey },
        }),
      ),
    },
    {
      name: `resources/${s.info}`,
      data: Buffer.from(
        JSON.stringify({
          id: randomUUID(),
          email,
          invite_link: `None/auth/jwt/invitation/accept/${uid}/${v.inviteToken}`,
        }),
      ),
    },
    {
      name: `resources/${s.beacon}`,
      data: Buffer.from(JSON.stringify({ distinct: v.beaconBody })),
    },
    {
      name: `resources/${s.att}`,
      data: Buffer.from(
        JSON.stringify({
          requests: [{ path: "/accounts/user-info/" }],
          password: v.invitePassword,
        }),
      ),
    },
    { name: `resources/page@${hex(16)}-1.jpeg`, data: randomBytes(2048) },
  ];
  return { v, s, email, zip: writeZip(entries) };
}

const allText = (buf) =>
  readZip(buf)
    .map((e) => e.data.toString("latin1"))
    .join("\n");

test("zip: what writeZip writes, readZip reads back", () => {
  const entries = [
    { name: "a.json", data: Buffer.from("{}") },
    { name: "resources/b.jpeg", data: randomBytes(64) },
  ];
  assert.deepEqual(
    readZip(writeZip(entries)).map((e) => [e.name, e.data.toString("hex")]),
    entries.map((e) => [e.name, e.data.toString("hex")]),
  );
});

test("scrubbed: the auditor finds nothing, sensitive bodies are gone, the rest is kept", () => {
  const { v, s, email, zip } = fixture();
  const before = auditBuffer(zip, "trace.zip", { registry: Object.values(v) });
  assert.ok(
    before.hits.length >= 10,
    "the unscrubbed fixture must be refused (red control)",
  );
  const {
    buffers: [out],
    stats,
  } = scrubTraceSet([zip]);
  const report = auditBuffer(out, "trace.zip", { registry: Object.values(v) });
  assert.deepEqual(report.problems, []);
  assert.deepEqual(
    report.hits.map((h) => `${h.kind} ${h.entry} ${h.name}`),
    [],
  );
  const names = readZip(out).map((e) => e.name);
  for (const gone of [s.req, s.resp, s.keys, s.beacon])
    assert.equal(names.includes(`resources/${gone}`), false);
  assert.ok(names.includes(`resources/${s.info}`) && names.includes(MARKER));
  const text = allText(out);
  for (const value of Object.values(v))
    assert.equal(text.includes(value), false);
  // Evidence stays useful: identities, routes and statuses survive.
  assert.ok(
    text.includes(email) &&
      text.includes("/sdk/api/v1/get-evals/") &&
      text.includes("/accounts/keys/"),
  );
  assert.ok(
    stats.sensitiveEndpoints >= 2 &&
      stats.droppedResources >= 3 &&
      stats.passwordValues >= 1,
  );
});

test("runner events (the worker's test.trace) are scrubbed in place with the chunks", () => {
  const { v, zip } = fixture();
  const events = [
    {
      type: "before",
      callId: "s9",
      method: "pw:api",
      title: `Fill "${v.password}"`,
      params: { selector: "#password", value: v.password },
    },
    {
      type: "after",
      callId: "s9",
      attachments: [
        {
          name: "a",
          contentType: "application/json",
          base64: Buffer.from(
            JSON.stringify({ secret_key: v.secretKey }),
          ).toString("base64"),
        },
      ],
    },
  ];
  scrubTraceSet([zip], events);
  const text = JSON.stringify(events);
  assert.equal(text.includes(v.password), false);
  assert.equal(
    Buffer.from(events[1].attachments[0].base64, "base64")
      .toString()
      .includes(v.secretKey),
    false,
  );
  assert.equal(auditBuffer(Buffer.from(text), "test.trace").refused, false);
});

test("negative control: the auditor refuses a value the scrubber has no rule for", () => {
  const {
    buffers: [clean],
  } = scrubTraceSet([fixture().zip]);
  const grant = until(
    () => hex(20),
    (h) => /\d/.test(h) && /[a-f]/.test(h),
  );
  const handoff = opaque();
  const plantSha = `${hex(20)}.json`;
  const entries = readZip(clean);
  const net = entries.find((e) => e.name === "0-trace.network");
  net.data = Buffer.concat([
    net.data,
    Buffer.from(
      jsonl([
        {
          type: "resource-snapshot",
          snapshot: {
            request: {
              url: "http://localhost:8100/model-hub/handoff/",
              headers: [{ name: "X-Unlisted-Grant", value: grant }],
            },
            response: {
              content: { mimeType: "application/json", _sha1: plantSha },
            },
          },
        },
      ]),
    ),
  ]);
  entries.push({
    name: `resources/${plantSha}`,
    data: Buffer.from(JSON.stringify({ handoff })),
  });
  const planted = writeZip(entries.filter((e) => e.name !== MARKER));
  const {
    buffers: [rescrubbed],
  } = scrubTraceSet([planted]);
  const text = allText(rescrubbed);
  assert.ok(
    text.includes(grant) && text.includes(handoff),
    "the scrubber must not know these (control premise)",
  );
  const report = auditBuffer(rescrubbed, "trace.zip");
  assert.equal(report.refused, true);
  assert.ok(
    report.hits.some(
      (h) => h.kind === "entropy-hex" && h.name === "X-Unlisted-Grant",
    ),
  );
  assert.ok(
    report.hits.some((h) => h.kind === "entropy-b64" && h.name === "handoff"),
  );
  const reportText = JSON.stringify(report);
  assert.equal(
    reportText.includes(grant) || reportText.includes(handoff),
    false,
  );
});

test("post-processor: idempotent in place, CLI over a tree, corrupt archives reported", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "trace-scrub-"));
  const file = path.join(dir, "trace.zip");
  writeFileSync(file, fixture().zip);
  assert.equal(scrubTraceFile(file), true);
  const once = readFileSync(file);
  assert.equal(scrubTraceFile(file), false, "already scrubbed: skipped");
  scrubTraceFile(file, { force: true });
  const strip = (b) =>
    readZip(b)
      .filter((e) => e.name !== MARKER)
      .map((e) => e.data.toString("hex"));
  assert.deepEqual(strip(readFileSync(file)), strip(once));
  writeFileSync(path.join(dir, "second.zip"), fixture().zip);
  writeFileSync(path.join(dir, "broken.zip"), fixture().zip.subarray(0, 200));
  const r = spawnSync(
    process.execPath,
    [path.join(here, "trace-scrub.mjs"), dir],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 1, r.stdout);
  assert.match(r.stdout, /scrubbed=1 failed=1/);
  assert.deepEqual(
    sweepTraceArchives([dir]).failed.map((f) => path.basename(f)),
    ["broken.zip"],
  );
});

test("every flow imports `test` through the shared fixtures (capture-time hook)", () => {
  const flows = path.join(repo, "e2e/flows");
  const specs = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      if (e.isDirectory()) walk(path.join(d, e.name));
      else if (e.name.endsWith(".spec.ts")) specs.push(path.join(d, e.name));
    }
  };
  walk(flows);
  assert.ok(specs.length > 50);
  const direct = specs
    .filter((f) =>
      /import\s*\{[^}]*\btest\b[^}]*\}\s*from\s*["']@playwright\/test["']/.test(
        readFileSync(f, "utf8"),
      ),
    )
    .map((f) => path.relative(repo, f));
  assert.deepEqual(direct, []);
});

// --- SENSITIVE_ROUTES pinned to the product's real routes ---------------------

/** Python source without # comments (string literals kept): commented-out routes are not routes. */
function stripComments(src) {
  let out = "";
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'") {
      let j = i + 1;
      for (; j < src.length && src[j] !== c; j++) if (src[j] === "\\") j++;
      out += src.slice(i, j + 1);
      i = j;
    } else if (c === "#") {
      while (i < src.length && src[i] !== "\n") i++;
      out += "\n";
    } else out += c;
  }
  return out;
}

function calls(src) {
  const out = [];
  const re = /\b(re_path|path)\(/g;
  for (let m = re.exec(src); m; m = re.exec(src)) {
    let i = re.lastIndex;
    let depth = 1;
    while (i < src.length && depth) {
      const c = src[i];
      if (c === '"' || c === "'") {
        for (i++; i < src.length && src[i] !== c; i++) if (src[i] === "\\") i++;
      } else if (c === "#") while (i < src.length && src[i] !== "\n") i++;
      else if (c === "(") depth++;
      else if (c === ")") depth--;
      i++;
    }
    out.push({ kind: m[1], body: src.slice(re.lastIndex, i - 1) });
    re.lastIndex = i;
  }
  return out;
}
const firstString = (body) =>
  /^\s*[rRbBuU]?(["'])((?:(?!\1)[^\\]|\\.)*)\1/.exec(body)?.[2];
const registrations = (src, router) =>
  [
    ...src.matchAll(
      new RegExp(`\\b${router}\\.register\\(\\s*[rR]?["']([^"']+)["']`, "g"),
    ),
  ].map((m) => `${m[1]}/`);

function productRoutes(mod, prefix) {
  if (mod.startsWith("ee.cloud")) return []; // private: never read
  const file = path.join(repo, "futureagi", `${mod.replaceAll(".", "/")}.py`);
  if (!existsSync(file)) return [];
  const src = stripComments(readFileSync(file, "utf8"));
  const out = [];
  for (const { kind, body } of calls(src)) {
    let route = firstString(body);
    if (route === undefined) continue;
    if (kind === "re_path") route = route.replace(/^\^/, "").replace(/\$$/, "");
    const at = body.indexOf("include(");
    if (at < 0) {
      out.push(prefix + route);
      continue;
    }
    const rest = body.slice(at + 8);
    const router = /^\s*(\w+)\.urls/.exec(rest);
    const dotted = /["']([a-z_]\w*(?:\.\w+)+)["']/.exec(rest);
    if (/^\s*\[/.test(rest))
      for (const c of calls(rest))
        out.push(prefix + route + (firstString(c.body) ?? ""));
    else if (router)
      for (const r of registrations(src, router[1]))
        out.push(prefix + route + r);
    else if (dotted) out.push(...productRoutes(dotted[1], prefix + route));
  }
  for (const m of src.matchAll(/(?:\*|\+\s*)(\w+)\.urls/g))
    for (const r of registrations(src, m[1])) out.push(prefix + r);
  return out;
}
const sample = (route) => route.replace(/<[^>]+>|\{[^}]+\}|:\w+/g, "x");

test("SENSITIVE_ROUTES are pinned to real product routes, and cover every credential route", () => {
  const routes = new Set(productRoutes("tfc.urls", "/"));
  assert.ok(routes.size > 200, `resolved ${routes.size} product routes`);
  for (const entry of SENSITIVE_ROUTES) {
    for (const r of entry.routes ?? []) {
      assert.ok(routes.has(r), `${r} is not a product route`);
      assert.ok(entry.re.test(sample(r)), `${entry.re} does not match ${r}`);
    }
  }
  const words =
    /pass|token|invit|reset|verif|secret|otp|2fa|mfa|login|logout|signup|oauth|saml|sso|credential|recover|totp|activate|callback|key|cred|auth/i;
  const uncovered = [...routes].filter(
    (r) => words.test(r) && !sensitiveUrl(sample(r)) && !(r in EXEMPT_ROUTES),
  );
  assert.deepEqual(
    uncovered,
    [],
    "add each to SENSITIVE_ROUTES, or to EXEMPT_ROUTES with its reason",
  );
  for (const r of Object.keys(EXEMPT_ROUTES))
    assert.ok(routes.has(r), `exempt ${r} is not a product route`);
});

test("SENSITIVE_ROUTES gateway and frontend entries exist where they say", () => {
  const server = readFileSync(
    path.join(repo, "agentcc-gateway/internal/server/server.go"),
    "utf8",
  );
  const auth = readFileSync(
    path.join(repo, "frontend/src/routes/sections/auth.jsx"),
    "utf8",
  );
  const index = readFileSync(
    path.join(repo, "frontend/src/routes/sections/index.jsx"),
    "utf8",
  );
  for (const entry of SENSITIVE_ROUTES) {
    for (const r of entry.gateway ?? []) {
      assert.ok(server.includes(`"${r}"`), `gateway route ${r}`);
      assert.ok(entry.re.test(sample(r)), `${entry.re} vs ${r}`);
    }
    for (const r of entry.frontend ?? []) {
      const inAuth = auth.includes(`path: "${r}"`);
      assert.ok(
        inAuth || index.includes(`path: "${r}"`),
        `frontend route ${r}`,
      );
      assert.ok(
        entry.re.test(sample(inAuth ? `/auth/jwt/${r}` : r)),
        `${entry.re} vs ${r}`,
      );
    }
  }
  assert.equal(
    sensitiveUrl("http://localhost:8100/accounts/user-info/"),
    false,
  );
  assert.equal(
    sensitiveUrl("http://localhost:8100/accounts/accept-invitation/a/b/?x=1"),
    true,
  );
});

test("the scrubber modules import nothing from the auditor", () => {
  for (const f of ["archive.mjs", "scrub.mjs", "routes.mjs", "zip.mjs"]) {
    const src = readFileSync(
      path.join(repo, "e2e/lib/trace-hygiene", f),
      "utf8",
    );
    assert.equal(
      /evidence-audit/.test(src.replace(/^\/\/.*$/gm, "")),
      false,
      f,
    );
  }
});

// --- r2 (708 add.78): credential NAMES by containment, in the scrubber -------

const shortValue = (n) => `H1!${hex(12)}`.slice(0, n); // below every entropy threshold
const R2_NAMES = [
  "loginPasswordValue",
  "userPassword",
  "new_password_2",
  "PASSWORD_CONFIRM",
  "x-session-token",
  "accessTokenValue",
  "clientSecretValue",
  "MyApiKey",
  "stripe-secret",
  "otpCode",
  "pwd2",
  "user_pass",
  "csrfmiddlewaretoken",
  "X-CSRFToken",
  "refreshTokenString",
  "jwtValue",
  "passphraseText",
  "githubWebhookSignature",
  "authHeader",
];

test("r2 (2) scrubber: containment names with short values are redacted in every form", async () => {
  const { newContext, scrubText } = await import(
    "../lib/trace-hygiene/scrub.mjs"
  );
  for (const name of R2_NAMES)
    for (const n of [6, 8, 12, 18]) {
      const value = shortValue(n);
      const forms = {
        json: JSON.stringify({ outer: { [name]: value } }),
        record: JSON.stringify({ headers: [{ name, value }] }),
        quoted: `saved ${name}: "${value}" ok`,
        escaped: `body {\\"${name}\\":\\"${value}\\"}`,
        form: `GET /cb?x=1&${name}=${value}`,
        spaced: `retry with ${name} = ${value} next`,
        nested: `note: "see ${name}='${value}' there"`,
      };
      for (const [form, text] of Object.entries(forms))
        assert.equal(
          scrubText(text, newContext()).includes(value),
          false,
          `${name} (${form}, ${n} chars)`,
        );
    }
  const header = scrubText(
    `X-Hub-Signature: sha1=${hex(20)}\nX-Request-Id: req-1\n`,
    newContext(),
  );
  assert.ok(
    header.includes("X-Request-Id: req-1") && !/sha1=[0-9a-f]/.test(header),
  );
});

test("r2 (2) the scrubber removes at least every credential name the auditor flags", async () => {
  const { newContext, scrubText } = await import(
    "../lib/trace-hygiene/scrub.mjs"
  );
  const names = [
    ...R2_NAMES,
    "token_type",
    "api_key_id",
    "apiKeyName",
    "is_password_set",
    "hasApiKey",
    "passwordPolicy",
    "max_tokens",
    "prompt_tokens_count",
    "tokenizer",
    "tokenizerName",
    "snapshotPath",
    "footprint",
    "author",
    "accessibilityLabel",
    "token_endpoint",
    "session_id",
    "sessionId",
    "sessionid",
    "auth_key_owner",
    "lastRefresh",
    "workspace_access",
    "access",
    "refresh",
    "jwt_refresh",
    "access-control-allow-origin",
    "invite_link",
    "invite_link_id",
    "password_reset_url",
    "resetPasswordUrl",
    "secretKeyRef",
    "private_key_pem",
    "x-amz-security-token",
    "X-Custom-Auth",
    "Proxy-Authorization",
    "set-cookie",
    "recovery_codes",
    "totp_secret",
    "magicLinkUrl",
    "password_hash",
    "id_token",
    "idempotency_key",
    "public_key",
    "passkey",
    "oauth",
  ];
  const v = "v2-a8f3c1";
  for (const name of names) {
    const raw = JSON.stringify({ [name]: v });
    const flagged = auditBuffer(Buffer.from(raw), "r.json").hits.some(
      (h) => h.kind === "credential-field",
    );
    const scrubbed = !scrubText(raw, newContext()).includes(v);
    assert.ok(
      !flagged || scrubbed,
      `${name}: the auditor flags it, the scrubber keeps it`,
    );
  }
});

test("r2 (2) a trace resource under a containment name is scrubbed, and the auditor is the red control", () => {
  const value = shortValue(12);
  const sha = `${hex(20)}.json`;
  const zip = writeZip([
    {
      name: "0-trace.network",
      data: Buffer.from(
        jsonl([
          {
            type: "resource-snapshot",
            snapshot: {
              request: {
                url: "http://localhost:8100/model-hub/settings/",
                method: "GET",
                headers: [],
                cookies: [{ name: "sessionid", value: opaque() }],
              },
              response: {
                status: 200,
                headers: [],
                content: { mimeType: "application/json", _sha1: sha },
              },
            },
          },
        ]),
      ),
    },
    {
      name: `resources/${sha}`,
      data: Buffer.from(
        JSON.stringify({ data: { loginPasswordValue: value } }),
      ),
    },
  ]);
  assert.equal(
    auditBuffer(zip, "trace.zip").refused,
    true,
    "red control: the unscrubbed trace is refused",
  );
  const {
    buffers: [out],
  } = scrubTraceSet([zip]);
  const report = auditBuffer(out, "trace.zip", { registry: [value] });
  assert.deepEqual(
    report.hits.map((h) => `${h.kind} ${h.entry} ${h.name}`),
    [],
  );
  const network = readZip(out).find((e) => e.name === "0-trace.network");
  const event = JSON.parse(network.data.toString("utf8").trim());
  assert.ok(
    Array.isArray(event.snapshot.request.cookies),
    "cookie arrays keep their shape",
  );
  assert.equal(event.snapshot.request.cookies[0].value, "[REDACTED]");
});

// r3 (708 add.78): the r2 live run's false positive. A traced GET
// /sdk/api/v1/get-evals/ response carries eval-template Python source as JSON
// strings (line ends as \n escapes); the scrubber rewrites `tokens = <x>` to
// `tokens = [REDACTED]`, and the r2 auditor read `[REDACTED]\n` as a value
// under a credential name, refusing every trace that loaded the evals. Red at
// ae14c2defe.
test("r3 (2) a traced get-evals response with eval-template code is scrubbed and audits clean", () => {
  const sha = `${hex(20)}.json`;
  const code = (assignment) =>
    [
      "def evaluate(input, output, expected=None, **kwargs):",
      '    text = str(output.get("text", output or "")).lower().strip()',
      `    ${assignment}`,
      '    if not tokens: return {"score": 0.0, "reason": "no keywords"}',
      "    hits = [t for t in tokens if t in text]",
      '    return {"score": len(hits) / len(tokens), "reason": ", ".join(hits)}',
    ].join("\n");
  const body = {
    status: true,
    result: {
      evals: [
        "tokens = [keywords]",
        "tokens = text.split()",
        "tokens = []",
      ].map((assignment, i) => ({
        id: randomUUID(),
        name: `keyword_presence_${i}`,
        eval_type: "code",
        code: code(assignment),
        config: { required_keys: ["output"], output: "score" },
      })),
    },
  };
  const zip = writeZip([
    {
      name: "0-trace.network",
      data: Buffer.from(
        jsonl([
          {
            type: "resource-snapshot",
            snapshot: {
              request: {
                url: "http://localhost:8100/sdk/api/v1/get-evals/",
                method: "GET",
                headers: [],
              },
              response: {
                status: 200,
                headers: [],
                content: { mimeType: "application/json", _sha1: sha },
              },
            },
          },
        ]),
      ),
    },
    { name: `resources/${sha}`, data: Buffer.from(JSON.stringify(body)) },
  ]);
  const {
    buffers: [out],
  } = scrubTraceSet([zip]);
  const resource = readZip(out).find((e) => e.name === `resources/${sha}`);
  assert.ok(resource, "the response body is kept (not a sensitive route)");
  assert.match(
    resource.data.toString("utf8"),
    /tokens = \[REDACTED\]\\n {4}if not tokens/,
    "premise: the scrubber rewrote the assignment as in the r2 live trace",
  );
  const report = auditBuffer(out, "trace.zip");
  assert.deepEqual(
    report.hits.map((h) => `${h.kind} ${h.entry} ${h.name}`),
    [],
  );
  assert.deepEqual(report.problems, []);
  assert.deepEqual(
    auditBuffer(zip, "trace.zip").hits.map((h) => `${h.kind} ${h.name}`),
    [],
    "the unscrubbed response is clean too",
  );
});
