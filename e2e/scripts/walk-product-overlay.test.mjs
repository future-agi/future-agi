// A group's productSha (e2e/walks/launch/groups.json) is what the walk VM builds and
// walks: e2e/walks/vm/product-overlay.sh checks it out under the launched harness. Before
// this, run-walks.sh built the launched HEAD and only labelled it with productSha.
// Synthetic repositories only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("../walks/vm/product-overlay.sh", import.meta.url));
const runWalks = fileURLToPath(new URL("../walks/vm/run-walks.sh", import.meta.url));
const HARNESS = [
  "e2e",
  "bin/e2e",
  "bin/lib",
  "agentcc-gateway/cmd/agentcc-mcpmock",
  "agentcc-gateway/cmd/agentcc-otlpmock",
];
const identity = {
  GIT_AUTHOR_NAME: "walk test",
  GIT_AUTHOR_EMAIL: "walk-test@example.invalid",
  GIT_COMMITTER_NAME: "walk test",
  GIT_COMMITTER_EMAIL: "walk-test@example.invalid",
};

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "walk-product-"));
  const git = (...args) =>
    execFileSync("git", ["-C", dir, ...args], {
      encoding: "utf8",
      env: { ...process.env, ...identity },
    }).trim();
  const put = (rel, body) => {
    mkdirSync(dirname(join(dir, rel)), { recursive: true });
    writeFileSync(join(dir, rel), body);
  };
  git("init", "-q");
  put("app/product.txt", "product-v1\n");
  put("futureagi/old.py", "old\n");
  put("e2e/harness.txt", "harness-v1\n");
  put("agentcc-gateway/cmd/agentcc-mcpmock/main.go", "mock-v1\n");
  git("add", "-A");
  git("commit", "-q", "-m", "product");
  const product = git("rev-parse", "HEAD");
  put("app/product.txt", "product-v2\n");
  rmSync(join(dir, "futureagi/old.py"));
  put("futureagi/new.py", "new\n");
  put("e2e/harness.txt", "harness-v2\n");
  put("bin/e2e", "#!/bin/sh\n");
  put("agentcc-gateway/cmd/agentcc-mcpmock/main.go", "mock-v2\n");
  git("add", "-A");
  git("commit", "-q", "-m", "walk head");
  const head = git("rev-parse", "HEAD");
  const read = (rel) => readFileSync(join(dir, rel), "utf8");
  const run = (...args) =>
    spawnSync("bash", [script, ...args], { cwd: dir, encoding: "utf8" });
  return { dir, git, put, read, run, product, head };
}

test("a productSha that differs is built: product paths from it, the launched harness over it", () => {
  const f = fixture();
  const r = f.run(f.head, f.product);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.deepEqual(r.stdout.trim().split("\n"), [
    `WALK_OVERLAY_FROM=${f.head}`,
    `WALK_OVERLAY_PATHS=${HARNESS.join(" ")}`,
  ]);
  assert.equal(f.git("rev-parse", "HEAD"), f.product);
  assert.equal(f.read("app/product.txt"), "product-v1\n");
  assert.ok(existsSync(join(f.dir, "futureagi/old.py")));
  assert.ok(!existsSync(join(f.dir, "futureagi/new.py")));
  assert.equal(f.read("e2e/harness.txt"), "harness-v2\n");
  assert.equal(f.read("bin/e2e"), "#!/bin/sh\n");
  assert.equal(f.read("agentcc-gateway/cmd/agentcc-mcpmock/main.go"), "mock-v2\n");
  // The overlay layout run-walks.sh and lib/walk/receipt.ts check: the product paths are
  // clean against HEAD (the product), and the harness paths are the launched commit's copy.
  const excluded = HARNESS.map((p) => `:!${p}`);
  assert.equal(f.git("status", "--porcelain", "--untracked-files=no", "--", ".", ...excluded), "");
  f.git("diff", "--quiet", f.head, "--", ...HARNESS);
});

test("a productSha whose product is HEAD's changes nothing and prints nothing", () => {
  const f = fixture();
  const r = f.run(f.head, f.head);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(r.stdout, "");
  assert.equal(f.git("rev-parse", "HEAD"), f.head);
  assert.equal(f.read("app/product.txt"), "product-v2\n");
});

test("a HEAD that is not the launched commit is refused", () => {
  const f = fixture();
  const r = f.run(f.product, f.product);
  assert.equal(r.status, 71, r.stdout + r.stderr);
  assert.equal(f.git("rev-parse", "HEAD"), f.head);
});

test("tracked changes are refused before anything moves", () => {
  const f = fixture();
  f.put("app/product.txt", "local edit\n");
  const r = f.run(f.head, f.product);
  assert.equal(r.status, 72, r.stdout + r.stderr);
  assert.equal(f.git("rev-parse", "HEAD"), f.head);
  assert.equal(f.read("app/product.txt"), "local edit\n");
});

for (const [name, sha] of [
  ["an unknown commit", "f".repeat(40)],
  ["not a full id", "abc123"],
]) {
  test(`a productSha that is ${name} is refused`, () => {
    const f = fixture();
    const r = f.run(f.head, sha);
    assert.equal(r.status, 64, r.stdout + r.stderr);
    assert.equal(f.git("rev-parse", "HEAD"), f.head);
  });
}

test("run-walks.sh checks the productSha out before it builds anything", () => {
  const src = readFileSync(runWalks, "utf8");
  const call = src.indexOf('bash e2e/walks/vm/product-overlay.sh "$EXPECT" "$PRODUCT"');
  assert.ok(call > 0, "run-walks.sh does not call product-overlay.sh");
  assert.ok(call < src.indexOf("BUILD_KEY=$EXPECT"), "the product is checked out after the build key");
  assert.ok(call < src.indexOf("OVERLAY_EXCLUDE=()"), "the product checkout runs after the overlay checks");
});
