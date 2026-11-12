// The Console-origin listener, server side (CP P6, D1; READINESS L05 Console path).
//
// The Console runs on its own origin (same site as Future AGI, a different origin), so a
// script running in the Console cannot read Future AGI's storage. This listener serves:
//   - the Console's static build (OMEGA_CONSOLE_STATIC_DIR), with a strict CSP whose
//     `frame-ancestors` names only the Future AGI origin, and the other security headers;
//   - the gateway API under the same base path, which here accepts pre-minted assertions
//     only: a Future AGI browser token is refused, so that token never has a reason to be
//     present on this origin (server.mjs enforces it per listener);
//   - a CSP report sink that logs the directive and the blocked origin, nothing else.
//
// Trusted Types are sent report-only until the Console's DOM writes are proven safe
// (lane E-SHELL); OMEGA_CONSOLE_TRUSTED_TYPES=enforce moves them into the enforced policy.
import fs from "node:fs";
import path from "node:path";

// Matches the hosted build's OMEGA_CONSOLE_BASE and edge-console.Dockerfile mount.
const CONSOLE_BASE_PATH = "/omega/console";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json",
};
export const CSP_REPORT_PATH = "/__omega/csp-report";
const MAX_REPORT_BYTES = 16_384;

/** The enforced and the report-only policy for Console documents. */
export function consolePolicies(
  consoleConfig,
  { trustedTypes = "report" } = {},
) {
  const base = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    `frame-ancestors ${consoleConfig.frameAncestors.join(" ")}`,
    `report-uri ${CSP_REPORT_PATH}`,
  ];
  const tt = "require-trusted-types-for 'script'";
  return trustedTypes === "enforce"
    ? { enforced: [...base, tt].join("; "), reportOnly: null }
    : {
        enforced: base.join("; "),
        reportOnly: [tt, `report-uri ${CSP_REPORT_PATH}`].join("; "),
      };
}

/** Headers on every Console-origin response (API answers included). */
export function securityHeaders(mode) {
  const headers = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-site",
    "permissions-policy":
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  };
  if (mode === "cloud")
    headers["strict-transport-security"] =
      "max-age=31536000; includeSubDomains";
  return headers;
}

/** Map a URL path onto a regular file inside `root`, or null. Symlinks out of the root are refused. */
export function resolveStaticFile(root, urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  if (
    decoded.includes("\0") ||
    decoded.includes("\\") ||
    decoded.split("/").some((part) => part === "..")
  )
    return null;
  const relative = decoded.replace(/^\/+/, "") || "index.html";
  const candidate = path.resolve(root, relative);
  let realRoot;
  let real;
  try {
    realRoot = fs.realpathSync(root);
    real = fs.realpathSync(candidate);
  } catch {
    return null;
  }
  if (real !== realRoot && !real.startsWith(`${realRoot}${path.sep}`))
    return null;
  const stat = fs.statSync(real, { throwIfNoEntry: false });
  return stat?.isFile() ? { file: real, size: stat.size } : null;
}

function cacheControlFor(file) {
  // Vite's hashed assets never change under one name; documents must revalidate so a new
  // Console version is picked up (CP P19).
  return /[.-][A-Za-z0-9_-]{8,}\.(?:js|css|woff2?|svg|png|webp)$/.test(file)
    ? "public, max-age=31536000, immutable"
    : "no-cache";
}

function isConsoleStaticPath(urlPath) {
  if (
    urlPath !== CONSOLE_BASE_PATH &&
    !urlPath.startsWith(`${CONSOLE_BASE_PATH}/`)
  )
    return false;
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return false;
  }
  // Unlike API literals, asset names may encode unreserved characters. Reject only
  // ambiguous structure: encoded separators, nested escapes, controls and dot segments.
  if (/%(?:2f|5c)/i.test(urlPath) || /%[0-9a-f]{2}/i.test(decoded))
    return false;
  // J00.L3 ruling d: no segment starting with "." (dotfiles, dot directories, dot
  // segments) is ever static, so it is neither served nor answered with the document.
  return (
    !/[\u0000-\u001f\u007f\\]/u.test(decoded) &&
    !decoded.includes("//") &&
    !decoded.split("/").some((part) => part.startsWith("."))
  );
}

// The built asset root is files-only: a directory or a missing file at or below it is a 404,
// never the SPA document. Judged on the decoded path (isConsoleStaticPath already proved it
// decodes and holds no encoded separator), so an escaped spelling cannot bypass it.
function isFilesOnlyPath(urlPath) {
  const decoded = decodeURIComponent(urlPath);
  const assetRoot = `${CONSOLE_BASE_PATH}/assets`;
  return decoded === assetRoot || decoded.startsWith(`${assetRoot}/`);
}

/**
 * Serve one static request. Returns false when the request is not a static one (the caller
 * answers 404). Only unambiguous paths at or below /omega/console are static; unknown
 * document paths there fall back to the root `index.html`, so client routes load.
 */
export function serveStatic(
  request,
  response,
  urlPath,
  consoleConfig,
  { mode, extraHeaders = {} },
) {
  if (
    !consoleConfig.staticDir ||
    (request.method !== "GET" && request.method !== "HEAD")
  )
    return false;
  // Gate file lookup as well as SPA fallback: neither a prefix lookalike nor an
  // ambiguous/traversal path may turn into a successful Console document request.
  if (!isConsoleStaticPath(urlPath)) return false;
  let found = resolveStaticFile(consoleConfig.staticDir, urlPath);
  const wantsDocument =
    /text\/html/.test(request.headers.accept || "") || !path.extname(urlPath);
  if (!found && wantsDocument && !isFilesOnlyPath(urlPath))
    found = resolveStaticFile(consoleConfig.staticDir, "/index.html");
  if (!found) return false;
  const type =
    TYPES[path.extname(found.file).toLowerCase()] || "application/octet-stream";
  const headers = {
    ...securityHeaders(mode),
    ...extraHeaders,
    "content-type": type,
    "content-length": String(found.size),
    "cache-control": cacheControlFor(found.file),
  };
  if (type.startsWith("text/html")) {
    const policies = consolePolicies(consoleConfig, {
      trustedTypes: consoleConfig.trustedTypes,
    });
    headers["content-security-policy"] = policies.enforced;
    if (policies.reportOnly)
      headers["content-security-policy-report-only"] = policies.reportOnly;
  }
  response.writeHead(200, headers);
  if (request.method === "HEAD") {
    response.end();
    return true;
  }
  const stream = fs.createReadStream(found.file);
  stream.once("error", () => response.destroy());
  response.once("close", () => stream.destroy());
  stream.pipe(response);
  return true;
}

/** Accept one CSP report and reduce it to what a dashboard needs. */
export async function readCspReport(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_REPORT_BYTES) return null;
    chunks.push(chunk);
  }
  let parsed;
  try {
    parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return null;
  }
  const report =
    parsed?.["csp-report"] ??
    (Array.isArray(parsed) ? parsed[0]?.body : parsed?.body) ??
    {};
  const blocked = String(report["blocked-uri"] ?? report.blockedURL ?? "");
  let blockedOrigin = blocked;
  try {
    blockedOrigin = new URL(blocked).origin;
  } catch {
    blockedOrigin = blocked.slice(0, 32);
  }
  return {
    directive: String(
      report["violated-directive"] ??
        report["effective-directive"] ??
        report.effectiveDirective ??
        "unknown",
    ).slice(0, 64),
    blocked: blockedOrigin,
    disposition: String(report.disposition ?? "enforce").slice(0, 16),
  };
}
