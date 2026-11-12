#!/usr/bin/env node
/**
 * The judge preflight (READINESS 3.19): one container of the runner image, started with exactly the
 * docker flags of every upload's run (src/sandbox.mjs dockerArgs), proves on THIS runner daemon that
 * the judge loads and keeps entry code out of reach. The judge shell checks its own preconditions
 * (src/judge.mjs JUDGE_HARDENED), then the judge node runs runner/judge/probe.cjs, which starts a child
 * the way `omega hub check` starts entry code. The child must run as uid and gid 10001 with every
 * capability set empty, and must be refused setuid(0), signals to the judge and PID 1, the judge's
 * environ, mem and fd/1, PID 1's fd/1, writes to the judge's files, and every directory of the
 * judge's PATH.
 *
 *   node src/preflight.mjs        exit 0: ok; 65: the judge would not hold; 69: the daemon did not run it
 *
 * bin/start.sh runs it before the worker in every deploy shape; test/sandbox.test.mjs runs it against
 * the real runner image.
 */
import { randomBytes } from "node:crypto";
import { ENTRY_UID, JUDGE_CAPS, JUDGE_HOME, JUDGE_SHELL, PREFLIGHT_SCRIPT, SETUP_EXIT } from "./judge.mjs";
import { runContainer } from "./sandbox.mjs";

const NO_CAPS = "0000000000000000";
const REFUSED = ["EACCES", "EPERM"];
const READ_ONLY = ["EROFS", "EACCES", "EPERM"];
const TAG = "omega-hub-judge-preflight/v1 ";

/** What is wrong with a probe report; an empty list means the judge holds. */
export function judgeProblems(report) {
  const problems = [];
  const want = (ok, what) => {
    if (!ok) problems.push(what);
  };
  const { judge = {}, entry = {}, syncUid } = report ?? {};
  want(judge.uid === 0 && judge.gid === 0, `the judge node runs as ${judge.uid}:${judge.gid}, not root`);
  want(Array.isArray(judge.globalPaths) && judge.globalPaths.every((dir) => !/^\/(tmp|work)(\/|$)/.test(dir)) && judge.globalPaths.some((dir) => dir.startsWith(JUDGE_HOME)),
    `the judge node's global module folders are not under ${JUDGE_HOME}: ${JSON.stringify(judge.globalPaths)}`);
  if (entry.error) return [...problems, `the entry-side child did not report: ${entry.error}`];
  want(entry.uid === ENTRY_UID && entry.gid === ENTRY_UID, `entry code runs as ${entry.uid}:${entry.gid}, not ${ENTRY_UID}:${ENTRY_UID}`);
  want(Array.isArray(entry.groups) && entry.groups.every((group) => group === ENTRY_UID), `entry code keeps supplementary groups ${JSON.stringify(entry.groups)}`);
  for (const set of ["capEff", "capPrm", "capInh"]) want(entry[set] === NO_CAPS, `entry code has ${set} ${entry[set]}`);
  want(entry.capAmb === null || entry.capAmb === NO_CAPS, `entry code has capAmb ${entry.capAmb}`);
  for (const probe of ["setuid0", "signalJudge", "signalPid1", "judgeEnviron", "judgeMem", "judgeFd1", "pid1Fd1"]) {
    want(REFUSED.includes(entry[probe]), `entry code was not refused ${probe}: ${entry[probe]}`);
  }
  for (const probe of ["writePreload", "writeNode", "writeCli"]) want(READ_ONLY.includes(entry[probe]), `entry code was not refused ${probe}: ${entry[probe]}`);
  want(Array.isArray(entry.writablePath) && entry.writablePath.length === 0, `entry code can write directories of the judge's PATH: ${JSON.stringify(entry.writablePath)}`);
  want(String(syncUid) === String(ENTRY_UID), `a synchronous child of the judge runs as ${syncUid}`);
  return problems;
}

/** Run the preflight container; `{ ok, code, reason, judgeLine, report }`, where code is 0, 65 or 69. */
export async function judgePreflight({ image, env = process.env, docker = "docker", timeoutMs = 60_000 }) {
  const name = `ohc-preflight-${randomBytes(4).toString("hex")}`;
  const run = await runContainer({ name, image, id: "preflight", bundle: Buffer.alloc(0), script: PREFLIGHT_SCRIPT, shell: JUDGE_SHELL, env, docker, timeoutMs });
  const tail = run.output.trim().split("\n").slice(-3).join(" | ").slice(-500);
  if (run.timedOut) return { ok: false, code: 69, reason: `the preflight container did not finish in ${timeoutMs / 1000} s` };
  if ([125, 126, 127].includes(run.code)) return { ok: false, code: 69, reason: `the runner daemon did not run the judge (docker exit ${run.code}): ${tail}` };
  if (SETUP_EXIT[run.code]) return { ok: false, code: 65, reason: `${SETUP_EXIT[run.code]} (exit ${run.code}): ${tail}` };
  const lines = run.stdout.split("\n");
  const judgeLine = lines.find((line) => line.startsWith("judge uid=")) ?? "";
  const tagged = lines.find((line) => line.startsWith(TAG));
  if (run.code !== 0 || !tagged) return { ok: false, code: 65, reason: `the judge did not run its probe (exit ${run.code}): ${tail}`, judgeLine };
  let report;
  try {
    report = JSON.parse(tagged.slice(TAG.length));
  } catch {
    return { ok: false, code: 65, reason: "the judge's probe report does not parse", judgeLine };
  }
  if (!judgeLine.includes(`uid=0 gid=0 caps=${JUDGE_CAPS}`)) return { ok: false, code: 65, reason: `the judge shell reported '${judgeLine}'`, judgeLine, report };
  const problems = judgeProblems(report);
  return problems.length ? { ok: false, code: 65, reason: problems.join("; "), judgeLine, report } : { ok: true, code: 0, reason: "", judgeLine, report };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const image = process.env.OMEGA_HUB_RUNNER_IMAGE;
  if (!image) {
    console.error("omega-hub-checker: OMEGA_HUB_RUNNER_IMAGE is not set");
    process.exit(64);
  }
  const result = await judgePreflight({ image });
  if (result.ok) {
    console.error(`omega-hub-checker: the runner image's judge runs and holds on this daemon: ${result.judgeLine}; entry code runs as ${ENTRY_UID} with no capabilities and is refused the judge's processes and files`);
  } else {
    console.error(`omega-hub-checker: the runner image's judge would not keep entry code out of reach on this daemon, so the checker does not start: ${result.reason}`);
  }
  process.exit(result.code);
}
