/**
 * bin/start.sh, the image's entrypoint in every deploy shape: it refuses a runner daemon that would
 * run uploads unsandboxed, gets the runner image onto it, and only then execs the worker. A fake
 * `docker` on PATH plays the daemon (its answers come from FAKE_* variables) and a fake `node`
 * records that the worker was started, with which arguments.
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

const START = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../bin/start.sh");
const dir = mkdtempSync(path.join(tmpdir(), "ohc-start-"));
after(() => rmSync(dir, { recursive: true, force: true }));

// An empty FAKE_INFO is a daemon that does not answer. Like Docker 27's CLI (seen on the rig's
// production run: "has no 'runsc' runtime (it has: )"), \`info --format\` then still exits 0 with
// empty fields; only \`version\` fails. The fake \`node\` plays src/preflight.mjs from FAKE_PREFLIGHT
// (its own tests are in sandbox.test.mjs and preflight.test.mjs) and records the worker's start.
const DOCKER = `#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1" in
  version) [ -n "$FAKE_INFO" ] || { echo "Cannot connect to the Docker daemon at $DOCKER_HOST" >&2; exit 1; }; echo 27.5.1 ;;
  info) [ -n "$FAKE_INFO" ] || { echo "Cannot connect to the Docker daemon at $DOCKER_HOST" >&2; echo "|"; exit 0; }; echo "$FAKE_INFO" ;;
  image) [ -f "$FAKE_STATE/image" ] || { echo "No such image" >&2; exit 1; } ;;
  pull) [ "$FAKE_PULL" = ok ] || { echo "pull access denied for $3" >&2; exit 1; }; touch "$FAKE_STATE/image"; echo sha256:abc ;;
  run) printf '%s\\n' "$FAKE_KERNEL" ;;
  *) exit 2 ;;
esac
`;
const NODE = `#!/bin/sh
if [ "$1" = src/preflight.mjs ]; then
  echo "node $* DOCKER_CONTEXT=$DOCKER_CONTEXT" > "$FAKE_STATE/preflight"
  case "$FAKE_PREFLIGHT" in
    refused) echo "omega-hub-checker: the runner image's judge would not keep entry code out of reach on this daemon, so the checker does not start: entry code has capEff 00000000000000c0" >&2; exit 65 ;;
    unrun) echo "omega-hub-checker: the runner daemon did not run the judge (docker exit 125)" >&2; exit 69 ;;
    *) echo "omega-hub-checker: the runner image's judge runs and holds on this daemon: judge uid=0 gid=0 caps=00000000000000c0 nnp=1" >&2; exit 0 ;;
  esac
fi
echo "node $* DOCKER_CONTEXT=$DOCKER_CONTEXT" > "$FAKE_STATE/worker"
`;
writeFileSync(path.join(dir, "docker"), DOCKER);
writeFileSync(path.join(dir, "node"), NODE);
chmodSync(path.join(dir, "docker"), 0o755);
chmodSync(path.join(dir, "node"), 0o755);

let n = 0;
function start(env = {}, args = ["--drain"]) {
  const state = path.join(dir, `state-${n += 1}`);
  spawnSync("mkdir", ["-p", state]);
  if (env.FAKE_IMAGE_PRESENT) writeFileSync(path.join(state, "image"), "");
  const log = path.join(state, "docker.log");
  const result = spawnSync("sh", [START, ...args], {
    encoding: "utf8",
    timeout: 20_000,
    env: {
      PATH: `${dir}:/usr/bin:/bin`,
      FAKE_LOG: log,
      FAKE_STATE: state,
      DOCKER_HOST: "tcp://hub-runner:2376",
      OMEGA_HUB_CHECKER_TOKEN: "t",
      OMEGA_HUB_RUNNER_IMAGE: "futureagi/omega-hub-runner:881b05b",
      FAKE_INFO: "runsc|io.containerd.runc.v2 runc runsc ",
      FAKE_KERNEL: "4.19.0-gvisor",
      OMEGA_HUB_DAEMON_WAIT_SECONDS: "0",
      ...env,
    },
  });
  const worker = path.join(state, "worker");
  const preflight = path.join(state, "preflight");
  return {
    code: result.status,
    stderr: result.stderr,
    worker: existsSync(worker) ? readFileSync(worker, "utf8").trim() : null,
    preflight: existsSync(preflight) ? readFileSync(preflight, "utf8").trim() : null,
    calls: existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [],
  };
}

test("a gVisor runner daemon with the image: the judge preflight passes, then the worker starts, with its arguments", () => {
  const r = start({ FAKE_IMAGE_PRESENT: "1" });
  assert.equal(r.code, 0, r.stderr);
  assert.equal(r.worker, "node src/main.mjs --drain DOCKER_CONTEXT=default");
  assert.equal(r.preflight, "node src/preflight.mjs DOCKER_CONTEXT=default");
  assert.match(r.stderr, /default runtime runsc/);
  assert.match(r.stderr, /sees gVisor \(4\.19\.0-gvisor\)/);
  assert.match(r.stderr, /the runner image's judge runs and holds on this daemon: judge uid=0 gid=0 caps=00000000000000c0/);
  const runs = r.calls.filter((call) => call.startsWith("run "));
  assert.equal(runs.length, 1, "start.sh runs only the gVisor probe itself; the judge's container is the preflight's");
  for (const flag of ["--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 10001:10001"]) assert.ok(runs[0].includes(flag), `${flag} in ${runs[0]}`);
  assert.ok(!r.calls.some((call) => call.startsWith("pull")), "nothing pulled when the image is there");
});

test("an image missing from the daemon is pulled there first", () => {
  const r = start({ FAKE_PULL: "ok" });
  assert.equal(r.code, 0, r.stderr);
  assert.ok(r.calls.some((call) => call === "pull --quiet futureagi/omega-hub-runner:881b05b"));
  assert.match(r.stderr, /pulled futureagi\/omega-hub-runner:881b05b onto the runner daemon/);
  assert.ok(r.worker);
});

test("no DOCKER_HOST: refused, so the CLI never falls back to a local socket", () => {
  const r = start({ DOCKER_HOST: "" });
  assert.equal(r.code, 64);
  assert.match(r.stderr, /DOCKER_HOST is not set/);
  assert.equal(r.worker, null);
  assert.deepEqual(r.calls, []);
});

test("a daemon whose default runtime is runc: refused (the checker passes no --runtime)", () => {
  const r = start({ FAKE_IMAGE_PRESENT: "1", FAKE_INFO: "runc|io.containerd.runc.v2 runc runsc " });
  assert.equal(r.code, 65);
  assert.match(r.stderr, /runs containers on 'runc' by default, not 'runsc'/);
  assert.equal(r.worker, null);
});

test("a daemon without gVisor at all: refused", () => {
  const r = start({ FAKE_IMAGE_PRESENT: "1", FAKE_INFO: "runc|io.containerd.runc.v2 runc " });
  assert.equal(r.code, 65);
  assert.match(r.stderr, /has no 'runsc' runtime/);
});

test("a runtime named runsc that is not gVisor: refused on what a container sees", () => {
  const r = start({ FAKE_IMAGE_PRESENT: "1", FAKE_KERNEL: "7.0.0-1014-gcp" });
  assert.equal(r.code, 65);
  assert.match(r.stderr, /sees kernel '7\.0\.0-1014-gcp', not gVisor/);
  assert.equal(r.worker, null);
});

test("a runner image whose judge would not hold on this daemon: refused with the preflight's code and reason", () => {
  const refused = start({ FAKE_IMAGE_PRESENT: "1", FAKE_PREFLIGHT: "refused" });
  assert.equal(refused.code, 65);
  assert.match(refused.stderr, /judge would not keep entry code out of reach on this daemon, so the checker does not start: entry code has capEff/);
  assert.equal(refused.worker, null);
  const unrun = start({ FAKE_IMAGE_PRESENT: "1", FAKE_PREFLIGHT: "unrun" });
  assert.equal(unrun.code, 69);
  assert.match(unrun.stderr, /did not run the judge/);
  assert.equal(unrun.worker, null);
});

test("runc only with the development opt-in, and then no gVisor probe, but the judge preflight", () => {
  const refused = start({ OMEGA_HUB_RUNNER_RUNTIME: "runc", FAKE_INFO: "runc|runc " });
  assert.equal(refused.code, 64);
  assert.match(refused.stderr, /needs OMEGA_HUB_ALLOW_RUNC=1/);
  const allowed = start({ OMEGA_HUB_RUNNER_RUNTIME: "runc", OMEGA_HUB_ALLOW_RUNC: "1", FAKE_INFO: "runc|runc ", FAKE_IMAGE_PRESENT: "1" });
  assert.equal(allowed.code, 0, allowed.stderr);
  assert.ok(!allowed.calls.some((call) => call.startsWith("run ")));
  assert.ok(allowed.preflight, "the judge preflight runs under runc too");
  assert.ok(allowed.worker);
  const refused2 = start({ OMEGA_HUB_RUNNER_RUNTIME: "runc", OMEGA_HUB_ALLOW_RUNC: "1", FAKE_INFO: "runc|runc ", FAKE_IMAGE_PRESENT: "1", FAKE_PREFLIGHT: "refused" });
  assert.equal(refused2.code, 65);
  assert.equal(refused2.worker, null);
});

test("an unknown runtime name, a missing image name or token: refused before the daemon is asked", () => {
  for (const [env, why] of [
    [{ OMEGA_HUB_RUNNER_RUNTIME: "kata" }, /must be runsc or runc/],
    [{ OMEGA_HUB_RUNNER_IMAGE: "" }, /OMEGA_HUB_RUNNER_IMAGE is not set/],
    [{ OMEGA_HUB_CHECKER_TOKEN: "" }, /OMEGA_HUB_CHECKER_TOKEN is not set/],
  ]) {
    const r = start(env);
    assert.equal(r.code, 64, JSON.stringify(env));
    assert.match(r.stderr, why);
    assert.deepEqual(r.calls, []);
  }
});

test("a daemon that never answers: exit 69 naming it, although `docker info` exits 0", () => {
  const r = start({ FAKE_INFO: "" });
  assert.equal(r.code, 69, r.stderr);
  assert.match(r.stderr, /runner daemon at tcp:\/\/hub-runner:2376 did not answer within 0s: Cannot connect/);
  assert.doesNotMatch(r.stderr, /runtime/);
  assert.equal(r.worker, null);
});

test("an image that cannot be had: exit 69 with how to build it; never pulled with OMEGA_HUB_RUNNER_PULL=never", () => {
  const r = start({ FAKE_PULL: "denied" });
  assert.equal(r.code, 69);
  assert.match(r.stderr, /not on the runner daemon at tcp:\/\/hub-runner:2376 and cannot be pulled \(pull access denied/);
  assert.match(r.stderr, /runner\/build\.sh/);
  const never = start({ OMEGA_HUB_RUNNER_PULL: "never", FAKE_PULL: "ok" });
  assert.equal(never.code, 69);
  assert.ok(!never.calls.some((call) => call.startsWith("pull")));
});

test("OMEGA_HUB_RUNNER_WAIT=1 keeps waiting for the image instead of exiting", () => {
  const r = spawnSync("sh", [START], {
    encoding: "utf8",
    timeout: 3000,
    env: {
      PATH: `${dir}:/usr/bin:/bin`, FAKE_LOG: path.join(dir, "wait.log"), FAKE_STATE: dir, DOCKER_HOST: "tcp://hub-runner:2376",
      OMEGA_HUB_CHECKER_TOKEN: "t", OMEGA_HUB_RUNNER_IMAGE: "futureagi/omega-hub-runner:x", FAKE_INFO: "runsc|runsc ",
      FAKE_PULL: "denied", OMEGA_HUB_RUNNER_WAIT: "1",
    },
  });
  assert.equal(r.signal, "SIGTERM", "still waiting when the test stopped it");
  assert.match(r.stderr, /trying again in 60s \(uploads wait in 'received' until then\)/);
});
