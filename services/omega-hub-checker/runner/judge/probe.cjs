/**
 * The judge preflight's probe (src/preflight.mjs; READINESS 3.19). It runs where `omega hub check`
 * runs: on the judge node, as root, after children.cjs. It starts one child the way the CLI starts
 * entry code (spawn of process.execPath) and one synchronous child, and prints one line:
 *
 *   omega-hub-judge-preflight/v1 {"judge":{...},"entry":{...},"syncUid":"10001"}
 *
 * `entry` is what that child, standing in for entry code, could do to the judge: its own uid, groups
 * and capability sets, setuid(0), signal 0 to the judge node and PID 1, the judge's environ, mem and
 * fd/1, PID 1's fd/1, writes to the judge's preload, its node and the CLI, and which directories of
 * the judge's PATH (handed to the child unchanged) it could write. src/preflight.mjs decides; this
 * file only reports.
 */
"use strict";
const { execFileSync, spawn } = require("node:child_process");
const Module = require("node:module");

const ENTRY = `
const fs = require("node:fs");
const status = Object.fromEntries(fs.readFileSync("/proc/self/status", "utf8").split("\\n").filter(Boolean)
  .map((line) => [line.slice(0, line.indexOf(":")), line.slice(line.indexOf(":") + 1).trim()]));
const outcome = (fn) => { try { fn(); return "allowed"; } catch (error) { return error.code || "error"; } };
const judge = process.ppid;
process.stdout.write(JSON.stringify({
  uid: process.getuid(), gid: process.getgid(), groups: process.getgroups(),
  capEff: status.CapEff, capPrm: status.CapPrm, capInh: status.CapInh, capAmb: status.CapAmb ?? null, noNewPrivs: status.NoNewPrivs ?? null,
  setuid0: outcome(() => process.setuid(0)),
  signalJudge: outcome(() => process.kill(judge, 0)),
  signalPid1: outcome(() => process.kill(1, 0)),
  judgeEnviron: outcome(() => fs.readFileSync("/proc/" + judge + "/environ")),
  judgeMem: outcome(() => fs.readFileSync("/proc/" + judge + "/mem")),
  judgeFd1: outcome(() => fs.writeFileSync("/proc/" + judge + "/fd/1", "")),
  pid1Fd1: outcome(() => fs.writeFileSync("/proc/1/fd/1", "")),
  writePreload: outcome(() => fs.appendFileSync("/opt/omega-hub-judge/children.cjs", "")),
  writeNode: outcome(() => fs.appendFileSync(process.execPath, "")),
  writeCli: outcome(() => fs.appendFileSync("/omega/packages/cli/dist/index.js", "")),
  writablePath: (process.env.PATH || "").split(":").filter((dir) => outcome(() => fs.accessSync(dir || ".", fs.constants.W_OK)) === "allowed"),
}));
`;

const self = {
  uid: process.getuid(),
  gid: process.getgid(),
  home: process.env.HOME,
  path: process.env.PATH,
  globalPaths: Module.globalPaths,
  execPath: process.execPath,
};

const child = spawn(process.execPath, ["-e", ENTRY], { stdio: ["ignore", "pipe", "pipe"], env: { PATH: process.env.PATH, HOME: process.env.HOME } });
const out = [];
const err = [];
child.stdout.on("data", (data) => out.push(data));
child.stderr.on("data", (data) => err.push(data));
child.on("error", (error) => finish({ error: error.code || error.message }));
child.on("close", (code) => {
  let entry;
  try {
    entry = JSON.parse(Buffer.concat(out).toString("utf8"));
  } catch {
    entry = { error: `exit ${code}: ${Buffer.concat(err).toString("utf8").slice(-300)}` };
  }
  finish(entry);
});

let done = false;
function finish(entry) {
  if (done) return;
  done = true;
  let syncUid;
  try {
    syncUid = execFileSync(process.execPath, ["-e", "process.stdout.write(String(process.getuid()))"], { encoding: "utf8" });
  } catch (error) {
    syncUid = `error ${error.code || error.message}`;
  }
  process.stdout.write(`omega-hub-judge-preflight/v1 ${JSON.stringify({ judge: self, entry, syncUid })}\n`);
}
