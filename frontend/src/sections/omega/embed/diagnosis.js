// Explains why a framed surface never said hello (EmbeddedSurface waits 6 s).
// It checks, in order: the browser's local-network permission, Safari's block
// on http pages inside an https page, then whether anything answers at the
// frame's origin (a no-cors probe that carries no token or credentials).
// A probe that gets no answer at all while the permission is still "prompt" is
// the browser holding the request for the person's answer (Chrome's local
// network prompt, open or dismissed): a stopped server refuses at once.
// Brave is named only when the probe cannot tell "blocked" from "not running".
import { isLoopbackHost } from "./telemetry/guard";

export const HELLO_TIMEOUT_MS = 6000;
const PROBE_TIMEOUT_MS = 3000;

export const DIAGNOSIS = Object.freeze({
  localNetworkDenied: "local-network-denied",
  localNetworkPrompt: "local-network-prompt",
  safari: "safari",
  brave: "brave",
  framingBlocked: "framing-blocked",
  notRunning: "not-running",
  noResponse: "no-response",
});

const LOCAL_NETWORK_PERMISSIONS = ["loopback-network", "local-network-access"];

// "denied" when any local-network permission is denied, else "prompt" when one
// still waits for the person, else undefined (granted or not this browser).
async function localNetworkState(nav) {
  if (!nav?.permissions?.query) return undefined;
  let state;
  for (const name of LOCAL_NETWORK_PERMISSIONS) {
    try {
      // Unknown permission names reject; that just means "not this browser".
      const status = await nav.permissions.query({ name });
      if (status?.state === "denied") return "denied";
      if (status?.state === "prompt") state = "prompt";
    } catch {
      /* not supported */
    }
  }
  return state;
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

// "answered", "refused" (it failed on its own) or "timeout" (no answer in
// PROBE_TIMEOUT_MS, so it was aborted).
async function probe(url, fetchImpl) {
  const controller =
    typeof AbortController === "undefined" ? null : new AbortController();
  let timedOut = false;
  const timer = controller
    ? setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, PROBE_TIMEOUT_MS)
    : null;
  try {
    await fetchImpl(url, {
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: controller?.signal,
    });
    return "answered";
  } catch {
    return timedOut ? "timeout" : "refused";
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

  const permission = local ? await localNetworkState(nav) : undefined;
  if (permission === "denied") {
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
    const answer = await probe(
      new URL(probePath, url.origin).toString(),
      fetchImpl,
    );
    if (answer === "answered") return result(DIAGNOSIS.framingBlocked);
    if (local && (await isBrave(nav))) return result(DIAGNOSIS.brave);
    if (answer === "timeout" && permission === "prompt") {
      return result(DIAGNOSIS.localNetworkPrompt);
    }
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
    case DIAGNOSIS.localNetworkPrompt:
      return {
        title: "Your browser is asking to reach apps on this device",
        body: `Future AGI asked your browser for permission to reach ${label} on this computer. When your browser asks, allow it, or allow local network access for this site in your browser's site settings. Then try again.`,
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
