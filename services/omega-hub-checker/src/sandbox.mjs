/**
 * Step 7, the sandbox run (SPEC §4.4): one container per job, network off, read-only root,
 * no-new-privileges, pid/memory/cpu caps, no bind mounts and no host environment. Entry code runs as
 * uid 10001 with no capabilities; the judge (PID 1 and the node that runs `omega hub check`) runs as
 * root with only CAP_SETUID and CAP_SETGID, which it needs to start entry code as 10001 and nothing
 * else (src/judge.mjs, READINESS 3.19). The nonce and the canonical bundle go in over stdin, and the
 * verdict comes back only through the judge's frame on stdout. The checker is the only thing that
 * talks to Docker.
 */
import { spawn } from "node:child_process";
import { SECRET_PATTERNS, redactText, scrubText } from "../vendor/omega-hub/src/index.mjs";
import { readUpload } from "./archive.mjs";
import { block } from "./findings.mjs";
import { packCanonical } from "./pack.mjs";
import { JUDGE_SCRIPT, JUDGE_SHELL, SETUP_EXIT, judgeInput, newNonce, readFrame } from "./judge.mjs";

export const SANDBOX_TIMEOUT_MS = 240_000;
export const LOG_LIMIT = 256 * 1024;
export const STDOUT_LIMIT = 2 * 1024 * 1024;
export class InfraError extends Error {}
const running = new Set();

/**
 * The `docker run` argv: the flags of SPEC §4.4, except that the container starts as the judge (root,
 * all capabilities dropped but CAP_SETUID and CAP_SETGID) instead of uid 10001, which every child of
 * the judge node becomes. The command is the judge shell.
 */
export function dockerArgs({ name, image, id, source = "", script = JUDGE_SCRIPT, shell = JUDGE_SHELL, network = "none" }) {
  return [
    "run", "--rm", "-i", "--name", name, "--network", network, "--read-only",
    "--tmpfs", "/tmp:rw,size=256m", "--tmpfs", "/work:rw,size=64m,uid=10001",
    "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID", "--security-opt", "no-new-privileges",
    "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
    "--env", "OMEGA_HUB_KEYLESS=1", "--env", `OMEGA_HUB_ENTRY_ID=${id}`, "--env", `OMEGA_HUB_SOURCE=${source}`,
    "--label", "org.futureagi.omega-hub.checker=1",
    image, shell, "-c", script,
  ];
}

/**
 * Only what the docker CLI needs to reach its daemon; nothing else of the checker's environment. A
 * deploy shape's runner daemon is remote and mutual TLS (deploy/hub-runner), so its TLS settings go
 * through too.
 */
export function dockerEnv(env = process.env) {
  const out = { PATH: env.PATH ?? "/usr/local/bin:/usr/bin:/bin", DOCKER_CONTEXT: env.DOCKER_CONTEXT ?? "colima" };
  for (const key of ["HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]) if (env[key]) out[key] = env[key];
  return out;
}

function exec(args, { input, timeoutMs, env, docker }) {
  return new Promise((resolve) => {
    const started = Date.now();
    const child = spawn(docker, args, { env, stdio: ["pipe", "pipe", "pipe"] });
    const chunks = [];
    const out = [];
    let size = 0;
    let outSize = 0;
    const take = (data) => {
      if (size < LOG_LIMIT * 2) chunks.push(data);
      size += data.length;
    };
    child.stdout.on("data", (data) => {
      if (outSize <= STDOUT_LIMIT) out.push(data);
      outSize += data.length;
      take(data);
    });
    child.stderr.on("data", take);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      kill(args[args.indexOf("--name") + 1], { env, docker });
    }, timeoutMs);
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ code: 127, output: String(error.message), stdout: "", stdoutBytes: 0, timedOut, ms: Date.now() - started });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, output: Buffer.concat(chunks).toString("utf8"), stdout: Buffer.concat(out).toString("utf8"), stdoutBytes: outSize, timedOut, ms: Date.now() - started });
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input ?? Buffer.alloc(0));
  });
}

export function kill(name, { env = dockerEnv(), docker = "docker" } = {}) {
  if (!name) return;
  spawn(docker, ["kill", name], { env, stdio: "ignore" }).on("error", () => {});
}

/** Kill every container this process started (on SIGTERM/SIGINT). */
export function killAll() {
  for (const name of running) kill(name);
}

export async function runContainer({ name, image, id, source, bundle, script, shell, network, timeoutMs = SANDBOX_TIMEOUT_MS, env = process.env, docker = "docker" }) {
  running.add(name);
  try {
    return await exec(dockerArgs({ name, image, id, source, script, shell, network }), { input: bundle, timeoutMs, env: dockerEnv(env), docker });
  } finally {
    running.delete(name);
  }
}

