import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import process from "node:process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const DEFAULT_DB_CONTAINERS = ["ws2-postgres", "futureagi-ws2-postgres-1"];
const DEFAULT_REDIS_CONTAINERS = ["ws2-redis", "futureagi-ws2-redis-1"];

const args = parseArgs(process.argv.slice(2));
const startedAt = Date.now();
const apiBase = normalizeBaseUrl(
  process.env.API_BASE || "http://localhost:8003",
);

const checks = [];

try {
  checks.push(await checkApiReachability(apiBase));
  checks.push(await checkAuthentication(apiBase));
  checks.push(
    ...(await checkDockerContainers({
      envName: "API_JOURNEY_DB_CONTAINER",
      fallbackNames: DEFAULT_DB_CONTAINERS,
      service: "postgres",
    })),
  );
  checks.push(
    ...(await checkContainerDiskAvailability({
      envName: "API_JOURNEY_DB_CONTAINER",
      fallbackNames: DEFAULT_DB_CONTAINERS,
      service: "postgres",
      paths: ["/", "/var/lib/postgresql/data"],
    })),
  );
  checks.push(
    ...(await checkDockerContainers({
      envName: "API_JOURNEY_REDIS_CONTAINER",
      fallbackNames: DEFAULT_REDIS_CONTAINERS,
      service: "redis",
    })),
  );
  checks.push(
    ...(await checkContainerDiskAvailability({
      envName: "API_JOURNEY_REDIS_CONTAINER",
      fallbackNames: DEFAULT_REDIS_CONTAINERS,
      service: "redis",
      paths: ["/", "/data"],
    })),
  );
  checks.push(
    ...(await checkRedisWriteHealth({
      envName: "API_JOURNEY_REDIS_CONTAINER",
      fallbackNames: DEFAULT_REDIS_CONTAINERS,
    })),
  );
  checks.push(await checkDockerDiskUsage());
} catch (error) {
  checks.push({
    name: "preflight_unhandled_error",
    status: "failed",
    error: error.message,
  });
}

const failed = checks.filter((check) => check.status === "failed");
const warnings = checks.filter((check) => check.status === "warning");
const summary = {
  status: failed.length ? "failed" : "passed",
  api_base: apiBase,
  elapsed_ms: Date.now() - startedAt,
  checks,
  failed: failed.length,
  warnings: warnings.length,
};

if (args.jsonPath) {
  await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
}

console.log(JSON.stringify(summary, null, 2));
if (failed.length) process.exitCode = 1;

async function checkApiReachability(base) {
  try {
    const response = await fetchWithTimeout(`${base}/accounts/user-info/`, {
      method: "GET",
    });
    await parseResponseBody(response);
    return {
      name: "api_reachable",
      status: response.status === 401 ? "passed" : "warning",
      http_status: response.status,
      detail:
        response.status === 401
          ? "API is reachable and authentication is enforced."
          : "API responded with a non-401 status before authentication.",
    };
  } catch (error) {
    return {
      name: "api_reachable",
      status: "failed",
      error: error.message,
    };
  }
}

async function checkAuthentication(base) {
  if (process.env.FUTURE_AGI_ACCESS_TOKEN) {
    return checkAccessToken(base, process.env.FUTURE_AGI_ACCESS_TOKEN);
  }

  const email = process.env.FUTURE_AGI_EMAIL;
  const password = process.env.FUTURE_AGI_PASSWORD;
  if (!email || !password) {
    return {
      name: "auth_context",
      status: "failed",
      detail:
        "Set FUTURE_AGI_ACCESS_TOKEN or FUTURE_AGI_EMAIL/FUTURE_AGI_PASSWORD before running API journeys.",
    };
  }

  const tokenResult = await requestToken(base, {
    email,
    password,
    includeRecaptcha: true,
  });

  const bodyMessage = String(
    tokenResult.body?.message || tokenResult.body?.detail || "",
  );
  if (
    tokenResult.status === 400 &&
    bodyMessage.includes("recaptcha-response")
  ) {
    const retry = await requestToken(base, {
      email,
      password,
      includeRecaptcha: false,
    });
    return tokenResponseToCheck(retry);
  }

  return tokenResponseToCheck(tokenResult);
}

