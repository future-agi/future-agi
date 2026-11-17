// Run only on an already booted cloud proof VM at its clean composed commit.
// Each case changes an INPUT alone, leaves assertions untouched, requires the
// intended assertion to fail, restores byte-for-byte, then runs both browsers
// green. Raw Playwright reports stay on VM; only value-free quotes are landed.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { execFileSync, spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { assertValueFreeReceipt } from "../../lib/walk/receipt-values.mjs";

if (process.platform !== "linux") throw new Error("cloud VM only");
const root = resolve(import.meta.dirname, "../../..");
const out = resolve(root, "out/w17/fallibility");
mkdirSync(out, { recursive: true });
const spec = "e2e/flows/walks/export-sync.spec.ts";
const adapters = "e2e/lib/walk/export-destinations.ts";
const sync = "e2e/lib/walk/export-sync.ts";
const cases = [
  {
    name: "connection-interval",
    file: adapters,
    // synthetic test value — baseline mutation anchor
    from: "{ interval: 'Every 30 minutes',",
    // synthetic test value — negative interval input
    to: "{ interval: 'Every 15 minutes',",
    expected: "sync_interval_seconds",
    frame: "export-sync.spec.ts",
    // synthetic test value — synthetic value-free failure description
    quote: "Expected sync_interval_seconds 1800; received 900.",
  },
  {
    name: "delivery-window",
    file: spec,
    // synthetic test value — baseline synthetic request anchor
    from: "mintRequestLogs(actor, probe, `e2e-w17-${suffix}`)",
    // synthetic test value — negative aged-request input
    to: "mintRequestLogs(actor, probe, `e2e-w17-${suffix}`, 3, 21)",
    expected: "toEqual",
    frame: "export-sync.ts",
    // synthetic test value — synthetic value-free failure description
    quote:
      "Expected the independently minted request set; the destination window excluded the aged input set.",
  },
  {
    name: "cancel-before-dispatch",
    file: sync,
    // Release the real preparation before delaying the navigation input.
    // The unchanged zero-dispatch assertion must observe the resulting POST.
    // synthetic test value — baseline navigation anchor
    from: "await page.getByRole('button', { name: 'Back to Integrations' }).click();",
    // synthetic test value — negative delayed-navigation input
    to: "release(); await page.waitForTimeout(2000); await page.getByRole('button', { name: 'Back to Integrations' }).click();",
    expected: "toBe",
    frame: "export-sync.ts",
    // synthetic test value — synthetic value-free failure description
    quote:
      "Expected zero dispatch requests; a POST arrived when preparation was released before cancellation.",
  },
  {
    name: "refused-edit",
    file: spec,
    // synthetic test value — baseline refused-edit anchor
    from: "editCredentials(page, d.id!, d.platform, d.badFields, UI_READY)",
    // synthetic test value — negative valid-credential input
    to: "editCredentials(page, d.id!, d.platform, d.fields, UI_READY)",
    expected: "400",
    frame: "export-sync.spec.ts",
    // synthetic test value — synthetic value-free failure description
    quote:
      "Expected refused edit HTTP 400; received HTTP 200 for the valid input credential.",
  },
  {
    name: "failure-recovery",
    file: adapters,
    // synthetic test value — baseline revocation anchor
    from: "sink.revoked(d.credential, down)",
    // synthetic test value — negative unrelated credential derivative
    to: "sink.revoked(`${d.credential}-unrelated`, down)",
    expected: "failed",
    frame: "integration-export.ts",
    // synthetic test value — synthetic value-free failure description
    quote:
      "Expected failed sync; no failed row arrived when only an unrelated credential was revoked.",
  },
  {
    name: "management-roles",
    file: spec,
    // synthetic test value — baseline member-management anchor
    from: "scopes.send(member, method, path, {})",
    // synthetic test value — negative owner-management input
    to: "scopes.send(scopes.ownerA, method, path, {})",
    expected: "403",
    frame: "export-sync.spec.ts",
    // synthetic test value — synthetic value-free failure description
    quote:
      "Expected management HTTP 403; owner input was admitted past the role gate.",
  },
];
const result = [];
const run = (label, project) => {
  const dir = resolve(out, label);
  mkdirSync(dir, { recursive: true });
  const args = [
    "playwright",
    "test",
    "--config",
    "playwright.walk.config.ts",
    "--grep",
    "@walk-W17",
    "--reporter=json,line,./walks/vm/counts-reporter.cjs",
    ...(project ? ["--project", project] : []),
  ];
  const proc = spawnSync("yarn", args, {
    cwd: resolve(root, "e2e"),
    encoding: "utf8",
    timeout: 7_200_000,
    maxBuffer: 16 * 1024 * 1024,
    env: {
      ...process.env,
      E2E_WALK_OUT: dir,
      E2E_WALK_COUNTS: resolve(dir, "counts.json"),
      PLAYWRIGHT_JSON_OUTPUT_FILE: resolve(dir, "result.json"),
    },
  });
  writeFileSync(
    resolve(dir, "raw.log"),
    `${proc.stdout ?? ""}\n${proc.stderr ?? ""}`,
  );
  return { proc, dir };
};
const save = () => {
  const json = JSON.stringify(result, null, 2);
  assertValueFreeReceipt(json);
  writeFileSync(resolve(out, "summary.json"), json);
};
const requested =
  process.env.E2E_W17_FALLIBILITY_CASES?.split(",").filter(Boolean);
