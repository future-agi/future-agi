import assert from "node:assert/strict";
import { test } from "node:test";
import { loadConfig } from "../src/config.mjs";
import { createHealthServer, healthy } from "../src/health.mjs";
import { processJob } from "../src/main.mjs";

const ENV = { OMEGA_HUB_CHECKER_TOKEN: "t", OMEGA_HUB_RUNNER_IMAGE: "omega-hub-runner:test" };

async function get(state, path) {
  const server = createHealthServer(state);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fetch(`http://127.0.0.1:${port}${path}`);
  } finally {
    server.close();
  }
}

function idle(overrides = {}) {
  return { startedAt: Date.now() - 1000, claims: 0, completed: 0, activeJobs: 0, lastPollAt: Date.now(), lastClaimAt: null,
    lastProgressAt: null, lastCompletionAt: null, ...overrides };
}

test("health server reports checker claims and active work", async () => {
  const now = Date.now();
  const health = await get(idle({ claims: 2, completed: 1, activeJobs: 1, lastClaimAt: now - 200, lastProgressAt: now - 200 }), "/healthz");
  assert.equal(health.status, 200);
  assert.deepEqual((await health.json()).claims, 2);
  const metrics = await get(idle({ activeJobs: 1 }), "/metrics");
  assert.match(await metrics.text(), /omega_hub_checker_active_jobs 1/);
});

test("health server goes unhealthy when the idle polling loop is wedged", async () => {
  const health = await get(idle({ lastPollAt: Date.now() - 2_000, healthTimeoutMs: 100 }), "/healthz");
  assert.equal(health.status, 503);
  assert.equal((await health.json()).status, "unhealthy");
});

test("a busy check longer than the poll timeout stays healthy with the shipped defaults", async () => {
  // The verifier's reproduction: one job claimed 40 s ago, the loop parked on it since.
  const config = loadConfig(ENV);
  const claimedAt = Date.now() - 40_000;
  const state = idle({ activeJobs: 1, claims: 1, lastPollAt: claimedAt, lastClaimAt: claimedAt, lastProgressAt: claimedAt,
    healthTimeoutMs: config.healthTimeoutMs, jobHealthTimeoutMs: config.jobHealthTimeoutMs });
  assert.equal(config.healthTimeoutMs, 30_000);
  assert.ok(config.jobHealthTimeoutMs >= config.timeoutMs, "busy health must outlast one sandbox step");
  const health = await get(state, "/healthz");
  assert.equal(health.status, 200);
  assert.equal((await health.json()).status, "ok");
});

test("a busy job with no accepted heartbeat for a whole lease goes unhealthy", () => {
  const now = Date.now();
  const stale = idle({ activeJobs: 1, lastPollAt: now - 16 * 60_000, lastClaimAt: now - 16 * 60_000, lastProgressAt: now - 16 * 60_000 });
  assert.equal(healthy(stale, now), false);
  assert.equal(healthy({ ...stale, lastProgressAt: now - 60_000 }, now), true);
  assert.equal(healthy({ ...stale, stopping: true, lastProgressAt: now }, now), false);
});

test("an accepted heartbeat counts as progress; a refused one does not", async () => {
  for (const accepted of [true, false]) {
    let progress = 0;
    let release;
    const client = {
      heartbeat: () => (accepted ? Promise.resolve() : Promise.reject(new Error("HTTP 409 lease lost"))),
      bytes: () => new Promise((_, reject) => { release = () => reject(new Error("download stopped by the test")); }),
      result: async () => ({ status: "error" }),
    };
    const running = processJob({ job: "j1", submission: "s1", bundleUrl: "/b" }, {
      client, config: { heartbeatMs: 10, image: "i", timeoutMs: 1000 }, identity: { omegaCommit: "c", runnerImage: "r" },
      log: () => {}, onProgress: () => { progress += 1; },
    });
    await new Promise((resolve) => setTimeout(resolve, 80));
    release();
    const report = await running;
    assert.equal(report.outcome, "error");
    if (accepted) assert.ok(progress >= 2, `expected heartbeats to record progress, got ${progress}`);
    else assert.equal(progress, 0);
  }
});