async function checkAccessToken(base, accessToken) {
  try {
    const response = await fetchWithTimeout(`${base}/accounts/user-info/`, {
      method: "GET",
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const body = await parseResponseBody(response);
    return {
      name: "auth_context",
      status: response.ok && body?.status !== false ? "passed" : "failed",
      http_status: response.status,
      detail:
        response.ok && body?.status !== false
          ? "Access token is accepted by /accounts/user-info/."
          : "Access token was rejected by /accounts/user-info/.",
      body: response.ok ? undefined : summarizeBody(body),
    };
  } catch (error) {
    return {
      name: "auth_context",
      status: "failed",
      error: error.message,
    };
  }
}

async function requestToken(base, { email, password, includeRecaptcha }) {
  try {
    const payload = {
      email,
      password,
      remember_me: true,
      ...(includeRecaptcha
        ? { "recaptcha-response": "api-journey-local-test" }
        : {}),
    };
    const response = await fetchWithTimeout(`${base}/accounts/token/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await parseResponseBody(response);
    return { status: response.status, ok: response.ok, body };
  } catch (error) {
    return { status: 0, ok: false, body: null, error: error.message };
  }
}

function tokenResponseToCheck({ status, ok, body, error }) {
  const hasAccess = Boolean(body?.access);
  return {
    name: "auth_context",
    status: ok && hasAccess ? "passed" : "failed",
    http_status: status || undefined,
    detail:
      ok && hasAccess
        ? "Login returned an access token."
        : "Login did not return an access token.",
    error,
    body: ok && hasAccess ? undefined : summarizeBody(body),
  };
}

async function checkDockerContainers({ envName, fallbackNames, service }) {
  const explicit = splitEnvList(process.env[envName]);
  const names = explicit.length ? explicit : fallbackNames;
  const uniqueNames = [...new Set(names.filter(Boolean))];
  const results = [];

  for (const name of uniqueNames) {
    results.push(
      await checkDockerContainer({
        name,
        service,
        explicit: explicit.length > 0,
      }),
    );
  }

  return results;
}

async function checkDockerContainer({ name, service, explicit }) {
  try {
    const { stdout } = await execFileAsync("docker", [
      "inspect",
      name,
      "--format",
      "{{json .State}}",
    ]);
    const state = JSON.parse(stdout.trim());
    const status =
      state.Status === "running" ? "passed" : explicit ? "failed" : "warning";
    const result = {
      name: `${service}_container:${name}`,
      status,
      container_status: state.Status,
      health: state.Health?.Status || null,
      error: state.Error || "",
    };

    if (status !== "passed") {
      result.recent_log_summary = await summarizeDockerLogs(name);
    }

    return result;
  } catch (error) {
    return {
      name: `${service}_container:${name}`,
      status: explicit ? "failed" : "warning",
      error: error.message,
    };
  }
}

async function checkRedisWriteHealth({ envName, fallbackNames }) {
  const explicit = splitEnvList(process.env[envName]);
  const names = [
    ...new Set((explicit.length ? explicit : fallbackNames).filter(Boolean)),
  ];
  const results = [];

  for (const name of names) {
    const state = await loadDockerContainerState(name);
    if (!state?.exists || state.status !== "running") continue;
    results.push(await checkRedisContainerWriteHealth(name));
  }

  return results;
}

async function checkContainerDiskAvailability({
  envName,
  fallbackNames,
  service,
  paths,
}) {
  const explicit = splitEnvList(process.env[envName]);
  const names = [
    ...new Set((explicit.length ? explicit : fallbackNames).filter(Boolean)),
  ];
  const results = [];

  for (const name of names) {
    const state = await loadDockerContainerState(name);
    if (!state?.exists || state.status !== "running") continue;
    results.push(await checkContainerDisk(name, service, paths));
  }

  return results;
}

async function checkContainerDisk(name, service, paths) {
  try {
    const { stdout } = await execFileAsync("docker", [
      "exec",
      name,
      "sh",
      "-lc",
      `df -Pk ${paths.map(shellQuote).join(" ")} 2>/dev/null || df -Pk`,
    ]);
    const rows = parseDfRows(stdout);
    const worstCapacity = rows.reduce(
      (max, row) => Math.max(max, row.capacity_percent),
      0,
    );
    const lowestAvailable = rows.reduce(
      (min, row) => Math.min(min, row.available_kib),
      Number.POSITIVE_INFINITY,
    );
    const status =
      lowestAvailable <= 0 || worstCapacity >= 100
        ? "failed"
        : lowestAvailable < 1024 * 1024 || worstCapacity >= 95
          ? "warning"
          : "passed";

    return {
      name: `${service}_disk:${name}`,
      status,
      filesystems: rows,
      detail:
        status === "passed"
          ? "Container filesystems have enough free space for API journey probes."
          : "Container filesystem free space is low; database, cache, and auth writes may fail.",
    };
  } catch (error) {
    return {
      name: `${service}_disk:${name}`,
      status: "warning",
      error: error.message,
    };
  }
}

async function loadDockerContainerState(name) {
  try {
    const { stdout } = await execFileAsync("docker", [
      "inspect",
      name,
      "--format",
      "{{json .State}}",
    ]);
    const state = JSON.parse(stdout.trim());
    return { exists: true, status: state.Status };
  } catch {
    return { exists: false, status: "" };
  }
}

function parseDfRows(output) {
  const rows = [];
  const seenMounts = new Set();
  for (const line of output.split(/\r?\n/).slice(1)) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 6) continue;
    const [filesystem, blocks, used, available, capacity, ...mountParts] =
      parts;
    const mounted_on = mountParts.join(" ");
    if (seenMounts.has(mounted_on)) continue;
    seenMounts.add(mounted_on);
    rows.push({
      filesystem,
      blocks_kib: Number(blocks),
      used_kib: Number(used),
      available_kib: Number(available),
      capacity_percent: Number(String(capacity).replace("%", "")),
      mounted_on,
    });
  }
  return rows;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

async function checkRedisContainerWriteHealth(name) {
  const key = `api_journey_preflight:${Date.now()}:${Math.random()
    .toString(36)
    .slice(2)}`;
  try {
    const { stdout } = await execFileAsync("docker", [
      "exec",
      name,
      "redis-cli",
      "SET",
      key,
      "ok",
      "EX",
      "30",
    ]);
    const output = stdout.trim();
    const passed = output === "OK";
    if (passed) {
      await execFileAsync("docker", ["exec", name, "redis-cli", "DEL", key]);
    }
    return {
      name: `redis_write:${name}`,
      status: passed ? "passed" : "failed",
      detail: passed
        ? "Redis accepted a short-lived write probe."
        : "Redis did not accept a write probe; cache-backed auth and journeys may fail.",
      output: passed ? undefined : output.slice(0, 1000),
      recent_log_summary: passed ? undefined : await summarizeDockerLogs(name),
    };
  } catch (error) {
    return {
      name: `redis_write:${name}`,
      status: "failed",
      error: error.message,
      recent_log_summary: await summarizeDockerLogs(name),
    };
  }
}

async function summarizeDockerLogs(name) {
  try {
    const { stdout, stderr } = await execFileAsync("docker", [
      "logs",
      "--tail=80",
      name,
    ]);
    const text = `${stdout}\n${stderr}`;
    const flags = [];
    if (/No space left on device/i.test(text)) flags.push("no_space_left");
    if (/MISCONF/i.test(text)) flags.push("redis_misconf");
    if (/FATAL/i.test(text)) flags.push("fatal");
    if (/Name or service not known/i.test(text))
      flags.push("dns_or_service_lookup");
    return {
      flags,
      tail: text
        .split(/\r?\n/)
        .filter(Boolean)
        .slice(-8)
        .join("\n")
        .slice(0, 2000),
    };
  } catch (error) {
    return { error: error.message };
  }
}

async function checkDockerDiskUsage() {
  try {
    const { stdout } = await execFileAsync("docker", [
      "system",
      "df",
      "--format",
      "{{json .}}",
    ]);
    const rows = stdout
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line));
    const volumeRow = rows.find((row) => row.Type === "Local Volumes");
    const imageRow = rows.find((row) => row.Type === "Images");
    const topVolumes = await loadDockerVolumeRows();
    const inactiveVolumeCandidates = topVolumes
      .filter((row) => Number(row.links) === 0)
      .slice(0, 8);

    const reclaimableVolumeBytes = parseHumanSize(
      String(volumeRow?.Reclaimable || "").split(/\s+/)[0],
    );
    const activeVolumeBytes = parseHumanSize(volumeRow?.Size);
    const status =
      reclaimableVolumeBytes > 5 * 1024 ** 3 ||
      activeVolumeBytes > 250 * 1024 ** 3
        ? "warning"
        : "passed";

    return {
      name: "docker_disk",
      status,
      local_volumes: volumeRow || null,
      images: imageRow || null,
      inactive_volume_reclaim_candidates: inactiveVolumeCandidates,
      top_volumes: topVolumes.slice(0, 8),
      detail:
        status === "warning"
          ? "Docker disk pressure is high; free space before treating DB-backed journey failures as product regressions."
          : "Docker disk usage is below the preflight warning threshold.",
    };
  } catch (error) {
    return {
      name: "docker_disk",
      status: "warning",
      error: error.message,
    };
  }
}

async function loadDockerVolumeRows() {
  const { stdout } = await execFileAsync("docker", ["system", "df", "-v"]);
  const lines = stdout.split(/\r?\n/);
  const start = lines.findIndex((line) =>
    line.startsWith("Local Volumes space usage:"),
  );
  if (start < 0) return [];

  const rows = [];
  for (const line of lines.slice(start + 1)) {
    if (!line.trim()) continue;
    if (line.startsWith("Build cache usage:")) break;
    if (line.startsWith("VOLUME NAME")) continue;

    const parts = line.trim().split(/\s+/);
    if (parts.length < 3) continue;
    const [name, links, size] = parts;
    if (name === "CACHE" || name === "Local") continue;
    rows.push({
      name,
      links: Number(links),
      size,
      size_bytes: parseHumanSize(size),
    });
  }

  return rows.sort((a, b) => b.size_bytes - a.size_bytes);
}

function parseHumanSize(value) {
  const match = String(value || "")
    .trim()
    .match(/^([\d.]+)\s*([kmgtp]?b?)$/i);
  if (!match) return 0;
  const amount = Number(match[1]);
  if (!Number.isFinite(amount)) return 0;
  const unit = match[2].toLowerCase();
  const multiplier = unit.startsWith("p")
    ? 1024 ** 5
    : unit.startsWith("t")
      ? 1024 ** 4
      : unit.startsWith("g")
        ? 1024 ** 3
        : unit.startsWith("m")
          ? 1024 ** 2
          : unit.startsWith("k")
            ? 1024
            : 1;
  return Math.round(amount * multiplier);
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    Number(process.env.API_JOURNEY_PREFLIGHT_TIMEOUT_MS || 8000),
  );
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function parseResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function summarizeBody(body) {
  if (!body) return body;
  if (typeof body === "string") return body.slice(0, 500);
  const safe = { ...body };
  delete safe.access;
  delete safe.refresh;
  delete safe.token;
  return JSON.stringify(safe).slice(0, 1000);
}

function splitEnvList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function parseArgs(argv) {
  const parsed = { jsonPath: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--json") {
      parsed.jsonPath = argv[++index] || "";
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return parsed;
}
