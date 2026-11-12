/** Checker configuration, from the environment only. */
import { readFileSync } from "node:fs";
import { hostname } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const CHECKER_VERSION = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8")).version;
export const VENDORED_HUB = readFileSync(path.join(ROOT, "vendor", "omega-hub", "VENDORED_FROM"), "utf8").trim();

export function loadConfig(env = process.env) {
  const token = env.OMEGA_HUB_CHECKER_TOKEN;
  if (!token) throw new Error("OMEGA_HUB_CHECKER_TOKEN is required");
  const image = env.OMEGA_HUB_RUNNER_IMAGE;
  if (!image) throw new Error("OMEGA_HUB_RUNNER_IMAGE is required (build it with runner/build.sh)");
  return {
    baseUrl: env.OMEGA_HUB_URL ?? "http://127.0.0.1:8740",
    token,
    checkerId: env.OMEGA_HUB_CHECKER_ID ?? `${hostname()}-${process.pid}`,
    image,
    pollMs: Number(env.OMEGA_HUB_POLL_MS ?? 5000),
    timeoutMs: Number(env.OMEGA_HUB_SANDBOX_TIMEOUT_MS ?? 240_000),
    heartbeatMs: Number(env.OMEGA_HUB_HEARTBEAT_MS ?? 5 * 60_000),
    healthPort: Number(env.OMEGA_HUB_CHECKER_HEALTH_PORT ?? 0),
    healthHost: env.OMEGA_HUB_CHECKER_HEALTH_HOST ?? "0.0.0.0",
    healthTimeoutMs: Number(env.OMEGA_HUB_CHECKER_HEALTH_TIMEOUT_MS ?? Math.max(Number(env.OMEGA_HUB_POLL_MS ?? 5000) * 3, 30_000)),
    // Busy health: a job with no claim or accepted heartbeat for a whole lease has lost its lease.
    jobHealthTimeoutMs: Number(env.OMEGA_HUB_CHECKER_JOB_HEALTH_TIMEOUT_MS ?? 15 * 60_000),
  };
}
