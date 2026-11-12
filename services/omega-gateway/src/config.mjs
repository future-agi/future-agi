// Gateway configuration.
//
// Cloud mode (the default) keeps Squad's rules from
// services/omega-squad-gateway/src/policy.mjs (7debd6482): HTTPS-only remote
// EE and issuer URLs that are not loopback, HTTPS browser origins, and no
// static operator credential. Local mode (OMEGA_GATEWAY_MODE=local) is the
// inverse fence for the loopback development stack: every URL and origin must
// be loopback, and the listener binds 127.0.0.1 only.
import fs from "node:fs";
import { parsePublicKeys } from "./assertion.mjs";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1"]);

function hostOf(url) {
  return url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
}

function isLocalHost(hostname) {
  return (
    LOOPBACK_HOSTS.has(hostname) ||
    hostname.endsWith(".local") ||
    hostname.endsWith(".localhost")
  );
}

function isLoopback(hostname) {
  return (
    hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1"
  );
}

function checkServiceUrl(name, value, mode) {
  if (typeof value !== "string" || value.trim() === "")
    throw new Error(`${name} is required`);
  const url = new URL(value.trim());
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(
      `${name} must not contain credentials, query parameters, or fragments`,
    );
  }
  const hostname = hostOf(url);
  if (mode === "local") {
    if (url.protocol !== "http:" && url.protocol !== "https:")
      throw new Error(`${name} must use http or https`);
    if (!isLoopback(hostname))
      throw new Error(`${name} must be a loopback address in local mode`);
    return url;
  }
  if (url.protocol !== "https:") throw new Error(`${name} must use https`);
  if (isLocalHost(hostname))
    throw new Error(`${name} must identify a remote cloud deployment`);
  return url;
}

export function normalizeRemoteBaseUrl(value, mode = "cloud") {
  return checkServiceUrl("OMEGA_REMOTE_BASE_URL", value, mode).href.replace(
    /\/+$/,
    "",
  );
}

export function parseAllowedOrigins(value, mode = "cloud") {
  if (!value || value.trim() === "") return [];
  return value
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean)
    .map((origin) => {
      const url = new URL(origin);
      if (url.pathname !== "/" || url.search || url.hash)
        throw new Error(
          `OMEGA_ALLOWED_ORIGINS must contain origins without paths or queries: ${origin}`,
        );
      const hostname = hostOf(url);
      if (mode === "local") {
        if (!isLoopback(hostname))
          throw new Error(
            `OMEGA_ALLOWED_ORIGINS must be loopback in local mode: ${origin}`,
          );
        return url.origin;
      }
      if (url.protocol !== "https:")
        throw new Error(
          `OMEGA_ALLOWED_ORIGINS contains a non-HTTPS origin: ${origin}`,
        );
      if (isLocalHost(hostname))
        throw new Error(
          `OMEGA_ALLOWED_ORIGINS contains a local origin: ${origin}`,
        );
      return url.origin;
    });
}

function normalizeBasePath(value) {
  const raw = (value || "").trim();
  if (raw === "" || raw === "/") return "";
  const valid =
    /^(?:\/[A-Za-z0-9._-]+)+\/?$/.test(raw) &&
    !raw.split("/").some((segment) => segment === "." || segment === "..");
  if (!valid)
    throw new Error(
      "OMEGA_GATEWAY_BASE_PATH must be an absolute path such as /omega/api",
    );
  return raw.replace(/\/+$/, "");
}

function boundedNumber(value, fallback, min, max) {
  const candidate = Number(value ?? fallback);
  return Number.isFinite(candidate)
    ? Math.min(Math.max(candidate, min), max)
    : fallback;
}

function readPublicKeys(env) {
  const file = env.OMEGA_ASSERTION_JWKS_FILE?.trim();
  if (!file) return null;
  return parsePublicKeys(JSON.parse(fs.readFileSync(file, "utf8")));
}

function flag(name, value, fallback) {
  const raw = (value ?? "").trim().toLowerCase();
  if (raw === "") return fallback;
  if (raw === "on" || raw === "true" || raw === "1") return true;
  if (raw === "off" || raw === "false" || raw === "0") return false;
  throw new Error(`${name} must be on or off`);
}

