// The independent evidence auditor's own controls (no stack).
// Every planted value is generated at run time, never written as a literal.
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { deflateRawSync, gzipSync } from "node:zlib";
import { auditBuffer, auditPaths } from "./evidence-audit/audit.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const hex = (n) => randomBytes(n).toString("hex");
const jwt = () =>
  `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ user_id: hex(8), exp: 9e9 })}.${randomBytes(32).toString("base64url")}`;
const pw = () => `Pw-${hex(5)}!${randomBytes(3).toString("base64url")}`;

// A tiny ZIP writer of the test's own (stored or deflated entries).
const CRC = new Int32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});
const crc32 = (buf) => {
  let c = -1;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
};
function makeZip(files, deflate = true) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const data = Buffer.from(content);
    const body = deflate ? deflateRawSync(data) : data;
    const n = Buffer.from(name);
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0);
    head.writeUInt16LE(20, 4);
    head.writeUInt16LE(deflate ? 8 : 0, 8);
    head.writeUInt32LE(crc32(data), 14);
    head.writeUInt32LE(body.length, 18);
    head.writeUInt32LE(data.length, 22);
    head.writeUInt16LE(n.length, 26);
    const cd = Buffer.alloc(46);
    cd.writeUInt32LE(0x02014b50, 0);
    cd.writeUInt16LE(20, 4);
    cd.writeUInt16LE(20, 6);
    cd.writeUInt16LE(deflate ? 8 : 0, 10);
    cd.writeUInt32LE(crc32(data), 16);
    cd.writeUInt32LE(body.length, 20);
    cd.writeUInt32LE(data.length, 24);
    cd.writeUInt16LE(n.length, 28);
    cd.writeUInt32LE(offset, 42);
    locals.push(head, n, body);
    central.push(cd, n);
    offset += head.length + n.length + body.length;
  }
  const dir = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(dir.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, dir, end]);
}

const kinds = (report) => [...new Set(report.hits.map((h) => h.kind))].sort();
const valueFree = (report, values) => {
  const text = JSON.stringify(report);
  for (const v of values)
    assert.equal(text.includes(v), false, "report carries a flagged value");
};

test("positive controls: each credential shape refuses, report is value-free", () => {
  const access = jwt();
  const password = pw();
  const apiKey = hex(16);
  const unlisted = hex(20);
  const cookie = randomBytes(24).toString("base64url");
  const invite = `${hex(3)}-${hex(16)}`;
  const lines = [
    {
      type: "before",
      class: "APIRequestContext",
      method: "fetch",
      params: {
        url: `http://localhost:8100/accounts/accept-invitation/MTIz/${invite}/`,
        headers: [
          { name: "Authorization", value: `Bearer ${access}` },
          { name: "X-Unlisted-Grant", value: unlisted },
        ],
        jsonData: JSON.stringify({
          new_password: password,
          repeat_password: password,
        }),
      },
    },
    {
      type: "resource-snapshot",
      snapshot: {
        request: { cookies: [{ name: "sessionid", value: cookie }] },
        response: {
          content: { text: JSON.stringify({ data: { api_key: apiKey } }) },
        },
      },
    },
    {
      type: "frame-snapshot",
      snapshot: {
        html: [
          "HTML",
          {},
          ["INPUT", { type: "password", __playwright_value_: password }],
        ],
      },
    },
    {
      type: "before",
      class: "Frame",
      method: "fill",
      params: { selector: "internal:label=/password/i", value: password },
    },
  ]
    .map((l) => JSON.stringify(l))
    .join("\n");
  const report = auditBuffer(Buffer.from(lines), "0-trace.trace");
  assert.equal(report.refused, true);
  for (const kind of [
    "jwt",
    "auth-scheme",
    "credential-field",
    "cookie",
    "password-field",
    "url-token",
    "entropy-hex",
  ])
    assert.ok(kinds(report).includes(kind), `missing ${kind}`);
  assert.ok(
    report.hits.some(
      (h) => h.kind === "entropy-hex" && h.name === "X-Unlisted-Grant",
    ),
  );
  valueFree(report, [access, password, apiKey, unlisted, cookie, invite]);
});

test("encodings: base64 postData, percent-encoding, nested ZIP and gzip are opened", () => {
  const password = pw();
  const step = {
    type: "before",
    method: "pw:api",
    params: {
      url: "http://localhost:8100/accounts/token/",
      postData: Buffer.from(JSON.stringify({ password })).toString("base64"),
    },
  };
  assert.ok(
    kinds(
      auditBuffer(Buffer.from(JSON.stringify(step)), "test.trace"),
    ).includes("credential-field"),
  );
  const pct = `GET /cb?password=${encodeURIComponent(password)}&x=1`;
  assert.ok(auditBuffer(Buffer.from(pct), "log.txt").refused);
  const inner = makeZip({ "test.trace": JSON.stringify(step) }, false);
  const outer = makeZip({
    "nested/trace.zip": inner,
    "resources/x.json.gz": gzipSync(JSON.stringify({ password })),
  });
  const report = auditBuffer(outer, "bundle.zip");
  assert.equal(report.refused, true);
  assert.ok(
    report.hits.some((h) => h.entry.startsWith("nested/trace.zip!test.trace")),
  );
  assert.ok(report.hits.some((h) => h.entry.includes("(gunzip)")));
  valueFree(report, [password]);
});

