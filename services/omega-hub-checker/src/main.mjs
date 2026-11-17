#!/usr/bin/env node
/**
 * The checker worker (SPEC §4.1): pull a job, judge it, post the result, repeat. It holds no database
 * or storage credentials, only the shared checker secret, and it is the only process with Docker.
 *
 *   OMEGA_HUB_CHECKER_TOKEN=… OMEGA_HUB_RUNNER_IMAGE=omega-hub-runner:881b05b node src/main.mjs [--once|--drain]
 */
import { spawnSync } from "node:child_process";
import { registryClient } from "./client.mjs";
import { CHECKER_VERSION, VENDORED_HUB, loadConfig } from "./config.mjs";
import { checkSubmission } from "./pipeline.mjs";
import { JUDGE_LABEL } from "./judge.mjs";
import { InfraError, dockerEnv, killAll, sandboxStep } from "./sandbox.mjs";
import { startHealthServer } from "./health.mjs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The runner's digest and Omega commit. Refuses an image built without this judge (src/judge.mjs): the
 * label org.futureagi.omega-hub.judge must be JUDGE_LABEL. An image with the execute-only judge ("1")
 * cannot run under gVisor and runs its judge as the entries' uid.
 */
export function runnerIdentity(image, env = process.env, docker = "docker") {
  const format = '{{.Id}} {{index .Config.Labels "org.futureagi.omega-hub.omega-commit"}} {{index .Config.Labels "org.futureagi.omega-hub.judge"}}';
  const out = spawnSync(docker, ["image", "inspect", image, "--format", format], { env: dockerEnv(env), encoding: "utf8" });
  if (out.status !== 0) throw new Error(`runner image ${image} is not built: ${String(out.stderr ?? "").trim()}`);
  const [id, commit, judge] = out.stdout.trim().split(" ");
  if (judge !== JUDGE_LABEL) {
    const why = judge ? `has judge ${judge}, not ${JUDGE_LABEL} (label org.futureagi.omega-hub.judge)` : "has no judge (label org.futureagi.omega-hub.judge)";
    throw new Error(`runner image ${image} ${why}; rebuild it with runner/build.sh`);
  }
  return { runnerImage: id, omegaCommit: commit ?? "" };
}

export async function processJob(job, { client, config, identity, log = console.log, onProgress = () => {} }) {
  const checker = { version: CHECKER_VERSION, omegaCommit: identity.omegaCommit, runnerImage: identity.runnerImage, vendoredHub: VENDORED_HUB };
  // A heartbeat the registry accepted renews the lease, and is the progress /healthz judges busy work by.
  const beat = setInterval(
    () => client.heartbeat(job).then(onProgress, (error) => log(`heartbeat failed: ${error.message}`)),
    config.heartbeatMs,
  );
  try {
    const raw = await client.bytes(job.bundleUrl);
    const registry = { context: (body) => client.context(job, body), previousBundle: () => client.bytes(job.previousBundleUrl) };
    const sandbox = (args) => sandboxStep({ ...args, image: config.image, timeoutMs: config.timeoutMs });
    const { report, bundle, log: runLog } = await checkSubmission({ job, raw, registry, sandbox, checker });
    const answer = await client.result(job, { report, bundle, log: runLog });
    log(`job ${job.job}: ${report.outcome} -> ${answer.submission ?? answer.status}`);
    return report;
  } catch (error) {
    const infra = error instanceof InfraError;
    log(`job ${job.job}: ${infra ? "infrastructure error" : "checker error"}: ${error.message}`);
    const report = { schema: "omega-hub-check-report/v1", job: job.job, submission: job.submission, checker, steps: [], outcome: "error", error: String(error.message).slice(0, 500) };
    await client.result(job, { report }).catch((inner) => log(`could not report the error: ${inner.message}`));
    return report;
  } finally {
    clearInterval(beat);
  }
}

async function main(argv) {
  const config = loadConfig();
  const identity = runnerIdentity(config.image);
  const client = registryClient(config);
  const state = {
    startedAt: Date.now(),
    claims: 0,
    completed: 0,
    activeJobs: 0,
    lastPollAt: Date.now(),
    lastClaimAt: null,
    lastProgressAt: null,
    lastCompletionAt: null,
    healthTimeoutMs: config.healthTimeoutMs,
    jobHealthTimeoutMs: config.jobHealthTimeoutMs,
    stopping: false,
  };
  const healthServer = startHealthServer(config.healthPort, state, config.healthHost);
  let stopping = false;
  const stop = () => {
    stopping = true;
    state.stopping = true;
    killAll();
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  process.on("exit", () => healthServer?.close());
  const once = argv.includes("--once");
  const drain = argv.includes("--drain");
  console.log(`omega-hub-checker ${CHECKER_VERSION}: ${config.baseUrl}, runner ${config.image} (${identity.omegaCommit.slice(0, 9)})`);
  while (!stopping) {
    state.lastPollAt = Date.now();
    const job = await client.claim().catch((error) => {
      console.log(`claim failed: ${error.message}`);
      return undefined;
    });
    if (job) {
      state.claims += 1;
      state.activeJobs += 1;
      state.lastClaimAt = Date.now();
      state.lastProgressAt = state.lastClaimAt;
      try {
        await processJob(job, { client, config, identity, onProgress: () => { state.lastProgressAt = Date.now(); } });
        state.completed += 1;
        state.lastCompletionAt = Date.now();
      } finally {
        state.activeJobs -= 1;
      }
      if (once) break;
      continue;
    }
    if (once || (drain && job === null)) break;
    await sleep(config.pollMs);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
