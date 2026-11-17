// What a walk launch may LAND, end to end without a VM. The
// command and the pull patterns come from `walks/launch/launch.sh gate print`
// (whatever the launcher builds); the group's own job is replaced by a fake
// that writes evidence, and the command runs in a scratch repo root (its
// e2e/scripts and e2e/walks link to this tree). Then the pull patterns are
// evaluated over that root, as the rig would. Controls: a clean run lands its
// evidence; a refused file never lands; a quarantine that cannot move the file
// (a read-only directory) and an auditor that crashes (a --require preload)
// land only the exit code and a value-free refusal report.
// Every planted value is generated at run time.
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const head = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
  encoding: "utf8",
  env: { ...process.env, GIT_NO_LAZY_FETCH: "1" },
}).trim();
const password = () =>
  `Pw-${randomBytes(5).toString("hex")}!${randomBytes(3).toString("base64url")}`;

/** The VM command (without the group's job) and the pull patterns the launcher builds. */
function launcher() {
  const env = { ...process.env, WALK_SHA: head };
  delete env.WALK_LANE;
  delete env.WALK_STRICT;
  delete env.WALK_LAND_ID;
  const argv = execFileSync(
    "bash",
    [path.join(repo, "e2e/walks/launch/launch.sh"), "gate", "print"],
    {
      encoding: "utf8",
      env,
    },
  )
    .trim()
    .split("\n");
  const command = argv.slice(argv.indexOf("--") + 1);
  const job = ["bash", "e2e/walks/vm/gate.sh", head];
  assert.deepEqual(
    command.slice(-3),
    job,
    "the gate group's job ends the VM command",
  );
  return {
    prefix: command.slice(0, -3),
    pulls: argv.flatMap((a, i) => (a === "--pull" ? [argv[i + 1]] : [])),
  };
}

function globRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      re += glob[i + 2] === "/" ? "(?:.*/)?" : ".*";
      i += glob[i + 2] === "/" ? 2 : 1;
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

/** Every file under `root` that a pull pattern matches (links are not followed). */
function pulled(root, pulls) {
  const res = pulls.map(globRegExp);
  const out = [];
  const walk = (dir) => {
    let names;
    try {
      names = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const d of names) {
      const p = path.join(dir, d.name);
      if (d.isSymbolicLink()) continue;
      if (d.isDirectory()) walk(p);
      else {
        const r = path.relative(root, p).split(path.sep).join("/");
        if (res.some((re) => re.test(r))) out.push(r);
      }
    }
  };
  walk(root);
  return out.sort();
}

/** Run the launcher's VM command with `fake` (a bash script) as the job, in a scratch root. */
function land(fake, { preload } = {}) {
  const { prefix, pulls } = launcher();
  const root = mkdtempSync(path.join(tmpdir(), "walk-landing-"));
  mkdirSync(path.join(root, "e2e"));
  symlinkSync(path.join(repo, "e2e/scripts"), path.join(root, "e2e/scripts"));
  symlinkSync(path.join(repo, "e2e/walks"), path.join(root, "e2e/walks"));
  const home = mkdtempSync(path.join(tmpdir(), "walk-landing-home-"));
  const env = { ...process.env, HOME: home };
  if (preload) {
    const file = path.join(home, "preload.cjs");
    writeFileSync(file, preload);
    env.NODE_OPTIONS =
      `${process.env.NODE_OPTIONS ?? ""} --require ${file}`.trim();
  }
  const run = spawnSync(prefix[0], [...prefix.slice(1), "bash", "-c", fake], {
    cwd: root,
    env,
    encoding: "utf8",
  });
  const files = pulled(root, pulls);
  const read = (suffix) => {
    const hit = files.find((f) => f.endsWith(suffix));
    return hit === undefined
      ? null
      : readFileSync(path.join(root, hit), "utf8");
  };
  return { run, root, home, pulls, files, read };
}

const write = (rel, content) =>
  `mkdir -p "$(dirname '${rel}')" && printf '%s' '${content}' > '${rel}'`;

function assertNoValue(result, value) {
  for (const f of result.files)
    assert.equal(
      readFileSync(path.join(result.root, f), "utf8").includes(value),
      false,
      `a pulled file carries the planted value (${f})`,
    );
  assert.equal(
    `${result.run.stdout}${result.run.stderr}`.includes(value),
    false,
    "command output carries it",
  );
}

function assertLandingOnly(result) {
  assert.ok(result.pulls.length > 0);
  const landing = result.pulls[0].split("/").slice(0, 3).join("/");
  assert.match(
    landing,
    /^e2e\/walk-landed\/[A-Za-z0-9][A-Za-z0-9._-]*$/,
    "pulls come from a landing directory",
  );
  for (const p of result.pulls)
    assert.ok(p.startsWith(`${landing}/`), `pull ${p} outside ${landing}`);
  for (const f of result.files)
    assert.ok(
      f.startsWith(`${landing}/`),
      `${f} would be pulled from outside the landing`,
    );
  return landing;
}