test("registry: a minted value with no credential shape is still found", () => {
  const minted = `plain${hex(4)}word`;
  const blob = Buffer.from(JSON.stringify({ note: `see ${minted} here` }));
  assert.equal(auditBuffer(blob, "a.json").refused, false);
  const report = auditBuffer(blob, "a.json", { registry: [minted] });
  assert.deepEqual(kinds(report), ["minted-value"]);
  valueFree(report, [minted]);
});

test("negative controls: identifiers, digests, masks and placeholders stay clean", () => {
  const sha1 = hex(20);
  const lines = [
    {
      type: "before",
      pageId: `page@${hex(16)}`,
      params: {
        url: "http://localhost:3100/dashboard",
        headers: [
          { name: "Authorization", value: "Bearer [REDACTED]" },
          { name: "X-Request-Id", value: hex(16) },
        ],
      },
    },
    {
      type: "resource-snapshot",
      snapshot: {
        _frameref: `frame@${hex(16)}`,
        response: { content: { _sha1: `${sha1}.json` } },
      },
    },
    {
      type: "after",
      attachments: [{ sha1, name: "trace" }],
      traceId: hex(16),
      spanId: hex(8),
      id: hex(16),
    },
    {
      realHead: hex(20),
      productHead: hex(20),
      mountedConfig: `sha256:${hex(32)}`,
      image: `sha256:${hex(32)}`,
    },
    {
      org: "3f0c1a52-8b4e-4c1d-9a7e-1f2b3c4d5e6f",
      token_type: "Bearer",
      access: "read",
      refresh: "30s",
      password: "[REDACTED]",
      api_key: "sk-...abcd",
      label: "Enter your password",
      entry: `resources/${sha1}`,
    },
  ]
    .map((l) => JSON.stringify(l))
    .join("\n");
  const report = auditBuffer(Buffer.from(lines), "0-trace.network");
  assert.deepEqual(report.hits, []);
  const bundle =
    'const r="invitation/accept/:uuid/:token";h.Authorization="Bearer "+t;img.src="data:image/png;base64,' +
    randomBytes(300).toString("base64") +
    '";';
  assert.deepEqual(
    auditBuffer(Buffer.from(bundle), "resources/abc.js").hits,
    [],
  );
});

test("fail closed: an unreadable archive is refused, not passed", () => {
  const zip = makeZip({ "test.trace": "{}" });
  const report = auditBuffer(zip.subarray(0, zip.length - 30), "trace.zip");
  assert.equal(report.refused, true);
  assert.ok(report.problems.length > 0);
});

test("CLI: globs, exit codes, value-free stdout, quarantine outside the tree", () => {
  const root = mkdtempSync(path.join(tmpdir(), "evidence-audit-"));
  const out = path.join(root, "e2e/walk-results/g1");
  mkdirSync(out, { recursive: true });
  const password = pw();
  writeFileSync(path.join(out, "steps.txt"), "profile=stock walk rc=0\n");
  const cli = path.join(here, "evidence-audit.mjs");
  const run = (args) =>
    spawnSync(process.execPath, [cli, ...args], {
      cwd: root,
      encoding: "utf8",
    });
  let r = run([
    "--glob",
    "e2e/walk-results/**/steps.txt",
    "--report",
    path.join(root, "clean.json"),
  ]);
  assert.equal(r.status, 0, r.stdout);
  assert.match(r.stdout, /clean files=1/);
  writeFileSync(path.join(out, "summary.json"), JSON.stringify({ password }));
  const q = path.join(root, "quarantine");
  r = run([
    "--glob",
    "e2e/walk-results/**/*.json",
    "--report",
    path.join(root, "refused.json"),
    "--quarantine",
    q,
  ]);
  assert.equal(r.status, 1);
  assert.equal(r.stdout.includes(password), false);
  assert.equal(existsSync(path.join(out, "summary.json")), false);
  assert.equal(
    existsSync(path.join(q, "e2e/walk-results/g1/summary.json")),
    true,
  );
  const report = JSON.parse(
    readFileSync(path.join(root, "refused.json"), "utf8"),
  );
  assert.deepEqual(report.quarantined, ["e2e/walk-results/g1/summary.json"]);
  assert.equal(JSON.stringify(report).includes(password), false);
  assert.equal(run([]).status, 2);
});