// Rate-limit buckets are shared between gateway replicas through Redis (READINESS L03,
// CP REL4). Without a URL each replica keeps its own buckets, which only local mode allows.
function rateLimitStore(env, mode) {
  const value = env.OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL?.trim();
  if (!value) {
    if (mode === "cloud")
      throw new Error(
        "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL is required in cloud mode so limits hold across replicas",
      );
    return { kind: "memory" };
  }
  const url = new URL(value);
  if (url.protocol !== "redis:" && url.protocol !== "rediss:")
    throw new Error(
      "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL must use redis or rediss",
    );
  if (url.search || url.hash)
    throw new Error(
      "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL must not carry query parameters or fragments",
    );
  if (mode === "local" && !isLoopback(hostOf(url)))
    throw new Error(
      "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL must be a loopback address in local mode",
    );
  const db =
    url.pathname && url.pathname !== "/" ? Number(url.pathname.slice(1)) : 0;
  if (!Number.isInteger(db) || db < 0 || db > 15)
    throw new Error("OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL database must be 0-15");
  return {
    kind: "redis",
    tls: url.protocol === "rediss:",
    host: hostOf(url),
    port: Number(url.port || 6379),
    username: decodeURIComponent(url.username || ""),
    password: decodeURIComponent(url.password || ""),
    db,
  };
}

// Gateway and request spans go to a Future AGI Observe project (CP P9, D16). The key pair
// is read from a file, never from the environment or a command line.
function otlpConfig(env, mode) {
  const value = env.OMEGA_GATEWAY_OTLP_TRACES_URL?.trim();
  if (!value) return null;
  const url = checkServiceUrl("OMEGA_GATEWAY_OTLP_TRACES_URL", value, mode);
  const file = env.OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE?.trim();
  let credentials = null;
  if (file) {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (
      typeof parsed?.apiKey !== "string" ||
      typeof parsed?.secretKey !== "string" ||
      !parsed.apiKey ||
      !parsed.secretKey
    ) {
      throw new Error(
        'OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE must hold {"apiKey", "secretKey"}',
      );
    }
    credentials = { apiKey: parsed.apiKey, secretKey: parsed.secretKey };
  }
  return {
    url: url.href,
    credentials,
    projectName: env.OMEGA_GATEWAY_OTLP_PROJECT?.trim() || "omega-console-ops",
    serviceName: "omega-gateway",
  };
}

// The Console-origin listener (CP P6, D1): the Console's static build and its API on their
// own origin, which accepts pre-minted assertions only.
function consoleOriginConfig(env, mode, allowedOrigins, publicKeys, basePath) {
  const origin = env.OMEGA_CONSOLE_ORIGIN?.trim();
  if (!origin) return null;
  // The API and the static build share the origin: the API lives under this base path,
  // while static Console paths live under /omega/console. Without an API base path every
  // file would be routed as an API call.
  if (!basePath)
    throw new Error(
      "OMEGA_CONSOLE_ORIGIN requires OMEGA_GATEWAY_BASE_PATH (for example /omega/api)",
    );
  const [parsed] = parseAllowedOrigins(origin, mode);
  if (!parsed) throw new Error("OMEGA_CONSOLE_ORIGIN must be one origin");
  const ancestors = env.OMEGA_CONSOLE_FRAME_ANCESTORS?.trim()
    ? parseAllowedOrigins(env.OMEGA_CONSOLE_FRAME_ANCESTORS, mode)
    : allowedOrigins;
  if (ancestors.length === 0)
    throw new Error(
      "OMEGA_CONSOLE_FRAME_ANCESTORS (or OMEGA_ALLOWED_ORIGINS) must name the Future AGI origin",
    );
  if (ancestors.includes(parsed))
    throw new Error(
      "OMEGA_CONSOLE_ORIGIN must differ from the Future AGI origin it is framed by",
    );
  // Every request on this origin is keyed and admitted on assertion claims alone, so the
  // signature must be checked here, not only in EE.
  if (!publicKeys)
    throw new Error("OMEGA_CONSOLE_ORIGIN needs OMEGA_ASSERTION_JWKS_FILE");
  const staticDir = env.OMEGA_CONSOLE_STATIC_DIR?.trim() || "";
  if (staticDir && !staticDir.startsWith("/"))
    throw new Error("OMEGA_CONSOLE_STATIC_DIR must be an absolute path");
  const trustedTypes = (env.OMEGA_CONSOLE_TRUSTED_TYPES || "report")
    .trim()
    .toLowerCase();
  if (trustedTypes !== "report" && trustedTypes !== "enforce")
    throw new Error("OMEGA_CONSOLE_TRUSTED_TYPES must be report or enforce");
  return {
    origin: parsed,
    frameAncestors: ancestors,
    staticDir,
    trustedTypes,
    host: mode === "local" ? "127.0.0.1" : env.HOST?.trim() || "0.0.0.0",
    port: Number(env.OMEGA_CONSOLE_PORT ?? 8081),
  };
}

