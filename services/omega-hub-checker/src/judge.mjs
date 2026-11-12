/**
 * The judge inside the runner (SPEC §4.4, §13 "a submission tampers with its own judge"; READINESS 3.19).
 *
 * The verdict must not come from anything entry code can write or read, so the judge runs under
 * another uid. PID 1, the judge shell, and the node that runs `omega hub check` run as root with only
 * CAP_SETUID and CAP_SETGID (src/sandbox.mjs: --user 0:0 --cap-drop ALL --cap-add SETUID --cap-add
 * SETGID --security-opt no-new-privileges). The judge node `--require`s runner/judge/children.cjs,
 * which starts every child of the CLI, and so all entry code, as uid and gid 10001; a setuid from 0
 * empties every capability set. Entry code therefore cannot open the judge's /proc/<pid>/fd, environ
 * or mem, signal it, or attach to it, under runc and gVisor alike. (An earlier judge ran as uid 10001
 * from execute-only binaries, to be non-dumpable; gVisor loads only binaries the caller may read.)
 * The bundle is unpacked as uid 10001 too, so the entry owns its own files. Only PID 1 writes the
 * container's stdout:
 *
 *   omega-hub-judge/v1 begin <nonce>
 *   <the CLI's stdout: one omega-hub-check/v1 line>
 *   omega-hub-judge/v1 end <nonce> <the CLI's exit code>
 *
 * The nonce is 16 random bytes the checker sends as the first line of stdin, ahead of the bundle; the
 * judge shell reads it into a variable that is neither exported nor in its argv. `readFrame` accepts
 * exactly that frame and nothing else on stdout, and the CLI's exit code must agree with the verdict.
 */
import { randomBytes } from "node:crypto";

export const JUDGE_SHELL = "/bin/sh";
export const JUDGE_NODE = "/usr/local/bin/node";
export const JUDGE_DIR = "/opt/omega-hub-judge";
export const JUDGE_PRELOAD = `${JUDGE_DIR}/children.cjs`;
/** Root-owned and read-only: the judge node's HOME while Node computes its global module folders. */
export const JUDGE_HOME = `${JUDGE_DIR}/home`;
export const ENTRY_UID = 10001;
/** The runner image label org.futureagi.omega-hub.judge: "2" is this judge; "1" was the execute-only one. */
export const JUDGE_LABEL = "2";
/** CapEff of the judge: CAP_SETGID (6) and CAP_SETUID (7), nothing else. */
export const JUDGE_CAPS = "00000000000000c0";
export const FRAME = "omega-hub-judge/v1";
export const CHECK_SCHEMA = "omega-hub-check/v1";

/** Exit codes of the judge's own set-up, before the CLI starts: infrastructure, not the entry. */
export const SETUP_EXIT = {
  70: "the judge did not receive its nonce",
  71: "the runner is not hardened: the judge must run as root with exactly CAP_SETUID and CAP_SETGID and no-new-privileges, with setpriv (rebuild the runner image, check the docker run flags)",
  72: "the judge could not unpack the canonical bundle",
};

/**
 * The judge's preconditions, with shell builtins and coreutils only: uid 0, CapEff exactly
 * CAP_SETUID|CAP_SETGID, no-new-privileges wherever the kernel's status file reports it (the docker run
 * flags always set it, and the runner image has no setuid binaries either), setpriv and the preload.
 */
export const JUDGE_HARDENED = [
  'OHC_CAP=; OHC_NNP=',
  `while IFS=':\t ' read -r OHC_K OHC_V; do case "$OHC_K" in CapEff) OHC_CAP=$OHC_V;; NoNewPrivs) OHC_NNP=$OHC_V;; esac; done </proc/$$/status`,
  `[ "$(id -u):$(id -g)" = 0:0 ] && [ "$OHC_CAP" = ${JUDGE_CAPS} ] && { [ -z "$OHC_NNP" ] || [ "$OHC_NNP" = 1 ]; } `
    + `&& command -v setpriv >/dev/null && [ -r ${JUDGE_PRELOAD} ] || exit 71`,
].join("\n");

/** Run a command as the entries' uid and gid, with no supplementary groups. */
export const AS_ENTRY = `setpriv --reuid=${ENTRY_UID} --regid=${ENTRY_UID} --clear-groups --`;

/** The judge node: root, the preload on its argv (never NODE_OPTIONS), HOME on a read-only path, no inspector. */
export const judgeNode = (args) => `HOME=${JUDGE_HOME} ${JUDGE_NODE} --disable-sigusr1 --require ${JUDGE_PRELOAD} ${args}`;