test("independence: the auditor imports nothing from the trace scrubber", () => {
  const files = [
    "evidence-audit.mjs",
    "evidence-audit/audit.mjs",
    "evidence-audit/detect.mjs",
    "evidence-audit/unzip.mjs",
  ];
  for (const f of files) {
    const src = readFileSync(path.join(here, f), "utf8");
    for (const m of src.matchAll(
      /\bfrom\s+["']([^"']+)["']|import\(\s*["']([^"']+)["']/g,
    )) {
      const spec = m[1] ?? m[2];
      assert.ok(
        spec.startsWith("node:") || spec.startsWith("./"),
        `${f} imports ${spec}`,
      );
      assert.equal(
        /trace-hygiene|lib\//.test(spec),
        false,
        `${f} imports ${spec}`,
      );
    }
  }
  assert.ok(execFileSync(process.execPath, ["--version"]).length > 0);
});

// --- r2: the six verified gaps. Each control is red at bb2af1d6d6. --

const short = (n) => `H1!${hex(12)}`.slice(0, n); // below every entropy threshold
const auditJson = (o, opts) =>
  auditBuffer(Buffer.from(JSON.stringify(o)), "resources/r.json", opts);
const until = (make, ok) => {
  for (;;) {
    const v = make();
    if (ok(v)) return v;
  }
};

// A ZIP writer with every metadata field: per-entry comment, central and local
// extra fields, a local name differing from the central one, data descriptors;
// an archive comment; bytes before the central directory and after the end.
function makeZipX(entries, { comment = "", gap = null, trailing = null } = {}) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const e of entries) {
    const data = Buffer.from(e.data ?? "");
    const body = deflateRawSync(data);
    const name = Buffer.from(e.name);
    const localName = Buffer.from(e.localName ?? e.name);
    const localExtra = Buffer.from(e.localExtra ?? "");
    const extra = Buffer.from(e.extra ?? "");
    const entryComment = Buffer.from(e.comment ?? "");
    const flags = e.descriptor ? 0x8 : 0;
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0);
    head.writeUInt16LE(20, 4);
    head.writeUInt16LE(flags, 6);
    head.writeUInt16LE(8, 8);
    if (!e.descriptor) {
      head.writeUInt32LE(crc32(data), 14);
      head.writeUInt32LE(body.length, 18);
      head.writeUInt32LE(data.length, 22);
    }
    head.writeUInt16LE(localName.length, 26);
    head.writeUInt16LE(localExtra.length, 28);
    const desc = Buffer.alloc(e.descriptor ? 16 : 0);
    if (e.descriptor) {
      desc.writeUInt32LE(0x08074b50, 0);
      desc.writeUInt32LE(crc32(data), 4);
      desc.writeUInt32LE(body.length, 8);
      desc.writeUInt32LE(data.length, 12);
    }
    const cd = Buffer.alloc(46);
    cd.writeUInt32LE(0x02014b50, 0);
    cd.writeUInt16LE(20, 4);
    cd.writeUInt16LE(20, 6);
    cd.writeUInt16LE(flags, 8);
    cd.writeUInt16LE(8, 10);
    cd.writeUInt32LE(crc32(data), 16);
    cd.writeUInt32LE(body.length, 20);
    cd.writeUInt32LE(data.length, 24);
    cd.writeUInt16LE(name.length, 28);
    cd.writeUInt16LE(extra.length, 30);
    cd.writeUInt16LE(entryComment.length, 32);
    cd.writeUInt32LE(offset, 42);
    const record = [head, localName, localExtra, body, desc];
    locals.push(...record);
    central.push(cd, name, extra, entryComment);
    offset += record.reduce((n, b) => n + b.length, 0);
  }
  const between = Buffer.from(gap ?? "");
  const dir = Buffer.concat(central);
  const commentBuf = Buffer.from(comment);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(dir.length, 12);
  end.writeUInt32LE(offset + between.length, 16);
  end.writeUInt16LE(commentBuf.length, 20);
  return Buffer.concat([
    ...locals,
    between,
    dir,
    end,
    commentBuf,
    Buffer.from(trailing ?? ""),
  ]);
}

const CONTAINED_NAMES = [
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

test("r2 (2) credential names are found by containment, with short values, in every pair form", () => {
  for (const name of CONTAINED_NAMES)
    for (const n of [6, 8, 12, 18]) {
      const value = short(n);
      const forms = {
        json: Buffer.from(JSON.stringify({ [name]: value })),
        escaped: Buffer.from(
          JSON.stringify({ body: JSON.stringify({ [name]: value }) }),
        ),
        record: Buffer.from(JSON.stringify({ headers: [{ name, value }] })),
        text: Buffer.from(`saved ${name}=${value} ok\n`),
        query: Buffer.from(
          `GET /cb?${name}=${encodeURIComponent(value)}&x=1\n`,
        ),
      };
      for (const [form, buf] of Object.entries(forms)) {
        const report = auditBuffer(buf, "log.txt");
        assert.equal(report.refused, true, `${name} (${form}, ${n} chars)`);
        valueFree(report, [value]);
      }
    }
});

test("r2 (2) structural exclusions: names that describe a credential, and non-credential values, stay clean", () => {
  const v = "v2-a8f3c1"; // credential-like: only the name can keep it clean
  const report = auditJson({
    token_type: v,
    api_key_id: "3f0c1a52-8b4e-4c1d-9a7e-1f2b3c4d5e6f",
    apiKeyName: v,
    api_key_name: v,
    is_password_set: v,
    hasApiKey: v,
    passwordPolicy: v,
    max_tokens: v,
    prompt_tokens_count: v,
    tokenizer: v,
    tokenizerName: v,
    snapshotPath: "/tmp/run-1/x9",
    footprint: v,
    author: v,
    authorName: v,
    accessibilityLabel: v,
    keyboardLayout: v,
    token_endpoint: "http://127.0.0.1:9/oauth/token",
    authorization_endpoint: "http://127.0.0.1:9/oauth/authorize",
    session_id: "3f0c1a52-8b4e-4c1d-9a7e-1f2b3c4d5e6f",
    sessionId: v,
    auth_key_owner: "e2e-1@futureagi.com",
    input_cost_per_token: "0.0000025",
    lastRefresh: "2026-10-29T14:00:00Z",
    last_refreshed_at: "2026-10-29T14:00:00.123+00:00",
    secretary: v,
  });
  assert.deepEqual(
    report.hits.map((h) => `${h.kind} ${h.name}`),
    [],
  );
});

test("r2 (3) binary payloads: text spans (ASCII, UTF-16LE/BE) get every detector, value-free", () => {
  const token = hex(24);
  const password = short(12);
  const json = (o) => Buffer.from(JSON.stringify(o));
  const utf16be = (s) => {
    const le = Buffer.from(s, "utf16le");
    for (let i = 0; i + 1 < le.length; i += 2)
      [le[i], le[i + 1]] = [le[i + 1], le[i]];
    return le;
  };
  const cases = {
    "nul-then-json-token": Buffer.concat([Buffer.from([0]), json({ token })]),
    "nul-then-json-password": Buffer.concat([
      Buffer.from([0, 1, 2, 0]),
      json({ password }),
    ]),
    "utf16le-json": Buffer.from(JSON.stringify({ password }), "utf16le"),
    "utf16be-json": utf16be(JSON.stringify({ password })),
    "jpeg-with-text": Buffer.concat([
      Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16]),
      Buffer.from(`apiKey=${password};`),
    ]),
  };
  for (const [name, buf] of Object.entries(cases)) {
    const report = auditBuffer(buf, "resources/response.bin");
    assert.equal(report.refused, true, name);
    assert.ok(
      report.hits.some((h) => /spans/.test(h.layer)),
      `${name}: found in a text span`,
    );
    valueFree(report, [token, password]);
  }
  const noise = Buffer.concat([
    Buffer.alloc(64),
    Buffer.from("JFIF image header v1.02"),
    randomBytes(4096),
  ]);
  assert.deepEqual(auditBuffer(noise, "resources/page@1.jpeg").hits, []);
});

