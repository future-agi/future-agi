// The shared Omega client in Chromium, WebKit and Firefox (READINESS lane 1.5: L07's
// browser test, L06's long exec, CH-12's buffering proxy), against harness-server.mjs.
//
//   node run-browser-checks.mjs [--out receipt.json] [--scale 1] [--browsers chromium,webkit,firefox]
//
// Playwright: OMEGA_LOCAL_PLAYWRIGHT (a playwright/index.js, the omega-local convention),
// else the e2e workspace's. Browsers come from PLAYWRIGHT_BROWSERS_PATH or the default
// cache. Exit 1 when any check fails in any browser.
import { existsSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { startHarness } from "./harness-server.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../../../../../..");
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index === -1 ? fallback : argv[index + 1];
};
const scale = Number(arg("scale", "1"));
const browsers = arg("browsers", "chromium,webkit,firefox").split(",");
const outFile = arg("out", null);
const EXEC_SECONDS = 90;

// CP COPY1 / READINESS L07.
const COPY1 = [
  /api key/i,
  /omega server/i,
  /operator/i,
  /binding/i,
  /colima/i,
  /container host/i,
  /coming soon/i,
  /omega squad/i,
  /\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b/,
];
const COPY_CASES = [
  { status: 401, code: "assertion_invalid" },
  { status: 401, code: "assertion_expired" },
  { status: 401, code: "assertion_replayed" },
  { status: 403, code: "identity_not_connected" },
  { status: 403, code: "role_out_of_date" },
  { status: 503, code: "verification_unavailable" },
  { status: 403, code: "host_delegation_scope_denied" },
  { status: 401, code: "unauthorized" },
  { status: 429, code: "omega_rate_limited" },
  { status: 403, code: "omega_viewer_not_admitted", exchange: "viewer" },
  { status: 403, code: "session_exchange_refused", exchange: "refused" },
  { status: 429, code: "session_exchange_throttled", exchange: "throttled" },
  { status: 503, code: "session_exchange_unavailable", exchange: "down" },
  // An owner or admin acting on a sandbox another member created
  // (EE workspace/sandboxes/access/creator-only.ts), in EE's own shape and words.
  {
    status: 403,
    code: "role_forbidden",
    detail: { reason: "not_creator", verb: "operate" },
    message:
      "Only the member who created this sandbox can run commands in it, change its files or change its state.",
  },
];