export function loadConfig(env = process.env) {
  const modeValue = (env.OMEGA_GATEWAY_MODE || "cloud").trim().toLowerCase();
  if (modeValue !== "cloud" && modeValue !== "local")
    throw new Error("OMEGA_GATEWAY_MODE must be cloud or local");
  const mode = modeValue;
  const exchangeUrl = env.OMEGA_SESSION_EXCHANGE_URL?.trim() || "";
  if (!exchangeUrl) {
    throw new Error(
      "OMEGA_SESSION_EXCHANGE_URL is required for tenant-bound sessions",
    );
  }
  if (env.OMEGA_UPSTREAM_TOKEN?.trim()) {
    throw new Error(
      "OMEGA_UPSTREAM_TOKEN is not supported; use a tenant-bound session exchange",
    );
  }
  checkServiceUrl("OMEGA_SESSION_EXCHANGE_URL", exchangeUrl, mode);
  const issuer = env.OMEGA_ASSERTION_ISSUER?.trim() || "";
  const assertionPublicKeys = readPublicKeys(env);
  // Rate limits, role floors and the Console origin are keyed on assertion claims, so in
  // cloud mode a forged claim must never reach them: the gateway checks signatures itself.
  if (mode === "cloud" && !assertionPublicKeys)
    throw new Error("OMEGA_ASSERTION_JWKS_FILE is required in cloud mode");
  const allowedOrigins = parseAllowedOrigins(env.OMEGA_ALLOWED_ORIGINS, mode);
  const basePath = normalizeBasePath(env.OMEGA_GATEWAY_BASE_PATH);
  const requestTimeoutMs = boundedNumber(
    env.OMEGA_REQUEST_TIMEOUT_MS,
    15_000,
    1_000,
    120_000,
  );
  const agentDraftTimeoutMs = boundedNumber(
    env.OMEGA_AGENT_DRAFT_TIMEOUT_MS,
    35_000,
    1_000,
    120_000,
  );
  return {
    mode,
    remoteBaseUrl: normalizeRemoteBaseUrl(env.OMEGA_REMOTE_BASE_URL, mode),
    exchangeUrl,
    allowedOrigins,
    basePath,
    // Local mode never listens beyond loopback.
    host: mode === "local" ? "127.0.0.1" : env.HOST?.trim() || "0.0.0.0",
    port: Number(env.PORT ?? 8080),
    maxBodyBytes: boundedNumber(
      env.OMEGA_MAX_BODY_BYTES,
      1_048_576,
      1_024,
      10_485_760,
    ),
    // Project bundles (POST/PUT /workspace/deployments/**) and sandbox file
    // writes (PUT /workspace/sandboxes/{id}/files/**) may exceed the JSON cap.
    maxBundleBytes: boundedNumber(
      env.OMEGA_MAX_BUNDLE_BYTES,
      67_108_864,
      1_048_576,
      268_435_456,
    ),
    // Bound session exchange and upstream connection establishment. The timer
    // is cleared as soon as upstream headers arrive, so an accepted SSE stream
    // remains long-lived while a dead dependency fails promptly.
    requestTimeoutMs,
    // Drafting is a bounded synchronous model call (30s in EE), unlike task admission.
    agentDraftTimeoutMs,
    // Time to upstream response headers, by the manifest's timeout class. Once headers
    // arrive the body is piped with no total timer, so streams and downloads stay open.
    timeouts: {
      read: requestTimeoutMs,
      write: boundedNumber(env.OMEGA_WRITE_TIMEOUT_MS, 30_000, 1_000, 300_000),
      model: agentDraftTimeoutMs,
      stream: requestTimeoutMs,
    },
    // Required in cloud mode (above); optional on loopback.
    assertionIssuer: issuer,
    assertionPublicKeys,
    rateLimitStore: rateLimitStore(env, mode),
    // Client IP for the public share class: the number of trusted proxies in front of the
    // gateway whose X-Forwarded-For entries are believed (0 = the socket address).
    trustedProxyHops: boundedNumber(
      env.OMEGA_GATEWAY_TRUSTED_PROXY_HOPS,
      0,
      0,
      5,
    ),
    // The public share class stays closed until CP wave 3 (F-SHARE).
    publicShare: flag(
      "OMEGA_GATEWAY_PUBLIC_SHARE",
      env.OMEGA_GATEWAY_PUBLIC_SHARE,
      false,
    ),
    // Graceful drain on SIGTERM (READINESS 1.3, CH-02, CP REL2).
    drainMs: boundedNumber(env.OMEGA_GATEWAY_DRAIN_MS, 10_000, 0, 30_000),
    // `/readyz` requires EE 200 JSON { status: "ready", ready: true } (<=64 KiB)
    // at this path and a 401 refusal from the session exchange's unauthenticated GET.
    eeReadyPath:
      normalizeBasePath(env.OMEGA_GATEWAY_EE_READY_PATH || "/readyz") ||
      "/readyz",
    otlp: otlpConfig(env, mode),
    consoleOrigin: consoleOriginConfig(
      env,
      mode,
      allowedOrigins,
      assertionPublicKeys,
      basePath,
    ),
  };
}