export const JUDGE_SCRIPT = [
  'IFS= read -r OHC_NONCE || exit 70',
  'case "$OHC_NONCE" in *[!0-9a-f]*|"") exit 70;; esac',
  '[ "${#OHC_NONCE}" -eq 32 ] || exit 70',
  JUDGE_HARDENED,
  `${AS_ENTRY} ${JUDGE_SHELL} -c 'mkdir -p /work/hub && tar -xzf - -C /work && mv /work/entry "/work/hub/$OMEGA_HUB_ENTRY_ID" `
    + `&& { [ -z "$OMEGA_HUB_SOURCE" ] || mv /work/source "/work/$OMEGA_HUB_SOURCE"; }' 1>&2 || exit 72`,
  `OHC_OUT=$(${judgeNode('/omega/packages/cli/dist/index.js hub check "$OMEGA_HUB_ENTRY_ID" --json')})`,
  'OHC_CODE=$?',
  `printf '${FRAME} begin %s\\n%s\\n${FRAME} end %s %s\\n' "$OHC_NONCE" "$OHC_OUT" "$OHC_NONCE" "$OHC_CODE"`,
  'exit "$OHC_CODE"',
].join("\n");

/**
 * The judge preflight's script (src/preflight.mjs): the judge's preconditions, then the judge node runs
 * runner/judge/probe.cjs, which starts children the way the CLI does and reports what they can reach.
 */
export const PREFLIGHT_SCRIPT = [
  JUDGE_HARDENED,
  `echo "judge uid=$(id -u) gid=$(id -g) caps=$OHC_CAP nnp=\${OHC_NNP:-unreported}"`,
  judgeNode(`${JUDGE_DIR}/probe.cjs`),
].join("\n");

export const newNonce = () => randomBytes(16).toString("hex");

/** The judge's stdin: the nonce line, then the canonical bundle. */
export const judgeInput = (nonce, bundle) => Buffer.concat([Buffer.from(`${nonce}\n`), bundle ?? Buffer.alloc(0)]);

const refuse = (reason, extra = {}) => ({ ok: false, reason, ...extra });

/**
 * Read the judge's frame from the container's stdout (never stderr). Returns
 * `{ ok: true, code, verdict }`, or `{ ok: false, reason, framed }` where `framed` says whether a frame
 * was present at all (no frame plus a SETUP_EXIT code is an infrastructure failure).
 */
export function readFrame(stdout, nonce) {
  const lines = String(stdout ?? "").split("\n");
  if (lines.at(-1) === "") lines.pop();
  const begin = `${FRAME} begin ${nonce}`;
  const endPattern = new RegExp(`^${FRAME.replace("/", "\\/")} end ${nonce} (\\d{1,3})$`);
  const begins = [];
  const ends = [];
  lines.forEach((line, index) => {
    if (line === begin) begins.push(index);
    else if (endPattern.test(line)) ends.push(index);
    else if (line.startsWith(FRAME)) begins.push(-1 - index); // a frame line with another nonce: forged
  });
  const framed = begins.length > 0 || ends.length > 0;
  if (begins.some((index) => index < 0)) return refuse("a judge line carries another nonce", { framed });
  if (begins.length !== 1 || ends.length !== 1) return refuse(`expected one judge frame, found ${begins.length} begin and ${ends.length} end lines`, { framed });
  const [from, to] = [begins[0], ends[0]];
  if (from > to) return refuse("the judge frame ends before it begins", { framed });
  if (lines.some((line, index) => (index < from || index > to) && line.trim() !== "")) {
    return refuse("something other than the judge wrote to the run's stdout", { framed });
  }
  const code = Number(endPattern.exec(lines[to])[1]);
  const verdicts = lines.slice(from + 1, to).filter((line) => line.trim().startsWith("{") && line.includes(CHECK_SCHEMA));
  if (code !== 0 && code !== 1) return refuse(`the omega CLI exited with code ${code}${code > 128 ? ` (killed by signal ${code - 128})` : ""} before it gave a result`, { framed, code });
  if (verdicts.length !== 1) return refuse(`expected one check result inside the frame, found ${verdicts.length}`, { framed, code });
  let verdict;
  try {
    verdict = JSON.parse(verdicts[0]);
  } catch {
    return refuse("the check result does not parse", { framed, code });
  }
  if (verdict?.schema !== CHECK_SCHEMA || typeof verdict.ok !== "boolean" || !Array.isArray(verdict.entries)) {
    return refuse("the check result is not omega-hub-check/v1", { framed, code });
  }
  if ((code === 0) !== verdict.ok) return refuse(`the CLI exited with ${code} but its result says ok=${verdict.ok}`, { framed, code });
  return { ok: true, code, verdict };
}