test("r2 (4) every decoding bound refuses when reached, never stops silently", () => {
  const token = hex(24);
  const core = JSON.stringify({ token });
  let zip = Buffer.from(core);
  let gz = Buffer.from(core);
  for (let n = 1; n <= 6; n++) {
    zip = makeZip({ payload: zip });
    gz = gzipSync(gz);
    const z = auditBuffer(zip, "evidence.zip");
    const g = auditBuffer(gz, "evidence.gz");
    assert.equal(z.refused, true, `zip nested ${n}`);
    assert.equal(g.refused, true, `gzip nested ${n}`);
    const limited = (r) =>
      r.problems.some((p) => p.problem === "archive-depth-limit");
    assert.equal(
      limited(z),
      n > 4,
      `zip nested ${n}: depth problem only past the bound`,
    );
    assert.equal(limited(g), n > 4, `gzip nested ${n}`);
    if (n <= 4) assert.ok(z.hits.some((h) => h.kind === "credential-field"));
  }
  assert.equal(
    auditBuffer(zip, "evidence.zip", {
      limits: { archiveDepth: 99 },
    }).problems.some((p) => p.problem === "archive-depth-limit"),
    true,
    "limits only tighten",
  );
  const password = short(12);
  let b64 = JSON.stringify({ password, pad: "x".repeat(12) });
  for (let n = 1; n <= 4; n++) {
    b64 = Buffer.from(b64).toString("base64");
    const r = auditJson({ note: b64 });
    assert.equal(r.refused, true, `base64 nested ${n}`);
    const limited = r.problems.some((p) => p.problem === "base64-depth-limit");
    assert.equal(
      limited,
      n > 3,
      `base64 nested ${n}: depth problem only past the bound`,
    );
    if (n <= 3)
      assert.ok(
        r.hits.some((h) => h.kind === "credential-field"),
        `base64 nested ${n} found`,
      );
    valueFree(r, [password]);
  }
  let pct = `password=${password}`;
  for (let n = 1; n <= 10; n++) {
    pct = encodeURIComponent(pct);
    const r = auditBuffer(Buffer.from(`log: ${pct}\n`), "log.txt");
    assert.equal(r.refused, true, `percent-encoded ${n} times`);
    assert.equal(
      r.problems.some((p) => p.problem === "normalize-limit"),
      n > 8,
      `percent ${n}: normalize problem only past the bound`,
    );
  }
  const many = Array.from({ length: 30 }, (_, i) =>
    Buffer.from(`benign text number ${i} ${"y".repeat(20)}`).toString("base64"),
  ).join(" ");
  const capped = auditBuffer(Buffer.from(many), "log.txt", {
    limits: { base64Decodes: 10 },
  });
  assert.ok(capped.problems.some((p) => p.problem === "base64-decode-limit"));
  assert.deepEqual(auditBuffer(Buffer.from(many), "log.txt").problems, []);
});

test("r2 (5) base64url is decoded, and only what was decoded and scanned is exempt from entropy", () => {
  const password = short(12);
  const encoded = until(
    () =>
      Buffer.from(
        JSON.stringify({ password, pad: hex(3) + "???>>>" }),
      ).toString("base64url"),
    (e) => /[-_]/.test(e),
  );
  const hidden = auditJson({ note: encoded });
  assert.equal(hidden.refused, true, "a base64url layer is decoded");
  assert.ok(
    hidden.hits.some(
      (h) => h.kind === "credential-field" && /base64/.test(h.layer),
    ),
  );
  valueFree(hidden, [password]);
  const benign = until(
    () =>
      Buffer.from(
        `{"msg":"Build ${hex(4)} READY at Stage ${randomBytes(1)[0]}???>>>"}`,
      ).toString("base64url"),
    (t) =>
      t.length >= 32 &&
      /\d/.test(t) &&
      /[a-z]/.test(t) &&
      /[A-Z]/.test(t) &&
      (t.match(/[-_]/g) ?? []).length >= 1 &&
      (t.match(/[-_]/g) ?? []).length <= 3,
  );
  assert.deepEqual(
    auditJson({ note: benign }).hits,
    [],
    "decoded, scanned, clean: exempt",
  );
  const undecoded = auditJson({ note: benign }, { limits: { base64Depth: 0 } });
  assert.equal(undecoded.refused, true, "not decoded: not exempt");
  assert.ok(undecoded.problems.some((p) => p.problem === "base64-depth-limit"));
});

