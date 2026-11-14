/* eslint-disable no-console */
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import process from "node:process";
import { promisify } from "node:util";
import { readCachedTokens, writeCachedTokens } from "./lib/token-cache.mjs";

const execFileAsync = promisify(execFile);

const DEFAULT_BACKEND_CONTAINERS = [
  "futureagi-ws2-backend-1",
  "ws2-backend-temp-api",
];
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
  checks.push(await checkApiHealth(apiBase));
  if (!args.publicOnly) {
    checks.push(await checkAuthentication(apiBase));
    checks.push(await checkHostDiskAvailability());
    checks.push(
      ...(await checkDockerContainers({
        envName: "API_JOURNEY_BACKEND_CONTAINER",
        fallbackNames: DEFAULT_BACKEND_CONTAINERS,
        service: "backend",
      })),
    );
    checks.push(
      ...(await checkContainerDiskAvailability({
        envName: "API_JOURNEY_BACKEND_CONTAINER",
        fallbackNames: DEFAULT_BACKEND_CONTAINERS,
        service: "backend",
        paths: ["/", "/app", "/tmp"],
      })),
    );
    checks.push(
      ...(await checkDockerContainers({
        envName: "API_JOURNEY_DB_CONTAINER",
        fallbackNames: DEFAULT_DB_CONTAINERS,
        service: "postgres",
      })),
    );
    checks.push(
      ...(await checkPostgresVolumeIntegrity({
        envName: "API_JOURNEY_DB_CONTAINER",
        fallbackNames: DEFAULT_DB_CONTAINERS,
      })),
    );
    checks.push(
      ...(await checkPostgresQueryHealth({
        envName: "API_JOURNEY_DB_CONTAINER",
        fallbackNames: DEFAULT_DB_CONTAINERS,
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
  }
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
  mode: args.publicOnly ? "public" : "full",
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
    const body = await parseResponseBody(response);
    if (response.status >= 500) {
      return {
        name: "api_reachable",
        status: "failed",
        http_status: response.status,
        detail:
          "API returned a 5xx response before authentication; route-level journey failures are likely local runtime failures until this is fixed.",
        body: summarizeBody(body),
      };
    }

    return {
      name: "api_reachable",
      status: response.status === 401 ? "passed" : "warning",
      http_status: response.status,
      detail:
        response.status === 401
          ? "API is reachable and authentication is enforced."
          : "API responded with a non-401 status before authentication.",
      body: response.status === 401 ? undefined : summarizeBody(body),
    };
  } catch (error) {
    return {
      name: "api_reachable",
      status: "failed",
      error: error.message,
    };
  }
}

async function checkApiHealth(base) {
  try {
    const response = await fetchWithTimeout(`${base}/tracer/v1/health`, {
      method: "GET",
    });
    const body = await parseResponseBody(response);
    const healthy =
      response.ok &&
      (body?.status === "healthy" || body?.result?.status === "healthy");

    return {
      name: "api_health",
      status: healthy
        ? "passed"
        : response.status >= 500
          ? "failed"
          : "warning",
      http_status: response.status,
      detail: healthy
        ? "Public tracer health endpoint reports healthy."
        : response.status >= 500
          ? "Public tracer health endpoint returned 5xx; do not trust route-level API journey failures until the local API runtime is healthy."
          : "Public tracer health endpoint did not return the expected healthy payload.",
      body: healthy ? undefined : summarizeBody(body),
    };
  } catch (error) {
    return {
      name: "api_health",
      status: "failed",
      error: error.message,
    };
  }
}

async function checkAuthentication(base) {
  if (process.env.FUTURE_AGI_ACCESS_TOKEN) {
    return checkAccessToken(base, process.env.FUTURE_AGI_ACCESS_TOKEN, {
      source: "env",
    });
  }

  const cachedTokens = await readCachedTokens({ apiBase: base });
  let cachedTokenCheck = null;
  if (cachedTokens?.access) {
    cachedTokenCheck = await checkAccessToken(base, cachedTokens.access, {
      source: "token_file",
    });
    if (cachedTokenCheck.status === "passed") return cachedTokenCheck;
  }

  const email = process.env.FUTURE_AGI_EMAIL;
  const password = process.env.FUTURE_AGI_PASSWORD;
  if (!email || !password) {
    if (cachedTokenCheck) {
      return {
        ...cachedTokenCheck,
        detail: `${cachedTokenCheck.detail} No FUTURE_AGI_EMAIL/FUTURE_AGI_PASSWORD fallback is set to refresh the token file.`,
      };
    }

    return {
      name: "auth_context",
      status: "failed",
      detail:
        "Set FUTURE_AGI_ACCESS_TOKEN, FUTURE_AGI_TOKEN_FILE, or FUTURE_AGI_EMAIL/FUTURE_AGI_PASSWORD before running API journeys.",
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
    if (retry.ok && retry.body?.access) {
      await writeCachedTokens(retry.body, { apiBase: base }).catch(() => null);
    }
    return tokenResponseToCheck(retry);
  }

  if (tokenResult.ok && tokenResult.body?.access) {
    await writeCachedTokens(tokenResult.body, { apiBase: base }).catch(
      () => null,
    );
  }
  return tokenResponseToCheck(tokenResult);
}

async function checkAccessToken(base, accessToken, { source } = {}) {
  try {
    const response = await fetchWithTimeout(`${base}/accounts/user-info/`, {
      method: "GET",
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const body = await parseResponseBody(response);
    return {
      name: "auth_context",
      source,
      status: response.ok && body?.status !== false ? "passed" : "failed",
      http_status: response.status,
      detail:
        response.ok && body?.status !== false
          ? `${source === "token_file" ? "Cached" : "Provided"} access token is accepted by /accounts/user-info/.`
          : `${source === "token_file" ? "Cached" : "Provided"} access token was rejected by /accounts/user-info/.`,
      body: response.ok ? undefined : summarizeBody(body),
    };
  } catch (error) {
    return {
      name: "auth_context",
      source,
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

async function checkPostgresVolumeIntegrity({ envName, fallbackNames }) {
  const explicit = splitEnvList(process.env[envName]);
  const names = [
    ...new Set((explicit.length ? explicit : fallbackNames).filter(Boolean)),
  ];
  const results = [];

  for (const name of names) {
    const container = await loadDockerContainerInspect(name);
    if (!container?.exists) continue;

    results.push(await checkPostgresContainerVolumeIntegrity(name, container));
  }

  return results;
}

async function checkPostgresQueryHealth({ envName, fallbackNames }) {
  const explicit = splitEnvList(process.env[envName]);
  const names = [
    ...new Set((explicit.length ? explicit : fallbackNames).filter(Boolean)),
  ];
  const results = [];

  for (const name of names) {
    const container = await loadDockerContainerInspect(name);
    if (!container?.exists || container.status !== "running") continue;

    results.push(await checkPostgresContainerQueryHealth(name, container));
  }

  return results;
}

async function checkPostgresContainerQueryHealth(name, container) {
  const database =
    process.env.API_JOURNEY_DB_NAME ||
    container.env
      .find((value) => value.startsWith("POSTGRES_DB="))
      ?.slice("POSTGRES_DB=".length) ||
    "postgres";
  const user =
    process.env.API_JOURNEY_DB_USER ||
    container.env
      .find((value) => value.startsWith("POSTGRES_USER="))
      ?.slice("POSTGRES_USER=".length) ||
    "postgres";
  const password =
    process.env.API_JOURNEY_DB_PASSWORD ||
    container.env
      .find((value) => value.startsWith("POSTGRES_PASSWORD="))
      ?.slice("POSTGRES_PASSWORD=".length) ||
    "";

  const args = ["exec"];
  if (password) args.push("-e", `PGPASSWORD=${password}`);
  args.push(
    name,
    "psql",
    "-U",
    user,
    "-d",
    database,
    "-v",
    "ON_ERROR_STOP=1",
    "-Atc",
    "select 1",
  );

  try {
    const { stdout } = await execFileAsync("docker", args, { timeout: 8000 });
    const output = stdout.trim();
    const passed = output === "1";

    return {
      name: `postgres_query:${name}`,
      status: passed ? "passed" : "failed",
      database,
      user,
      detail: passed
        ? "PostgreSQL accepted a simple query."
        : "PostgreSQL did not return the expected result for a simple query; DB-backed API journeys are not trustworthy.",
      output: passed ? undefined : output.slice(0, 1000),
      recent_log_summary: passed ? undefined : await summarizeDockerLogs(name),
    };
  } catch (error) {
    return {
      name: `postgres_query:${name}`,
      status: "failed",
      database,
      user,
      error: error.message,
      stdout: error.stdout ? String(error.stdout).slice(0, 1000) : undefined,
      stderr: error.stderr ? String(error.stderr).slice(0, 1000) : undefined,
      detail:
        "PostgreSQL could not serve a simple query; fix the local DB before classifying authenticated API journey failures as product regressions.",
      recent_log_summary: await summarizeDockerLogs(name),
    };
  }
}

async function checkPostgresContainerVolumeIntegrity(name, container) {
  const pgdata =
    container.env
      .find((value) => value.startsWith("PGDATA="))
      ?.slice("PGDATA=".length) || "/var/lib/postgresql/data";
  const mountInfo = findMountForPath(container.mounts, pgdata);

  if (!mountInfo) {
    return {
      name: `postgres_volume_integrity:${name}`,
      status: "warning",
      pgdata,
      detail:
        "Could not find the Docker mount for PGDATA, so PostgreSQL data-directory integrity was not checked.",
    };
  }

  const mountSource =
    mountInfo.mount.Type === "volume"
      ? mountInfo.mount.Name
      : mountInfo.mount.Source;
  const mountedPath = `/mnt${mountInfo.relativePath}`;

  try {
    const { stdout } = await execFileAsync("docker", [
      "run",
      "--rm",
      "--entrypoint",
      "sh",
      "-v",
      `${mountSource}:/mnt:ro`,
      container.image,
      "-lc",
      buildPostgresDataProbeScript(mountedPath),
    ]);
    const probe = parsePostgresVolumeProbe(stdout);
    const classification = classifyPostgresVolumeProbe(probe);

    return {
      name: `postgres_volume_integrity:${name}`,
      status: classification.status,
      container_status: container.status,
      image: container.image,
      pgdata,
      mount: {
        type: mountInfo.mount.Type,
        name: mountInfo.mount.Name || null,
        destination: mountInfo.mount.Destination,
      },
      files: probe.files,
      directories: probe.directories,
      entry_count: probe.entryCount,
      flags: classification.flags,
      detail: classification.detail,
      recommended_action: classification.recommendedAction,
    };
  } catch (error) {
    return {
      name: `postgres_volume_integrity:${name}`,
      status: "warning",
      container_status: container.status,
      image: container.image,
      pgdata,
      mount: {
        type: mountInfo.mount.Type,
        name: mountInfo.mount.Name || null,
        destination: mountInfo.mount.Destination,
      },
      error: error.message,
      detail:
        "Could not inspect the PostgreSQL data volume read-only. Check Docker image and volume access before trusting DB-backed journey results.",
    };
  }
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
  const container = await loadDockerContainerInspect(name);
  return {
    exists: Boolean(container?.exists),
    status: container?.status || "",
  };
}

async function loadDockerContainerInspect(name) {
  try {
    const { stdout } = await execFileAsync("docker", ["inspect", name]);
    const [container] = JSON.parse(stdout);
    return {
      exists: true,
      status: container?.State?.Status || "",
      image: container?.Config?.Image || "",
      env: container?.Config?.Env || [],
      mounts: container?.Mounts || [],
    };
  } catch {
    return { exists: false };
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

function findMountForPath(mounts, targetPath) {
  const normalizedTarget = stripTrailingSlash(targetPath);
  const candidates = (mounts || [])
    .filter((mount) => mount.Type === "volume" || mount.Type === "bind")
    .map((mount) => ({
      mount,
      destination: stripTrailingSlash(mount.Destination),
    }))
    .sort((a, b) => b.destination.length - a.destination.length);

  for (const candidate of candidates) {
    if (
      normalizedTarget === candidate.destination ||
      normalizedTarget.startsWith(`${candidate.destination}/`)
    ) {
      return {
        mount: candidate.mount,
        relativePath: normalizedTarget.slice(candidate.destination.length),
      };
    }
  }
  return null;
}

function stripTrailingSlash(value) {
  const normalized = String(value || "").replace(/\/+$/, "");
  return normalized || "/";
}

function buildPostgresDataProbeScript(mountedPath) {
  const base = shellQuote(mountedPath);
  return `
set -eu
base=${base}
for file in PG_VERSION postgresql.conf pg_hba.conf pg_ident.conf postgresql.auto.conf; do
  path="$base/$file"
  if [ -e "$path" ]; then
    bytes=$(wc -c < "$path" | tr -d ' ')
    if [ "$bytes" = "0" ]; then
      printf 'file\\t%s\\tzero\\t%s\\n' "$file" "$bytes"
    else
      printf 'file\\t%s\\tok\\t%s\\n' "$file" "$bytes"
    fi
  else
    printf 'file\\t%s\\tmissing\\t0\\n' "$file"
  fi
done
for dir in base global pg_wal pg_xact; do
  if [ -d "$base/$dir" ]; then
    printf 'dir\\t%s\\tpresent\\t0\\n' "$dir"
  else
    printf 'dir\\t%s\\tmissing\\t0\\n' "$dir"
  fi
done
entries=$(find "$base" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
printf 'entries\\t.\\t%s\\t0\\n' "$entries"
`;
}

function parsePostgresVolumeProbe(output) {
  const files = [];
  const directories = [];
  let entryCount = 0;

  for (const line of String(output || "").split(/\r?\n/)) {
    const [kind, name, status, size] = line.split("\t");
    if (!kind || !name) continue;

    if (kind === "file") {
      files.push({ name, status, size_bytes: Number(size) || 0 });
    } else if (kind === "dir") {
      directories.push({ name, status });
    } else if (kind === "entries") {
      entryCount = Number(status) || 0;
    }
  }

  return { files, directories, entryCount };
}

function classifyPostgresVolumeProbe(probe) {
  const criticalFileNames = new Set([
    "PG_VERSION",
    "postgresql.conf",
    "pg_hba.conf",
  ]);
  const criticalFiles = probe.files.filter((file) =>
    criticalFileNames.has(file.name),
  );
  const zeroCritical = criticalFiles.filter((file) => file.status === "zero");
  const missingCritical = criticalFiles.filter(
    (file) => file.status === "missing",
  );
  const presentDataDirectories = probe.directories
    .filter((directory) => directory.status === "present")
    .map((directory) => directory.name);
  const hasExistingData =
    probe.entryCount > 0 || presentDataDirectories.length > 0;
  const flags = [];

  if (zeroCritical.length) flags.push("postgres_zero_byte_critical_files");
  if (missingCritical.length && hasExistingData)
    flags.push("postgres_missing_critical_files");
  if (presentDataDirectories.length) flags.push("postgres_data_dirs_present");

  if (zeroCritical.length || (missingCritical.length && hasExistingData)) {
    const fileNames = [...zeroCritical, ...missingCritical]
      .map((file) => `${file.name}:${file.status}`)
      .join(", ");
    return {
      status: "failed",
      flags,
      detail: `PostgreSQL data directory appears truncated or only partially initialized; critical files are invalid (${fileNames}).`,
      recommendedAction:
        "Back up, repair, or intentionally recreate the local Postgres volume before running DB-backed API journeys. Do not delete active volumes without explicit approval.",
    };
  }

  if (!hasExistingData) {
    return {
      status: "warning",
      flags: ["postgres_data_dir_empty"],
      detail:
        "PostgreSQL data directory is empty; the container may need a clean initialization before DB-backed API journeys run.",
      recommendedAction:
        "Start the Postgres service and rerun preflight after initialization completes.",
    };
  }

  return {
    status: "passed",
    flags,
    detail:
      "PostgreSQL data directory contains non-empty critical files expected for an initialized cluster.",
  };
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
    if (/initdb: error: directory .* exists but is not empty/i.test(text)) {
      flags.push("postgres_initdb_non_empty_data_dir");
    }
    if (
      /xlog flush request|request to flush past end of generated WAL/i.test(
        text,
      )
    ) {
      flags.push("postgres_wal_flush_error");
    }
    if (/could not write block/i.test(text)) {
      flags.push("postgres_write_block_error");
    }
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

async function checkHostDiskAvailability() {
  const paths = [
    ...new Set(
      (splitEnvList(process.env.API_JOURNEY_HOST_DISK_PATHS).length
        ? splitEnvList(process.env.API_JOURNEY_HOST_DISK_PATHS)
        : [process.cwd(), os.tmpdir()]
      ).filter(Boolean),
    ),
  ];

  try {
    const { stdout } = await execFileAsync("df", ["-Pk", ...paths]);
    const filesystems = parseDfRows(stdout);
    if (!filesystems.length) {
      return {
        name: "host_disk",
        status: "warning",
        paths,
        detail:
          "Could not parse host filesystem free-space output; verify disk availability before DB-backed API journeys.",
      };
    }

    const worstCapacity = filesystems.reduce(
      (max, row) => Math.max(max, row.capacity_percent),
      0,
    );
    const lowestAvailable = filesystems.reduce(
      (min, row) => Math.min(min, row.available_kib),
      Number.POSITIVE_INFINITY,
    );
    const status =
      lowestAvailable < 1024 * 1024 || worstCapacity >= 100
        ? "failed"
        : lowestAvailable < 5 * 1024 * 1024 || worstCapacity >= 95
          ? "warning"
          : "passed";

    return {
      name: "host_disk",
      status,
      paths,
      filesystems,
      detail:
        status === "passed"
          ? "Host filesystem has enough free space for local API journey probes."
          : "Host filesystem free space is low; local services can return false route failures, database fsync errors, or container I/O errors.",
    };
  } catch (error) {
    return {
      name: "host_disk",
      status: "warning",
      paths,
      error: error.message,
      detail:
        "Could not inspect host filesystem free space; verify disk availability before DB-backed API journeys.",
    };
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
  const parsed = { jsonPath: "", publicOnly: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--json") {
      parsed.jsonPath = argv[++index] || "";
    } else if (arg === "--public" || arg === "--public-only") {
      parsed.publicOnly = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return parsed;
}