export function redactLog(text, { patterns = [] } = {}) {
  let out = scrubText(text, { tmpDirs: ["/work"] });
  out = redactText(out, patterns);
  for (const [, pattern] of SECRET_PATTERNS) out = out.replace(new RegExp(pattern.source, `${pattern.flags.replace("g", "")}g`), "<redacted>");
  return out.length > LOG_LIMIT ? `${out.slice(0, LOG_LIMIT - 40)}\n… (log cut at 256 KiB)\n` : out;
}

/**
 * The canonical bundle without the entry's `test` file (R2 makes `test` name a file in the entry). In
 * the demo run nothing of the entry executes before the CLI reads demo.cast, so R15 compares the
 * recording that was uploaded, not one a test rewrote at run time.
 */
export function withoutTest(bundle) {
  const entry = new Map();
  const sourceFiles = new Map();
  for (const member of readUpload(bundle).members) {
    if (member.type !== "file") continue;
    if (member.name.startsWith("entry/")) entry.set(member.name.slice(6), member.data);
    else if (member.name.startsWith("source/")) sourceFiles.set(member.name.slice(7), member.data);
  }
  const manifest = JSON.parse(entry.get("omega.hub.json")?.toString("utf8") ?? "{}");
  if (typeof manifest.test === "string") entry.delete(manifest.test);
  return packCanonical(entry, sourceFiles).bundle;
}

/** One container through the judge, keeping the findings of `rules` only. */
async function judgedRun({ name, rules, image, id, source, bundle, manifest, timeoutMs, env, docker }) {
  const nonce = newNonce();
  const run = await runContainer({ name, image, id, source, bundle: judgeInput(nonce, bundle), timeoutMs, env, docker });
  const log = redactLog(run.output.replaceAll(nonce, "<nonce>"), { patterns: manifest?.demo?.redact ?? [] });
  const findings = [];
  const judge = (message) => findings.push(block("sandbox-run", "judge", "", message, { fix: "let omega hub check finish: do not signal, trace or write to other processes" }));
  if ([125, 126, 127].includes(run.code) && !run.timedOut) throw new InfraError(`docker run failed (${run.code}): ${log.slice(-400)}`);
  if (run.timedOut) {
    findings.push(block("sandbox-run", "timeout", "", `the ${rules.join(" and ")} run did not finish in ${Math.round((timeoutMs ?? SANDBOX_TIMEOUT_MS) / 1000)} s`, { fix: "make the keyless test and run finish quickly" }));
    return { findings, log, ms: run.ms };
  }
  const frame = run.stdoutBytes > STDOUT_LIMIT ? { ok: false, framed: true, reason: "the run's stdout is larger than the judge ever writes" } : readFrame(run.stdout, nonce);
  if (!frame.framed && SETUP_EXIT[run.code]) throw new InfraError(`${SETUP_EXIT[run.code]} (exit ${run.code}): ${log.slice(-400)}`);
  if (!frame.ok) {
    judge(`the sandbox run did not give one trusted check result: ${frame.reason} (exit ${run.code}). Log tail: ${log.slice(-300)}`);
  } else if (run.code !== frame.code) {
    judge(`the container exited with ${run.code}, but the judge recorded ${frame.code}`);
  } else {
    const entry = frame.verdict.entries.find((item) => item?.id === id);
    if (!entry || !Array.isArray(entry.findings)) judge(`the check result has no entry ${id}`);
    else {
      for (const item of entry.findings.filter((f) => rules.includes(f?.rule))) {
        findings.push(block("sandbox-run", item.rule, String(item.file ?? "").replace(/^hub\/[^/]+\//, ""), redactLog(String(item.message ?? "")), { fix: item.fix }));
      }
    }
  }
  return { findings, log, ms: run.ms };
}

/**
 * Run R14 and R15 for one entry through the judge, in two containers: the test run keeps R14 (the
 * keyless test is the first entry code to run), the demo run keeps R15 (the same bundle without the
 * test file, so demo.cast is read before any entry code runs). Returns the step and the redacted log;
 * throws InfraError when Docker or the runner failed. The verdict is the judge's frame and nothing
 * else: a missing, doubled or forged frame, a killed CLI, or an exit code that disagrees with the
 * result blocks with rule `judge`.
 */
export async function sandboxStep({ jobId, image, id, source, bundle, manifest, timeoutMs, env, docker }) {
  const name = `ohc-${String(jobId).replace(/-/g, "").slice(0, 8)}`;
  const runs = [
    { label: "test run (R14)", name, rules: ["R14"], bundle },
    { label: "demo run (R15)", name: `${name}-demo`, rules: ["R15"], bundle: withoutTest(bundle) },
  ];
  const findings = [];
  const logs = [];
  let ms = 0;
  for (const run of runs) {
    const result = await judgedRun({ ...run, image, id, source, manifest, timeoutMs, env, docker });
    findings.push(...result.findings);
    logs.push(`== ${run.label} ==\n${result.log}`);
    ms += result.ms;
  }
  return { step: { id: "sandbox-run", status: findings.length ? "fail" : "pass", ms, findings, network: "none", container: name, judge: "omega-hub-judge/v1" }, log: logs.join("\n") };
}