test("r2 (6) archive metadata: names, comments, extra fields, local names and unclaimed bytes, value-free", () => {
  const token = hex(24);
  const password = short(12);
  const secretName = `token=${token}.txt`;
  const cases = {
    "entry-name": makeZipX([{ name: secretName, data: "safe" }]),
    "archive-comment": makeZipX([{ name: "note.txt", data: "safe" }], {
      comment: JSON.stringify({ token }),
    }),
    "entry-comment": makeZipX([
      { name: "note.txt", data: "safe", comment: `password=${password}` },
    ]),
    "central-extra": makeZipX([
      {
        name: "note.txt",
        data: "safe",
        extra: `\x01\x99\x10\x00password=${password}`,
      },
    ]),
    "local-extra": makeZipX([
      {
        name: "note.txt",
        data: "safe",
        localExtra: `\x01\x99\x10\x00password=${password}`,
      },
    ]),
    "local-name": makeZipX([
      { name: "note.txt", localName: secretName, data: "safe" },
    ]),
    "gap-before-central": makeZipX([{ name: "note.txt", data: "safe" }], {
      gap: JSON.stringify({ password }),
    }),
    trailing: makeZipX([{ name: "note.txt", data: "safe" }], {
      trailing: JSON.stringify({ password }),
    }),
  };
  for (const [name, zip] of Object.entries(cases)) {
    const report = auditBuffer(zip, "trace.zip");
    assert.equal(report.refused, true, name);
    valueFree(report, [token, password, secretName]);
  }
  const named = auditBuffer(cases["entry-name"], "trace.zip");
  assert.ok(
    named.hits.every((h) => /name withheld/.test(h.entry)),
    "the entry is reported by position",
  );
  const clean = makeZipX([
    { name: "0-trace.trace", data: "{}", descriptor: true },
    { name: "resources/a.json", data: '{"ok":true}', descriptor: true },
  ]);
  assert.deepEqual(
    auditBuffer(clean, "trace.zip").hits,
    [],
    "data descriptors are claimed bytes",
  );
  assert.deepEqual(auditBuffer(clean, "trace.zip").problems, []);
  const dir = mkdtempSync(path.join(tmpdir(), "evidence-audit-path-"));
  writeFileSync(path.join(dir, `api_key=${token}.json`), "{}");
  const byPath = auditPaths([dir], { root: dir });
  assert.equal(
    byPath.refused,
    true,
    "a credential-bearing file NAME is a finding",
  );
  valueFree(byPath, [token]);
});

test("r2 (1) CLI: a quarantine that cannot move a file exits 3 and says so; staging copies exactly what was audited", () => {
  const root = mkdtempSync(path.join(tmpdir(), "evidence-audit-r2-"));
  const group = path.join(root, "e2e/walk-results/g1");
  mkdirSync(group, { recursive: true });
  const password = pw();
  writeFileSync(path.join(group, "steps.txt"), "walk rc=0\n");
  writeFileSync(path.join(group, "summary.json"), JSON.stringify({ password }));
  const cli = path.join(here, "evidence-audit.mjs");
  const run = (args) =>
    spawnSync(process.execPath, [cli, ...args], {
      cwd: root,
      encoding: "utf8",
    });
  chmodSync(group, 0o500);
  let r;
  try {
    r = run([
      "--glob",
      "e2e/walk-results/**/*",
      "--report",
      path.join(root, "refused.json"),
      "--quarantine",
      path.join(root, "q"),
      "--stage",
      path.join(root, "stage-refused"),
      "--nonce",
      "t1",
    ]);
  } finally {
    chmodSync(group, 0o700);
  }
  assert.equal(
    existsSync(path.join(group, "summary.json")),
    true,
    "control premise: the move was denied",
  );
  assert.equal(r.status, 3, r.stdout);
  const refused = JSON.parse(
    readFileSync(path.join(root, "refused.json"), "utf8"),
  );
  assert.deepEqual(refused.quarantineFailed, [
    "e2e/walk-results/g1/summary.json",
  ]);
  assert.deepEqual(refused.quarantined, []);
  assert.equal(refused.stage.complete, false);
  assert.equal(
    existsSync(path.join(root, "stage-refused")),
    false,
    "nothing staged after an incomplete quarantine",
  );
  assert.equal(JSON.stringify(refused).includes(password), false);
  writeFileSync(path.join(group, "summary.json"), JSON.stringify({ ok: true }));
  r = run([
    "--glob",
    "e2e/walk-results/**/*",
    "--report",
    path.join(root, "clean.json"),
    "--stage",
    path.join(root, "stage"),
    "--nonce",
    "t2",
  ]);
  assert.equal(r.status, 0, r.stdout);
  for (const f of ["steps.txt", "summary.json"])
    assert.deepEqual(
      readFileSync(path.join(root, "stage/e2e/walk-results/g1", f)),
      readFileSync(path.join(group, f)),
    );
  const check = (file, nonce) =>
    spawnSync(
      process.execPath,
      [cli, "--check-report", file, "--nonce", nonce],
      { encoding: "utf8" },
    );
  assert.equal(
    check(path.join(root, "clean.json"), "t2").stdout.trim(),
    "clean",
  );
  assert.equal(
    check(path.join(root, "clean.json"), "other").status,
    1,
    "a report from another run is invalid",
  );
  const tampered = JSON.parse(
    readFileSync(path.join(root, "clean.json"), "utf8"),
  );
  tampered.note = "anything";
  writeFileSync(path.join(root, "tampered.json"), JSON.stringify(tampered));
  assert.equal(
    check(path.join(root, "tampered.json"), "t2").stdout.trim(),
    "invalid",
  );
});

