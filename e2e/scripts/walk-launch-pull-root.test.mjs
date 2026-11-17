// The walk launcher's pulls must land outside every git worktree: the cloud rig refuses
// a --to inside one, but only when the job is pulled, after the walk has run. Before this,
// launch.sh defaulted to <worktree>/e2e/walk-landed, so every default launch lost its pull.
// No VM is created here: omega-cloud and host-guard are /bin/false.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const launch = join(root, "e2e/walks/launch/launch.sh");
const groups = JSON.parse(
  readFileSync(join(root, "e2e/walks/launch/groups.json"), "utf8"),
);
const group = Object.keys(groups).find((g) => !g.startsWith("_"));
const head = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {
  encoding: "utf8",
}).trim();

function env(extra = {}) {
  const e = {
    ...process.env,
    WALK_SHA: head,
    OMEGA_CLOUD: "/bin/false",
    HOST_GUARD: "/bin/false",
  };
  // Only this launch's own settings: none inherited from an enclosing walk or E2E run.
  for (const name of Object.keys(e))
    if (name.startsWith("E2E_") || ["WALK_LANE", "WALK_STRICT", "WALK_PULL_ROOT"].includes(name))
      delete e[name];
  return { ...e, ...extra };
}
const inside = (p) => {
  const rel = relative(root, p);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
};

test("by default a launch lands its pulls outside the worktree", () => {
  const argv = execFileSync("bash", [launch, group, "print"], {
    encoding: "utf8",
    env: env(),
  })
    .trim()
    .split("\n");
  const to = argv[argv.indexOf("--to") + 1];
  assert.ok(isAbsolute(to), `--to ${to} is not absolute`);
  assert.ok(!inside(to), `--to ${to} is inside the worktree ${root}`);
});

for (const [name, value] of [
  ["inside the worktree", join(root, "e2e/walk-landed")],
  ["relative", "walk-landed"],
]) {
  test(`a pull root ${name} is refused before anything launches`, () => {
    const r = spawnSync("bash", [launch, group, "plan"], {
      encoding: "utf8",
      env: env({ WALK_PULL_ROOT: value }),
    });
    assert.equal(r.status, 64, r.stdout + r.stderr);
    assert.match(r.stdout, /WALK_PULL_ROOT/);
  });
}
