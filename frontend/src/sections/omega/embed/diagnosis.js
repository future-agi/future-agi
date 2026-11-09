// Explains why a framed surface never said hello (EmbeddedSurface waits 6 s).
// It checks, in order: the browser's local-network permission, Safari's block
// on http pages inside an https page, then whether anything answers at the
// frame's origin (a no-cors probe that carries no token or credentials).
// Brave is named only when the probe cannot tell "blocked" from "not running".
import { isLoopbackHost } from "./telemetry/guard";

export const HELLO_TIMEOUT_MS = 6000;
const PROBE_TIMEOUT_MS = 3000;

export const DIAGNOSIS = Object.freeze({
  localNetworkDenied: "local-network-denied",
  safari: "safari",
  brave: "brave",
  framingBlocked: "framing-blocked",
  notRunning: "not-running",
  noResponse: "no-response",
});

const LOCAL_NETWORK_PERMISSIONS = ["loopback-network", "local-network-access"];

async function localNetworkDenied(nav) {
  if (!nav?.permissions?.query) return false;
  for (const name of LOCAL_NETWORK_PERMISSIONS) {
    try {
      // Unknown permission names reject; that just means "not this browser".
      const status = await nav.permissions.query({ name });
      if (status?.state === "denied") return true;
    } catch {
      /* not supported */
    }
  }
  return false;
}

export function isSafari(nav) {
  const ua = nav?.userAgent || "";
  return (
    /Safari\//.test(ua) &&
    !/Chrome\/|Chromium\/|CriOS\/|FxiOS\/|EdgiOS\/|Edg\/|Android/.test(ua)
  );
}

async function isBrave(nav) {
  try {
    return Boolean(nav?.brave && (await nav.brave.isBrave?.()));
  } catch {
    return false;
  }
}

async function probe(url, fetchImpl) {
  const controller =
    typeof AbortController === "undefined" ? null : new AbortController();
  const timer = controller
    ? setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS)
    : null;
  try {
    await fetchImpl(url, {
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: controller?.signal,
    });
    return true;
  } catch {
    return false;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * @param {object} options
 * @param {string} options.src        the frame's URL
 * @param {string} [options.probePath] path answered by the child's server
 *   (Studio: "/studio/v1/openapi.json"); no probe without it
 * @returns {Promise<{cause: string, origin: string}>}
 */
export async function diagnoseMissingHello({
  src,
  probePath,
  nav = typeof navigator === "undefined" ? undefined : navigator,
  fetchImpl = typeof fetch === "undefined" ? undefined : fetch,
  parentProtocol = typeof window === "undefined"
    ? "https:"
    : window.location.protocol,
}) {
  const url = new URL(
    src,
    typeof window === "undefined" ? undefined : window.location.href,
  );
  const result = (cause) => ({ cause, origin: url.origin });
  const local = isLoopbackHost(url.hostname);

  if (local && (await localNetworkDenied(nav))) {
    return result(DIAGNOSIS.localNetworkDenied);
  }
  if (
    isSafari(nav) &&
    parentProtocol === "https:" &&
    url.protocol === "http:"
  ) {
    return result(DIAGNOSIS.safari);
  }
  if (probePath && fetchImpl) {
    const reachable = await probe(
      new URL(probePath, url.origin).toString(),
      fetchImpl,
    );
    if (reachable) return result(DIAGNOSIS.framingBlocked);
    if (local && (await isBrave(nav))) return result(DIAGNOSIS.brave);
    return result(DIAGNOSIS.notRunning);
  }
  if (local && (await isBrave(nav))) return result(DIAGNOSIS.brave);
  return result(DIAGNOSIS.noResponse);
}

// Plain-language copy per cause. Consumers may override any entry.
export function diagnosisCopy(cause, { label = "the page", origin = "" } = {}) {
  switch (cause) {
    case DIAGNOSIS.localNetworkDenied:
      return {
        title: "Your browser blocked the local connection",
        body: `Future AGI needs permission to reach ${label} on this computer. Allow local network access for this site in your browser's site settings, then try again.`,
      };
    case DIAGNOSIS.safari:
      return {
        title: "Safari may be blocking this local page",
        body: `Safari may block http pages on this computer inside a secure site. Try opening ${label} directly instead.`,
      };
    case DIAGNOSIS.brave:
      return {
        title: "Brave Shields may be blocking the connection",
        body: `Turn Shields off for this site and try again, or open ${label} directly.`,
      };
    case DIAGNOSIS.framingBlocked:
      return {
        title: `${capitalize(label)} didn't open inside Future AGI`,
        body: `${origin} answered, but the frame did not connect. It may not allow this site to show it in a frame. Restart it with this site in its allowed framing origins, or open it directly.`,
      };
    case DIAGNOSIS.notRunning:
      return {
        title: `Can't reach ${label} at ${origin}`,
        body: `Start ${label} and try again. If it is running, check the address and port.`,
      };
    default:
      return {
        title: `${capitalize(label)} didn't respond`,
        body: "It may still be starting. Try again in a moment.",
      };
  }
}

function capitalize(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}