// --- r4: preserve every word of qualified names; no plural-token bypass. ---
const PLURAL_NAMES = [
  "api_tokens",
  "session_tokens",
  "access_tokens",
  "refresh_tokens",
  "id_tokens",
  "csrf_tokens",
  "auth_tokens",
];
const pluralForms = (name, value) => {
  const json = JSON.stringify({ [name]: value });
  return {
    direct: json,
    escaped: JSON.stringify({ body: json }),
    text: `saved ${name}=${value} ok\n`,
    base64: JSON.stringify({ body: Buffer.from(json).toString("base64") }),
    "base64-nested": JSON.stringify({
      body: JSON.stringify({ postData: Buffer.from(json).toString("base64") }),
    }),
  };
};

// Separate tests ensure a red witness reports every name/form, not only the
// first failed loop assertion. No registry: name detection must do the work.
for (const name of PLURAL_NAMES)
  for (const form of Object.keys(pluralForms(name, "")))
    test(`r4 plural ${name} ${form}: short credential pairs refuse`, () => {
      const camel = name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
      for (const key of [name, camel])
        for (const n of [6, 8, 12, 18]) {
          const value = short(n);
          const report = auditBuffer(
            Buffer.from(pluralForms(key, value)[form]),
            "resources/r.json",
          );
          assert.equal(report.refused, true, `${key} (${form}, ${n} chars)`);
          assert.ok(
            report.hits.some(
              (h) =>
                h.kind === "credential-field" &&
                h.name === key &&
                (!form.startsWith("base64") || h.layer.includes("base64")),
            ),
            "must find the decoded pair, not only an entropy-shaped encoding",
          );
          valueFree(report, [value]);
        }
    });

for (const name of [
  "tokens",
  "cached_tokens",
  "completion_tokens",
  "tokens_per_second",
])
  test(`r4 count spelling ${name}: a credential-shaped value still refuses`, () => {
    const value = "v2-a8f3c1"; // retain and strengthen the original r3 control
    for (const body of Object.values(pluralForms(name, value))) {
      const report = auditBuffer(Buffer.from(body), "resources/r.json");
      assert.equal(report.refused, true);
      valueFree(report, [value]);
    }
  });

test("r4 bare tokens: identifier lists stay clean, credential-shaped items refuse", () => {
  for (const list of [
    "[identifier]",
    "[keywords,phrases.lower]",
    "[]",
    "[ keywords, phrases.lower ]",
  ])
    for (const body of [
      `tokens = ${list}\n`,
      JSON.stringify({ code: `tokens = ${list}\n    if not tokens: return` }),
      JSON.stringify({ tokens: list }),
    ])
      assert.equal(
        auditBuffer(Buffer.from(body), "resources/r.json").refused,
        false,
      );
  // Only the exact bare spelling inherits this code-list exception. Exercise
  // every pair reader, including serialized headers/evaluate arguments/URLs.
  for (const name of [...PLURAL_NAMES, "apiTokens", "sessionTokens", "TOKENS"])
    for (const list of [
      "[identifier]",
      "[keywords,phrases.lower]",
      "[]",
      "[ keywords, phrases.lower ]",
    ])
      for (const body of [
        ...Object.values(pluralForms(name, list)),
        JSON.stringify({ name, value: list }),
        JSON.stringify({ k: name, v: { s: list } }),
        `http://127.0.0.1/?${name}=${encodeURIComponent(list)}`,
      ])
        assert.equal(
          auditBuffer(Buffer.from(body), "resources/r.json").refused,
          true,
          `${name} must not inherit the bare-name list exemption`,
        );
  const value = `Pw7${hex(5)}`;
  for (const list of [
    `[${value}]`,
    `[keywords,${value}]`,
    `[ keywords, ${value} ]`,
  ])
    for (const body of [
      ...Object.values(pluralForms("tokens", list)),
      JSON.stringify({ code: `tokens = ${list}\n    next` }),
    ]) {
      const report = auditBuffer(Buffer.from(body), "resources/r.json");
      assert.equal(report.refused, true);
      valueFree(report, [value]);
    }
});

