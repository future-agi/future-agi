// Where the Omega Console lives, seen from Future AGI.
//
// The parent page owns /dashboard/console/*; everything after it is the
// Console's own route (/dashboard/console/agents/a1 <-> #/agents/a1).
// By default the Console is the private EE build served on this same site
// under /omega/console/ (the dev server and the local stack proxy it there)
// and opened in embed mode with ?embed=1. Its router reads the route from the
// fragment, so the frame uses hash routing.
//
// In production the Console runs on its own origin (CP P6, decision D1: same
// site, another origin, for example https://console.omega.futureagi.com),
// so a script in the Console cannot read Future AGI's storage. That origin is
// set at runtime (window.__FUTURE_AGI_CONFIG__.VITE_OMEGA_CONSOLE_URL, which
// the container entrypoint writes) or at build time (VITE_OMEGA_CONSOLE_URL).
// The frame then speaks only with that exact origin, and is sandboxed.
export const CONSOLE_BASE_PATH = "/dashboard/console";

export const DEFAULT_CONSOLE_SRC = "/omega/console/?embed=1";

export const CONSOLE_ROUTE_MODE = "hash";

// How long the frame waits, after a hello this page could not answer, before
// it shows the needs-key panel. The shared surface waits for the child's own
// gate, because Studio may still hold a key from an earlier visit. The Console
// never does: it keeps its key in memory only, and this page mints fresh ones
// for every hello. So an unanswered hello means the mint failed, the page
// already knows why, and the reason is shown at once instead of after the
// Console's own 6 s wait.
export const CONSOLE_NO_KEY_TIMEOUT_MS = 0;

// The frame's permissions. clipboard-write lets the Console's "Copy
// reference" write the clipboard (omega-embed-1.additions.json, `frame`).
// A Console served from this computer on another origin (a dev server) also
// needs the local-network permissions; a hosted Console never gets them.
export const CONSOLE_FRAME_ALLOW = "clipboard-write";
const LOCAL_CONSOLE_FRAME_ALLOW =
  "clipboard-write; local-network-access; loopback-network";

// On its own origin the frame is sandboxed. allow-same-origin keeps the
// Console's own origin (not this page's: the frame is cross-origin), so its
// API calls still carry `Origin`. Left out on purpose: top navigation (the
// Console moves Future AGI only through embed.navigate and embed.open), pointer
// lock, presentation and orientation lock. Popups may escape the sandbox so
// an OAuth consent window works as a normal page.
export const CONSOLE_FRAME_SANDBOX = [
  "allow-scripts",
  "allow-same-origin",
  "allow-forms",
  "allow-popups",
  "allow-popups-to-escape-sandbox",
  "allow-downloads",
  "allow-modals",
].join(" ");

const LOOPBACK_HOST =
  /^(?:localhost|.+\.localhost|127(?:\.\d{1,3}){3}|\[::1\])$/i;

const cleanUrl = (value) => (typeof value === "string" ? value.trim() : "");

// A build may point the frame elsewhere (the Console origin, or a Console dev
// server on another port). Only http(s) URLs without credentials, or
// same-site paths, are accepted.
export function resolveConsoleSrc(value, origin = window.location.origin) {
  const raw = cleanUrl(value);
  if (!raw) return DEFAULT_CONSOLE_SRC;
  try {
    const url = new URL(raw, origin);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return DEFAULT_CONSOLE_SRC;
    }
    if (url.username || url.password) return DEFAULT_CONSOLE_SRC;
    return raw.startsWith("/") && !raw.startsWith("//") ? raw : url.toString();
  } catch {
    return DEFAULT_CONSOLE_SRC;
  }
}

const runtimeConsoleUrl = () => {
  const config =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  const value = config.VITE_OMEGA_CONSOLE_URL;
  return typeof value === "string" && value.trim() ? value : undefined;
};

// Runtime config wins over the build-time value, as in src/config-global.js.
export const consoleSrc = () =>
  resolveConsoleSrc(
    runtimeConsoleUrl() ?? import.meta.env?.VITE_OMEGA_CONSOLE_URL,
  );

export function consoleOrigin(src, origin = window.location.origin) {
  try {
    return new URL(src, origin).origin;
  } catch {
    return null;
  }
}

/**
 * The frame attributes for a Console at `src`: same-origin keeps today's
 * frame (no sandbox); another origin gets the sandbox.
 * @returns {{ allow: string, sandbox: string | undefined, crossOrigin: boolean }}
 */
export function consoleFrameAttributes(src, origin = window.location.origin) {
  const target = consoleOrigin(src, origin);
  const crossOrigin = Boolean(target) && target !== origin;
  if (!crossOrigin) {
    return { allow: CONSOLE_FRAME_ALLOW, sandbox: undefined, crossOrigin };
  }
  const local = LOOPBACK_HOST.test(new URL(target).hostname);
  return {
    allow: local ? LOCAL_CONSOLE_FRAME_ALLOW : CONSOLE_FRAME_ALLOW,
    sandbox: CONSOLE_FRAME_SANDBOX,
    crossOrigin,
  };
}
