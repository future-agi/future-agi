/**
 * The judge node's preload (src/judge.mjs, runner/Dockerfile): `--require`d by the node that runs
 * `omega hub check` inside the runner, never put in NODE_OPTIONS (the CLI copies its environment into
 * every child it starts).
 *
 * The judge (PID 1's shell and this node) runs as root with only CAP_SETUID and CAP_SETGID. Entry code
 * is never in this process: `omega hub check` runs every test and recording as a child
 * (packages/cli/src/hub/hub-runner.ts, spawnCapture). This preload makes every child run as the
 * entries' uid and gid 10001. A setuid from 0 to a non-zero uid empties the permitted, effective and
 * ambient capability sets, so entry code has no capability and a different uid from the judge: the
 * judge's /proc/<pid>/fd, environ and mem and its signals are closed to it, under runc and gVisor.
 *
 * Every async child goes through ChildProcess.prototype.spawn (spawn, fork, exec, execFile). The
 * synchronous calls are wrapped on the module's exports, and syncBuiltinESMExports() carries the
 * wrappers into `import { ... } from "node:child_process"`. Worker threads would run in this process,
 * as root; the CLI starts none.
 *
 * HOME: the judge starts this node with HOME on a root-owned, read-only directory, so the CommonJS
 * loader's global folders ($HOME/.node_modules, $HOME/.node_libraries, computed once before this file
 * runs) are not on the tmpfs entry code can write. Then HOME goes back to the runner's /tmp, which the
 * CLI scrubs from recordings and hands to the entry's children, as before.
 */
"use strict";
const childProcess = require("node:child_process");
const { syncBuiltinESMExports } = require("node:module");

const ENTRY_UID = 10001;
const ENTRY_HOME = "/tmp";

if (process.getuid() !== 0 || process.getgid() !== 0) {
  process.stderr.write(`omega-hub-judge: the judge node runs as ${process.getuid()}:${process.getgid()}, not root; it cannot start entry code as uid ${ENTRY_UID}\n`);
  process.exit(71);
}

/** A copy of `options` with the entries' uid and gid; whatever the caller asked for is overridden. */
const asEntry = (options) => ({ ...(options && typeof options === "object" ? options : {}), uid: ENTRY_UID, gid: ENTRY_UID });

const spawnChild = childProcess.ChildProcess.prototype.spawn;
childProcess.ChildProcess.prototype.spawn = function spawn(options) {
  if (options && typeof options === "object") {
    options.uid = ENTRY_UID;
    options.gid = ENTRY_UID;
  }
  return spawnChild.call(this, options);
};

/** spawnSync(file[, args][, options]) and execFileSync(file[, args][, options]). */
for (const name of ["spawnSync", "execFileSync"]) {
  const original = childProcess[name];
  childProcess[name] = function (file, args, options) {
    if (!Array.isArray(args) && args !== null && typeof args === "object") return original.call(this, file, [], asEntry(args));
    return original.call(this, file, args ?? [], asEntry(options));
  };
}
const execSync = childProcess.execSync;
childProcess.execSync = function (command, options) {
  return execSync.call(this, command, asEntry(options));
};
syncBuiltinESMExports();

process.env.HOME = ENTRY_HOME;