test("r4 qualified plurals: production landing refuses every pair representation", () => {
  const land = path.join(here, "../walks/vm/land.sh");
  for (const name of PLURAL_NAMES)
    for (const [form, body] of Object.entries(pluralForms(name, short(8)))) {
      const root = mkdtempSync(path.join(tmpdir(), "evidence-r4-land-"));
      const group = path.join(root, "e2e/walk-results/group");
      mkdirSync(group, { recursive: true });
      symlinkSync(here, path.join(root, "e2e/scripts"));
      writeFileSync(path.join(group, "summary.json"), body);
      const run = spawnSync(
        "bash",
        [land, "r4", "1", "e2e/walk-results/**/summary.json", "--", "true"],
        {
          cwd: root,
          encoding: "utf8",
          env: { ...process.env, HOME: root },
        },
      );
      const landed = path.join(root, "e2e/walk-landed/r4/e2e/walk-results");
      assert.equal(run.status, 1, `${name} (${form}) must refuse landing`);
      assert.notEqual(
        readFileSync(
          path.join(landed, "command/command-exit.txt"),
          "utf8",
        ).trim(),
        "0",
      );
      assert.equal(existsSync(path.join(landed, "group/summary.json")), false);
    }
});

// R4 follow-up: list contents are untrusted, not necessarily code identifiers.
const LIST_NAMES = [
  "tokens",
  ...PLURAL_NAMES.flatMap((name) => [
    name,
    name.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
  ]),
];
const listForms = (name, value) => ({
  ...pluralForms(name, value),
  base64url: JSON.stringify({
    body: Buffer.from(JSON.stringify({ [name]: value })).toString("base64url"),
  }),
  header: JSON.stringify({ name, value }),
  evaluate: JSON.stringify({ k: name, v: { s: value } }),
  url: `http://127.0.0.1/?${name}=${encodeURIComponent(value)}`,
});
const unsafeList = (style, value) =>
  style === "tab" ? `[ keywords, \t${value} ]` : `[ keywords, ${value}-9 ]`;

for (const style of ["tab", "hyphen"])
  for (const form of Object.keys(listForms("tokens", "")))
    test(`r4 list regression ${style} ${form}: every qualified and bare name refuses`, () => {
      const failures = [];
      for (const name of LIST_NAMES) {
        const value = `Pw7${hex(5)}`;
        const body = listForms(name, unsafeList(style, value))[form];
        const report = auditBuffer(Buffer.from(body), "resources/r.json");
        valueFree(report, [value]);
        if (
          !report.refused ||
          !report.hits.some(
            (h) =>
              h.name === name &&
              (!form.startsWith("base64") || h.layer.includes("base64")),
          )
        )
          failures.push(name);
      }
      assert.deepEqual(
        failures,
        [],
        "every decoded credential pair must refuse",
      );
    });

for (const style of ["tab", "hyphen"])
  test(`r4 list regression ${style}: production landing refuses all encodings`, () => {
    const failures = [];
    for (const name of LIST_NAMES) {
      const value = `Pw7${hex(5)}`;
      for (const [form, body] of Object.entries(
        listForms(name, unsafeList(style, value)),
      )) {
        const root = mkdtempSync(path.join(tmpdir(), "evidence-list-land-"));
        const group = path.join(root, "e2e/walk-results/group");
        mkdirSync(group, { recursive: true });
        symlinkSync(here, path.join(root, "e2e/scripts"));
        writeFileSync(path.join(group, "summary.json"), body);
        const run = spawnSync(
          "bash",
          [
            path.join(here, "../walks/vm/land.sh"),
            "lists",
            "1",
            "e2e/walk-results/**/summary.json",
            "--",
            "true",
          ],
          { cwd: root, encoding: "utf8", env: { ...process.env, HOME: root } },
        );
        const landed = path.join(
          root,
          "e2e/walk-landed/lists/e2e/walk-results",
        );
        const status = path.join(landed, "command/command-exit.txt");
        if (
          run.status !== 1 ||
          !existsSync(status) ||
          readFileSync(status, "utf8").trim() === "0" ||
          existsSync(path.join(landed, "group/summary.json"))
        )
          failures.push({ name, form });
      }
    }
    assert.deepEqual(
      failures,
      [],
      "production must never stage an unsafe list",
    );
  });

test("r4 list regression: exact bare identifier lists survive serialized separators", () => {
  for (const value of [
    "[ keywords, \tphrases.lower ]",
    "[\tidentifier\t]",
    "[]",
  ])
    for (const body of [
      ...Object.values(listForms("tokens", value)),
      JSON.stringify({ code: `tokens = ${value}\n    if not tokens: return` }),
    ])
      assert.equal(
        auditBuffer(Buffer.from(body), "resources/r.json").refused,
        false,
      );
});

test("r4 scalar compatibility: list decoding must not turn escaped credentials into prose", () => {
  for (const name of ["password", "tokens", ...PLURAL_NAMES])
    for (const separator of ["\t", "\n", "\r"])
      for (const form of [
        "direct",
        "escaped",
        "base64",
        "header",
        "evaluate",
      ]) {
        const value = `Pw7${hex(4)}${separator}${hex(4)}`;
        const report = auditBuffer(
          Buffer.from(listForms(name, value)[form]),
          "resources/r.json",
        );
        assert.equal(report.refused, true, `${name} (${form}) stays sensitive`);
        valueFree(report, [value]);
      }
});

// --- r3: the two findings of the r2 gate run. Each control is
// red at ae14c2defe. ----------------------------------------------------------

const nestJson = (o, depth) => {
  let v = o;
  for (let i = 0; i < depth; i++) v = { body: JSON.stringify(v) };
  return JSON.stringify(v);
};