test("landing: a clean run lands its evidence and exit code, from the landing directory only", () => {
  const r = land(
    [
      write("e2e/walk-results/g1/steps.txt", "walk rc=0"),
      write("e2e/walk-results/g1/summary.json", '{"ok":true}'),
    ].join(" && "),
  );
  const landing = assertLandingOnly(r);
  assert.equal(r.run.status, 0, r.run.stdout + r.run.stderr);
  assert.deepEqual(r.files, [
    `${landing}/e2e/walk-results/command/command-exit.txt`,
    `${landing}/e2e/walk-results/command/evidence-audit.json`,
    `${landing}/e2e/walk-results/g1/steps.txt`,
    `${landing}/e2e/walk-results/g1/summary.json`,
  ]);
  assert.equal(r.read("command-exit.txt").trim(), "0");
  assert.equal(
    JSON.parse(r.read("command/evidence-audit.json")).refused,
    false,
  );
});

test("landing: a refused file never lands; the clean rest lands with exit 86 and a value-free report", () => {
  const value = password();
  const r = land(
    [
      write("e2e/walk-results/g1/steps.txt", "walk rc=0"),
      write("e2e/walk-results/g1/summary.json", `{"password":"${value}"}`),
    ].join(" && "),
  );
  const landing = assertLandingOnly(r);
  assertNoValue(r, value);
  assert.equal(r.run.status, 1);
  assert.equal(r.read("command-exit.txt").trim(), "86");
  assert.ok(r.files.includes(`${landing}/e2e/walk-results/g1/steps.txt`));
  assert.ok(!r.files.some((f) => f.endsWith("g1/summary.json")));
  const report = JSON.parse(r.read("command/evidence-audit.json"));
  assert.equal(report.refused, true);
  assert.deepEqual(report.quarantined, ["e2e/walk-results/g1/summary.json"]);
  assert.equal(
    existsSync(path.join(r.root, "e2e/walk-results/g1/summary.json")),
    false,
    "quarantined out of the tree",
  );
});

test("landing: a quarantine that cannot move the file lands only the exit code and the verified report", () => {
  const value = password();
  const r = land(
    [
      write("e2e/walk-results/g1/steps.txt", "walk rc=0"),
      write("e2e/walk-results/g1/summary.json", `{"password":"${value}"}`),
      "chmod 0500 e2e/walk-results/g1",
    ].join(" && "),
  );
  try {
    assert.equal(
      existsSync(path.join(r.root, "e2e/walk-results/g1/summary.json")),
      true,
      "control premise: the read-only directory kept the file (is this run root?)",
    );
    const landing = assertLandingOnly(r);
    assertNoValue(r, value);
    assert.equal(r.run.status, 1);
    assert.deepEqual(r.files, [
      `${landing}/e2e/walk-results/command/command-exit.txt`,
      `${landing}/e2e/walk-results/command/evidence-audit.json`,
    ]);
    assert.equal(r.read("command-exit.txt").trim(), "87");
    const report = JSON.parse(r.read("command/evidence-audit.json"));
    assert.equal(report.refused, true);
    assert.deepEqual(report.quarantineFailed, [
      "e2e/walk-results/g1/summary.json",
    ]);
  } finally {
    chmodSync(path.join(r.root, "e2e/walk-results/g1"), 0o700);
  }
});

test("landing: an auditor that crashes lands only the exit code and a constants-only stub", () => {
  for (const [label, preload] of [
    [
      "the audit run",
      'if (process.argv.includes("--glob")) throw new Error("control: auditor crash");\n',
    ],
    ["every node process", 'throw new Error("control: node crash");\n'],
  ]) {
    const value = password();
    const r = land(
      [
        write("e2e/walk-results/g1/steps.txt", "walk rc=0"),
        write("e2e/walk-results/g1/summary.json", `{"password":"${value}"}`),
      ].join(" && "),
      { preload },
    );
    const landing = assertLandingOnly(r);
    assertNoValue(r, value);
    assert.equal(r.run.status, 1, label);
    assert.deepEqual(
      r.files,
      [
        `${landing}/e2e/walk-results/command/command-exit.txt`,
        `${landing}/e2e/walk-results/command/evidence-audit.json`,
      ],
      label,
    );
    assert.equal(r.read("command-exit.txt").trim(), "87", label);
    const stub = JSON.parse(r.read("command/evidence-audit.json"));
    assert.equal(stub.refused, true, label);
    assert.match(stub.reason, /^[a-z-]+$/, label);
  }
});

test("landing: a launch id that already landed (a reused VM) refuses to land again", () => {
  const { prefix } = launcher();
  const root = mkdtempSync(path.join(tmpdir(), "walk-landing-again-"));
  mkdirSync(path.join(root, "e2e"));
  symlinkSync(path.join(repo, "e2e/scripts"), path.join(root, "e2e/scripts"));
  symlinkSync(path.join(repo, "e2e/walks"), path.join(root, "e2e/walks"));
  const home = mkdtempSync(path.join(tmpdir(), "walk-landing-home-"));
  const first = spawnSync(prefix[0], [...prefix.slice(1), "true"], {
    cwd: root,
    env: { ...process.env, HOME: home },
    encoding: "utf8",
  });
  assert.equal(first.status, 0, first.stdout + first.stderr);
  const again = spawnSync(prefix[0], [...prefix.slice(1), "true"], {
    cwd: root,
    env: { ...process.env, HOME: home },
    encoding: "utf8",
  });
  assert.equal(again.status, 1);
});