if (requested?.some((name) => !cases.some((item) => item.name === name)))
  throw new Error("unknown W17 fallibility case");
const selected = requested?.length
  ? cases.filter((item) => requested.includes(item.name))
  : cases;
for (const item of selected) {
  const path = resolve(root, item.file);
  const original = readFileSync(path, "utf8");
  if (original.split(item.from).length !== 2)
    throw new Error(`mutation anchor not unique: ${item.name}`);
  try {
    writeFileSync(path, original.replace(item.from, item.to));
    const { proc, dir } = run(item.name, "chromium");
    const report = JSON.parse(
      readFileSync(resolve(dir, "result.json"), "utf8"),
    );
    const errors = [];
    const visit = (suite) => {
      for (const s of suite.suites ?? []) visit(s);
      for (const s of suite.specs ?? [])
        for (const t of s.tests ?? [])
          for (const r of t.results ?? []) errors.push(...(r.errors ?? []));
    };
    for (const suite of report.suites ?? []) visit(suite);
    const intended = errors.find(
      (e) =>
        String(e.message).includes(item.expected) &&
        String(e.stack).includes(item.frame),
    );
    if (proc.status !== 1 || !intended)
      throw new Error(
        `did not observe intended failed assertion: ${item.name}`,
      );
    // Quote is an explicitly value-free description, not an invented verbatim
    // error. The raw error's fingerprint lets reviewers bind it on the VM.
    const { createHash } = await import("node:crypto");
    const failureQuote = String(intended.message)
      .replace(/\u001b\[[0-9;]*m/g, "")
      .replace(/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/gi, "<identity>")
      .replace(/[A-Za-z0-9.+_-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "<address>")
      .replace(/e2e-[A-Za-z0-9_-]+/g, "<fixture>")
      .split("\n")
      .filter((line) =>
        /Error:|Expected:|Received:|[-+]\s+"|sync_interval_seconds/.test(line),
      )
      .slice(0, 18)
      .join("\n");
    assertValueFreeReceipt(JSON.stringify({ failureQuote }));
    result.push({
      check: item.name,
      result: "failed-as-intended",
      failureDescription: item.quote,
      failureQuote,
      failureDigest: `sha256:${createHash("sha256").update(JSON.stringify(intended)).digest("hex")}`,
    });
    save();
  } finally {
    writeFileSync(path, original);
  }
}
execFileSync("git", ["-C", root, "diff", "--exit-code", "--", "e2e/"]);
const { proc } = run("restored-green");
result.push({
  check: "restored-both-browsers",
  result: proc.status === 0 ? "pass" : "fail",
});
save();
if (proc.status !== 0) throw new Error("restored green failed");
