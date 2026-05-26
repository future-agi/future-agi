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
    ...(await checkDockerContainers({
      envName: "API_JOURNEY_REDIS_CONTAINER",
      fallbackNames: DEFAULT_REDIS_CONTAINERS,
      service: "redis",
    })),
  );
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
