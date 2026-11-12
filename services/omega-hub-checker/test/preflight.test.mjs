/**
 * The judge's uid split without Docker (READINESS 3.19): the preload runner/judge/children.cjs, the
 * preflight's verdict on a probe report (src/preflight.mjs judgeProblems), and judgePreflight against a
 * fake docker. The same preflight against the real runner image, on runc and on gVisor, is in
 * sandbox.test.mjs.
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { JUDGE_HOME, PREFLIGHT_SCRIPT } from "../src/judge.mjs";
import { judgePreflight, judgeProblems } from "../src/preflight.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const PRELOAD = path.join(here, "..", "runner", "judge", "children.cjs");
const dir = mkdtempSync(path.join(tmpdir(), "ohc-preflight-"));
after(() => rmSync(dir, { recursive: true, force: true }));

test("the preload refuses to run anywhere but in a root judge node", { skip: process.getuid() === 0 ? "this test runs as root" : false }, () => {
  const run = spawnSync(process.execPath, ["--require", PRELOAD, "-e", "console.log('entry code ran')"], { encoding: "utf8" });
  assert.equal(run.status, 71);
  assert.match(run.stderr, /the judge node runs as \d+:\d+, not root; it cannot start entry code as uid 10001/);
  assert.doesNotMatch(run.stdout, /entry code ran/);
});

test("every child_process call of a judge node, async and sync, ESM and CommonJS, carries uid and gid 10001; HOME goes back to /tmp", () => {
  // A stand-in root: getuid/getgid answer 0, and recorders take the place of the real spawns, so this
  // shows what the preload passes on without starting anything.
  const shim = path.join(dir, "shim.cjs");
  writeFileSync(shim, `
process.getuid = () => 0;
process.getgid = () => 0;
const cp = require("node:child_process");
globalThis.seen = [];
cp.ChildProcess.prototype.spawn = function (options) { seen.push({ via: "ChildProcess", file: options.file, uid: options.uid, gid: options.gid }); return 0; };
for (const name of ["spawnSync", "execFileSync", "execSync"]) cp[name] = (...args) => { seen.push({ via: name, args }); return Buffer.alloc(0); };
`);
  const script = `
import { spawn, fork, spawnSync, execFileSync, execSync } from "node:child_process";
import { createRequire } from "node:module";
const cjs = createRequire(import.meta.url)("node:child_process");
spawn("/usr/local/bin/node", ["--test", "main.test.mjs"], { cwd: "/work", uid: 0, gid: 0 });
spawn("/usr/local/bin/node");
fork("/omega/x.mjs", [], { silent: true });
spawnSync("git", ["status"], { cwd: "/work", uid: 0 });
spawnSync("git", { cwd: "/work" });
execFileSync("git", ["log"]);
cjs.execFileSync("git");
execSync("id -u", { encoding: "utf8" });
cjs.execSync("id -u");
console.log(JSON.stringify({ seen: globalThis.seen, home: process.env.HOME }));
`;
  const run = spawnSync(process.execPath, ["--require", shim, "--require", PRELOAD, "--input-type=module", "-e", script], {
    encoding: "utf8", env: { PATH: process.env.PATH, HOME: "/opt/omega-hub-judge/home" },
  });
  assert.equal(run.status, 0, run.stderr);
  const { seen, home } = JSON.parse(run.stdout.trim().split("\n").at(-1));
  assert.equal(home, "/tmp");
  const asyncCalls = seen.filter((call) => call.via === "ChildProcess");
  assert.equal(asyncCalls.length, 3, JSON.stringify(seen));
  for (const call of asyncCalls) assert.deepEqual([call.uid, call.gid], [10001, 10001], JSON.stringify(call));
  const optionsOf = (call) => (call.via === "execSync" ? call.args[1] : call.args[2]);
  const syncCalls = seen.filter((call) => call.via !== "ChildProcess");
  assert.equal(syncCalls.length, 6, JSON.stringify(seen));
  for (const call of syncCalls) assert.deepEqual([optionsOf(call).uid, optionsOf(call).gid], [10001, 10001], JSON.stringify(call));
  // The argument shapes survive: an options object in the args position, and the caller's own options.
  assert.deepEqual(syncCalls[1].args.slice(0, 2), ["git", []]);
  assert.equal(syncCalls[1].args[2].cwd, "/work");
  assert.deepEqual(syncCalls[0].args[1], ["status"]);
  assert.equal(syncCalls[4].args[1].encoding, "utf8");
});

const GOOD = {
  judge: { uid: 0, gid: 0, home: "/tmp", globalPaths: [`${JUDGE_HOME}/.node_modules`, `${JUDGE_HOME}/.node_libraries`, "/usr/local/lib/node"] },
  entry: {
    uid: 10001, gid: 10001, groups: [10001], capEff: "0000000000000000", capPrm: "0000000000000000", capInh: "0000000000000000", capAmb: "0000000000000000",
    noNewPrivs: "1", setuid0: "EPERM", signalJudge: "EPERM", signalPid1: "EPERM", judgeEnviron: "EACCES", judgeMem: "EACCES", judgeFd1: "EACCES",
    pid1Fd1: "EACCES", writePreload: "EROFS", writeNode: "EROFS", writeCli: "EROFS", writablePath: [],
  },
  syncUid: "10001",
};
const withEntry = (patch) => ({ ...GOOD, entry: { ...GOOD.entry, ...patch } });

test("a report where the judge holds has no problems; gVisor's status file without CapAmb is fine", () => {
  assert.deepEqual(judgeProblems(GOOD), []);
  assert.deepEqual(judgeProblems(withEntry({ capAmb: null, noNewPrivs: null })), []);
});

test("each way the judge could fail to hold is named", () => {
  const cases = [
    [withEntry({ uid: 0 }), /entry code runs as 0:10001/],
    [withEntry({ capEff: "00000000000000c0" }), /entry code has capEff 00000000000000c0/],
    [withEntry({ capAmb: "00000000000000c0" }), /capAmb/],
    [withEntry({ groups: [0, 10001] }), /supplementary groups \[0,10001\]/],
    [withEntry({ setuid0: "allowed" }), /not refused setuid0: allowed/],
    [withEntry({ signalJudge: "allowed" }), /signalJudge/],
    [withEntry({ judgeFd1: "allowed" }), /judgeFd1/],
    [withEntry({ judgeMem: "EIO" }), /judgeMem: EIO/],
    [withEntry({ pid1Fd1: "allowed" }), /pid1Fd1/],
    [withEntry({ writePreload: "allowed" }), /writePreload/],
    [withEntry({ writablePath: ["/tmp/bin"] }), /can write directories of the judge's PATH: \["\/tmp\/bin"\]/],
    [withEntry({ writablePath: undefined }), /can write directories of the judge's PATH/],
    [{ ...GOOD, syncUid: "0" }, /a synchronous child of the judge runs as 0/],
    [{ ...GOOD, judge: { ...GOOD.judge, uid: 10001 } }, /the judge node runs as 10001:0, not root/],
    [{ ...GOOD, judge: { ...GOOD.judge, globalPaths: ["/tmp/.node_modules", "/usr/local/lib/node"] } }, /global module folders are not under/],
    [{ ...GOOD, entry: { error: "exit 1: EPERM" } }, /the entry-side child did not report: exit 1: EPERM/],
  ];
  for (const [report, why] of cases) assert.match(judgeProblems(report).join("; "), why);
});

function fakeDocker(name, body) {
  const file = path.join(dir, name);
  writeFileSync(file, `#!/bin/sh\ncat >/dev/null\n${body}\n`);
  chmodSync(file, 0o755);
  return file;
}

test("judgePreflight runs exactly an upload's docker flags with the preflight script, and passes a holding judge", async () => {
  const argsFile = path.join(dir, "args");
  const report = JSON.stringify(GOOD);
  const docker = fakeDocker("good", `printf '%s\\n' "$@" > ${argsFile}\necho 'judge uid=0 gid=0 caps=00000000000000c0 nnp=1'\necho 'omega-hub-judge-preflight/v1 ${report}'`);
  const result = await judgePreflight({ image: "omega-hub-runner:test", docker, env: { PATH: process.env.PATH } });
  assert.equal(result.ok, true, result.reason);
  assert.equal(result.code, 0);
  const args = (await import("node:fs")).readFileSync(argsFile, "utf8").trim().split("\n");
  for (const flag of ["--user", "0:0", "--cap-add", "SETUID", "SETGID", "--network", "none", "--read-only", "no-new-privileges"]) assert.ok(args.includes(flag), flag);
  assert.equal(args.at(-1), PREFLIGHT_SCRIPT.split("\n").at(-1));
});

test("judgePreflight: a judge that does not hold is 65, a daemon that does not run it is 69", async () => {
  const bad = JSON.stringify(withEntry({ capEff: "00000000000000c0" }));
  const leaky = await judgePreflight({ image: "x", env: {}, docker: fakeDocker("leaky", `echo 'judge uid=0 gid=0 caps=00000000000000c0 nnp=1'\necho 'omega-hub-judge-preflight/v1 ${bad}'`) });
  assert.deepEqual([leaky.ok, leaky.code], [false, 65]);
  assert.match(leaky.reason, /capEff/);
  const unhardened = await judgePreflight({ image: "x", env: {}, docker: fakeDocker("unhardened", "exit 71") });
  assert.deepEqual([unhardened.code], [65]);
  assert.match(unhardened.reason, /not hardened/);
  const loadless = await judgePreflight({ image: "x", env: {}, docker: fakeDocker("loadless", "echo 'failed to load /bin/sh: permission denied' >&2; exit 126") });
  assert.equal(loadless.code, 69);
  assert.match(loadless.reason, /did not run the judge \(docker exit 126\): failed to load/);
  const silent = await judgePreflight({ image: "x", env: {}, docker: fakeDocker("silent", "echo 'judge uid=0 gid=0 caps=00000000000000c0 nnp=1'") });
  assert.equal(silent.code, 65);
  assert.match(silent.reason, /did not run its probe/);
  const wrongCaps = await judgePreflight({ image: "x", env: {}, docker: fakeDocker("wrongcaps", `echo 'judge uid=0 gid=0 caps=000001ffffffffff nnp=1'\necho 'omega-hub-judge-preflight/v1 ${JSON.stringify(GOOD)}'`) });
  assert.equal(wrongCaps.code, 65);
  assert.match(wrongCaps.reason, /the judge shell reported 'judge uid=0 gid=0 caps=000001ffffffffff/);
});