async function bundle() {
  const require = createRequire(path.join(frontend, "package.json"));
  const esbuild = require("esbuild");
  const stub = path.join(here, "axios-stub.js");
  const result = await esbuild.build({
    entryPoints: [path.join(here, "harness-entry.js")],
    bundle: true,
    write: false,
    format: "esm",
    target: "es2020",
    logLevel: "silent",
    plugins: [
      {
        name: "fa-aliases",
        setup(build) {
          build.onResolve({ filter: /^src\/utils\/axios$/ }, () => ({
            path: stub,
          }));
          build.onResolve({ filter: /^src\// }, async (args) =>
            build.resolve(`./${args.path}`, {
              resolveDir: frontend,
              kind: args.kind,
            }),
          );
        },
      },
    ],
  });
  return result.outputFiles[0].text;
}

function loadPlaywright() {
  const candidates = [
    process.env.OMEGA_LOCAL_PLAYWRIGHT,
    path.resolve(frontend, "../e2e/node_modules/playwright/index.js"),
  ].filter(Boolean);
  const found = candidates.find((file) => existsSync(file));
  if (!found)
    throw new Error(
      `Playwright not found; set OMEGA_LOCAL_PLAYWRIGHT (tried ${candidates.join(", ")})`,
    );
  return import(pathToFileURL(found).href).then(
    (module) => module.default ?? module,
  );
}

const contiguous = (ids, total) =>
  ids.length === total && ids.every((id, index) => id === index + 1);

async function runOne(playwright, name, code) {
  const harness = await startHarness({ bundle: code, scale });
  const browser = await playwright[name].launch();
  const checks = [];
  const check = (id, pass, detail) => {
    checks.push({ id, pass: Boolean(pass), detail });
    process.stderr.write(`[${name}] ${pass ? "ok  " : "FAIL"} ${id}\n`);
  };
  const step = (text) => process.stderr.write(`[${name}] ${text}\n`);
  try {
    const page = await (await browser.newContext()).newPage();
    const consoleErrors = [];
    // The browser logs each refusal this run asks for ("Failed to load resource: ... 401");
    // anything else, and every uncaught page error, fails the run.
    page.on("console", (message) => {
      if (
        message.type() === "error" &&
        !/^Failed to load resource/.test(message.text())
      )
        consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) =>
      consoleErrors.push(`pageerror: ${error.message}`),
    );
    await page.goto(`${harness.origin}/`);
    await page.waitForFunction(() => window.omegaHarnessReady === true, null, {
      timeout: 15_000,
    });

    step("page ready");
    // L07: every refusal in Future AGI words; the page never shows EE's text.
    const copy = await page.evaluate(
      (cases) => window.omegaHarness.copy(cases),
      COPY_CASES,
    );
    const pageText = await page.evaluate(() => document.body.innerText);
    const offending = COPY1.filter((pattern) => pattern.test(pageText)).map(
      String,
    );
    check(
      "L07 page text passes COPY1 in every refusal state",
      offending.length === 0,
      { offending, cases: copy.length },
    );
    const notConnected = copy.find(
      (row) => row.code === "identity_not_connected",
    );
    check(
      "L07 a user with no connected identity sees the not-connected copy",
      notConnected?.title === "Omega isn't connected for your account" &&
        /isn't connected to Omega in this workspace yet/.test(
          notConnected.message,
        ),
      notConnected,
    );
    const notCreator = copy.find(
      (row) => row.key === "role_forbidden:not_creator:operate",
    );
    check(
      "L07 an admin acting on another member's sandbox reads that only its creator can, not 'only owners and admins'",
      notCreator?.state === "refused" &&
        notCreator.title === "Only the sandbox's creator can do this" &&
        /^Only the member who created this sandbox can run commands in it/.test(
          notCreator.message,
        ) &&
        !/only workspace owners and admins/i.test(
          `${notCreator.title} ${notCreator.message}`,
        ) &&
        !/only workspace owners and admins/i.test(pageText),
      notCreator,
    );
    // The four session-exchange refusals, through the host axios instance's
    // rejection shape (axios-stub.js): each reads its own copy, never the
    // catch-all "Omega isn't available here" (verifier finding 1 on 1bf593b37).
    const byExchange = Object.fromEntries(
      copy.filter((row) => row.exchange).map((row) => [row.exchange, row]),
    );
    const { viewer, refused, throttled, down } = byExchange;
    check(
      "L07 the session exchange's viewer, refused, throttled and down answers each read their own copy",
      viewer?.state === "refused" &&
        viewer.status === 403 &&
        viewer.resultCode === "omega_viewer_not_admitted" &&
        viewer.title === "Viewers can't use Omega" &&
        refused?.state === "refused" &&
        refused.status === 403 &&
        refused.resultCode === "session_exchange_refused" &&
        throttled?.status === 429 &&
        throttled.resultCode === "session_exchange_throttled" &&
        /too many times in the last minute\. Try again in a few seconds\.$/.test(
          throttled.message,
        ) &&
        down?.state === "unavailable" &&
        down.resultCode === "session_exchange_unavailable",
      byExchange,
    );
    const stale = copy.filter((row) =>
      ["assertion_expired", "assertion_replayed", "role_out_of_date"].includes(
        row.code,
      ),
    );
    check(
      "L07 a stale key is replaced once and refused only when the fresh one is too",
      stale.every((row) => row.ok === false && row.state === "refused"),
      stale.map((r) => r.code),
    );
    check(
      "L07 every result carries a title and no server text",
      copy.every(
        (row) =>
          row.ok === false &&
          row.title &&
          row.message !== "Valid Omega API key required.",
      ),
      copy.map((r) => [r.code, r.title]),
    );
    check("L07 one-use keys are never reused", harness.log.reusedKeys === 0, {
      reusedKeys: harness.log.reusedKeys,
    });

    // L06: a stream cut many times (clean end and dropped socket) arrives whole.
    const feed = await page.evaluate(() => window.omegaHarness.feed());
    const feedIds = feed.events
      .filter((e) => e.event === "log")
      .map((e) => e.json.n);
    const cuts = harness.log.feed.connections.map((c) => c.cut).filter(Boolean);
    check(
      "L06 a stream across clean and dropped cuts: every event once, in order",
      feed.result.ended === true && contiguous(feedIds, 60),
      {
        connections: harness.log.feed.connections.length,
        cuts: [...new Set(cuts)],
        ids: feedIds.length,
      },
    );

    step("exec runs started");
    // L06: `sleep 90; echo done` both ways EE can carry it, side by side.
    const [held, cut] = await Promise.all([
      page.evaluate((s) => window.omegaHarness.exec("hold", s), EXEC_SECONDS),
      page.evaluate((s) => window.omegaHarness.exec("cut", s), EXEC_SECONDS),
    ]);
    const exitOf = (run) =>
      run.events.find((e) => e.event === "exit")?.json?.exitCode;
    const expectedMs = EXEC_SECONDS * 1000 * scale;
    const around = (ms) => ms >= expectedMs - 1000 && ms <= expectedMs + 10_000;
    check(
      "L06 a 90 s exec on one stream shows exit 0 at about 90 s",
      held.result.ended === true &&
        exitOf(held) === 0 &&
        around(held.elapsedMs) &&
        held.opens.length === 1,
      { elapsedMs: held.elapsedMs, opens: held.opens.length },
    );
    const attaches = harness.log.execRequests.filter(
      (r) => r.kind === "attach",
    );
    check(
      "L06 a 90 s exec cut at the key's expiry reattaches and shows exit 0 at about 90 s, with no event twice",
      cut.result.ended === true &&
        exitOf(cut) === 0 &&
        around(cut.elapsedMs) &&
        attaches.length >= 1 &&
        new Set(cut.events.map((e) => e.id)).size === cut.events.length,
      {
        elapsedMs: cut.elapsedMs,
        opens: cut.opens.map((o) => [o.path, o.at]),
        attaches,
      },
    );

    // CH-12: a proxy that holds the stream is detected inside 10 s and polled without loss.
    const buffered = await page.evaluate(() => window.omegaHarness.buffered());
    const bufferedIds = buffered.events
      .filter((e) => e.event === "log")
      .map((e) => e.json.n);
    const firstPoll = harness.log.slowFeed.connections.find(
      (c) => c.mode === "poll",
    );
    check(
      "CH-12 buffering detected and the first long-poll window open within 10 s",
      Boolean(firstPoll) && firstPoll.at < 10_000,
      {
        firstPollAtMs: firstPoll?.at,
        modes: harness.log.slowFeed.connections.map((c) => c.mode),
      },
    );
    check(
      "CH-12 long-poll fallback loses and repeats nothing",
      buffered.result.ended === true &&
        buffered.result.mode === "poll" &&
        contiguous(bufferedIds, 40),
      {
        ids: bufferedIds.length,
        mode: buffered.result.mode,
        elapsedMs: buffered.elapsedMs,
      },
    );
    check(
      "no script errors on the page",
      consoleErrors.length === 0,
      consoleErrors.slice(0, 5),
    );
    return {
      browser: name,
      version: browser.version(),
      checks,
      pass: checks.every((c) => c.pass),
    };
  } finally {
    await browser.close();
    await harness.close();
  }
}

const startedAt = new Date().toISOString();
const code = await bundle();
const playwright = await loadPlaywright();
const results = await Promise.all(
  browsers.map((name) =>
    runOne(playwright, name, code).catch((error) => ({
      browser: name,
      pass: false,
      error: String(error?.stack || error),
    })),
  ),
);
const receipt = {
  test: "FA-OMEGA-CLIENT browser checks",
  startedAt,
  finishedAt: new Date().toISOString(),
  scale,
  execSeconds: EXEC_SECONDS,
  results,
  pass: results.every((r) => r.pass),
};
if (outFile) writeFileSync(outFile, `${JSON.stringify(receipt, null, 2)}\n`);
for (const result of results) {
  process.stdout.write(
    `${result.browser} ${result.version || ""}: ${result.pass ? "PASS" : "FAIL"}\n`,
  );
  for (const c of result.checks || [])
    process.stdout.write(
      `  ${c.pass ? "ok  " : "FAIL"} ${c.id}${c.pass ? "" : ` ${JSON.stringify(c.detail)}`}\n`,
    );
  if (result.error) process.stdout.write(`  ${result.error}\n`);
}
process.exit(receipt.pass ? 0 : 1);