test("r3 (1) a credential pair in JSON carried inside a JSON string is refused: first key or not, any depth, escaped quotes", () => {
  for (const name of [
    "loginPasswordValue",
    "password",
    "x-session-token",
    "api_key",
    "otpCode",
  ])
    for (const n of [6, 12]) {
      const value = short(n);
      const once = nestJson({ [name]: value }, 1);
      // The r2 gate's control exactly: {"body":"{\"<name>\":\"<value>\"}"}.
      assert.equal(once, `{"body":"{\\"${name}\\":\\"${value}\\"}"}`);
      const forms = {
        once,
        "after-another-key": JSON.stringify({
          body: JSON.stringify({ email: "e2e-1@futureagi.com", [name]: value }),
        }),
        twice: nestJson({ [name]: value }, 2),
        thrice: nestJson({ [name]: value }, 3),
        "trace-postData": JSON.stringify({
          type: "before",
          method: "fetch",
          params: { postData: JSON.stringify({ [name]: value }) },
        }),
        "array-element": JSON.stringify({
          rows: [JSON.stringify({ [name]: value })],
        }),
        // A value that itself carries a quote: the whole JSON string is the value.
        "escaped-quote": JSON.stringify({ [name]: `Q"${value}` }),
        "escaped-quote-nested": nestJson({ [name]: `Q"${value}` }, 1),
      };
      for (const [form, text] of Object.entries(forms)) {
        const report = auditBuffer(Buffer.from(text), "resources/r.json");
        assert.equal(report.refused, true, `${name} (${form}, ${n} chars)`);
        assert.ok(
          report.hits.some(
            (h) => h.kind === "credential-field" && h.name === name,
          ),
          `${name} (${form}, ${n} chars): found as a credential pair`,
        );
        valueFree(report, [value]);
      }
    }
});

// A get-evals-shaped response (GET /sdk/api/v1/get-evals/): eval templates
// whose Python source is a JSON string, with line ends as \n escapes.
const evalTemplate = (assignment) =>
  [
    "def evaluate(input, output, expected=None, **kwargs):",
    '    text = str(output.get("text", output or "")).lower().strip()',
    `    ${assignment}`,
    '    if not tokens: return {"score": 0.0, "reason": "no keywords"}',
    "    hits = [t for t in tokens if t in text]",
    '    return {"score": len(hits) / len(tokens), "reason": ", ".join(hits)}',
  ].join("\n");

test("r3 (2) get-evals-shaped code, count nouns and redacted line ends stay clean; credentials beside them do not", () => {
  const assignments = [
    "tokens = [keywords]",
    "tokens = [keywords,phrases]",
    "tokens = [REDACTED]", // the trace scrubber's rewrite of that line
    "tokens = text.split()",
    "tokens = []",
    "password = [fieldname]",
    "secret = [keywords,phrases.lower]",
    "api_key = get_key(config)",
  ];
  const response = {
    status: true,
    result: {
      evals: assignments.map((assignment, i) => ({
        id: `3f0c1a52-8b4e-4c1d-9a7e-1f2b3c4d5e${String(i).padStart(2, "0")}`,
        name: `keyword_presence_${i}`,
        eval_type: "code",
        code: evalTemplate(assignment),
        config: { required_keys: ["output"], output: "score" },
      })),
    },
  };
  const text = JSON.stringify(response);
  assert.ok(text.includes("tokens = [REDACTED]\\n    if not tokens"));
  const clean = auditBuffer(Buffer.from(text), "resources/evals.json");
  assert.deepEqual(
    clean.hits.map((h) => `${h.kind} ${h.name}`),
    [],
  );
  assert.deepEqual(clean.problems, []);
  // Actual model-token counts are clean values, not a blanket name exemption.
  // Metadata/predicate names keep their structural exclusions.
  const v = "v2-a8f3c1";
  const counts = auditJson({
    tokens: 17,
    num_tokens: v,
    total_tokens: v,
    cached_tokens: "17",
    completion_tokens: "17",
    tokens_per_second: "17.5",
    token_count: v,
    tokenizer: v,
  });
  assert.deepEqual(
    counts.hits.map((h) => `${h.kind} ${h.name}`),
    [],
  );
  // Still refused: a credential-like item in brackets, an identifier-shaped
  // credential in brackets, a credential qualifier on the plural, an opaque
  // token under a count noun, a value before a \n escape.
  const credential = short(12);
  const identifierLike = `Pw7${hex(5)}`; // a digit: credential-like alone
  const opaque = hex(24);
  const still = {
    "bracketed credential": `saved password=[${credential}] ok\n`,
    "bracketed identifier-shaped credential": JSON.stringify({
      log: `password=[${identifierLike}]\n    next`,
    }),
    "credential after a closed bracket": `saved password=[a]${credential} ok\n`,
    "qualified plural": JSON.stringify({ access_tokens: credential }),
    "opaque token under a count noun": JSON.stringify({ tokens: opaque }),
    "value before a line-end escape": JSON.stringify({
      log: `password=${credential}\n    next`,
    }),
  };
  for (const [form, body] of Object.entries(still)) {
    const report = auditBuffer(Buffer.from(body), "resources/r.json");
    assert.equal(report.refused, true, form);
    valueFree(report, [credential, identifierLike, opaque]);
  }
});
