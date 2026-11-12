// The Console's own health reports, forwarded to Future AGI's error tracker
// (CP P8, OBS1). The frame sends embed.telemetry with closed fields only
// (additions.js has already dropped anything else, and every product event):
//   error  {name, routeTemplate, release, code, requestId?}  -> one Sentry event
//   vital  {name, routeTemplate, release, valueMs}           -> a breadcrumb and
//                                                               a timed span
// Each Sentry event carries a fixed message plus the request id, so FA's
// ignoreErrors list (which matches messages such as "Loading chunk") cannot
// swallow a Console chunk failure, and two reports of one kind with
// different requests are not merged by the SDK's dedupe; the fingerprint
// groups them into one issue per (name, code, page). FA's beforeSend
// scrubbers run on every event as on any other.
//
// The host keeps its own cap besides the Console's: a frame that floods (a
// bug, or a page that is not the Console) costs at most this many events.
import * as Sentry from "@sentry/react";

export const CONSOLE_TELEMETRY_PRODUCT = "omega-console";
export const MAX_ERRORS_PER_MINUTE = 20;
export const MAX_VITALS_PER_MINUTE = 60;
const MINUTE_MS = 60_000;

function rollingCap(limit, now) {
  const times = [];
  return () => {
    const at = now();
    while (times.length > 0 && at - times[0] >= MINUTE_MS) times.shift();
    if (times.length >= limit) return false;
    times.push(at);
    return true;
  };
}

/**
 * @typedef {Object} ConsoleTelemetrySink
 * @property {(event: Parameters<typeof Sentry.captureEvent>[0]) => unknown} captureEvent
 * @property {(breadcrumb: Parameters<typeof Sentry.addBreadcrumb>[0]) => unknown} addBreadcrumb
 * @property {(options: Parameters<typeof Sentry.startInactiveSpan>[0]) => {end: (time?: number) => void} | undefined} startInactiveSpan
 */
/**
 * @param {{ sentry?: ConsoleTelemetrySink, now?: () => number,
 *   maxErrorsPerMinute?: number, maxVitalsPerMinute?: number }} [options]
 * @returns {{ report: (message: object) => boolean }}
 */
export function createConsoleTelemetry({
  sentry = Sentry,
  now = () => Date.now(),
  maxErrorsPerMinute = MAX_ERRORS_PER_MINUTE,
  maxVitalsPerMinute = MAX_VITALS_PER_MINUTE,
} = {}) {
  const errorAllowed = rollingCap(maxErrorsPerMinute, now);
  const vitalAllowed = rollingCap(maxVitalsPerMinute, now);
  const sent = new Set();

  function error(message) {
    const { name, routeTemplate, release, code, requestId } = message;
    const fingerprint = `${name}|${code}|${requestId ?? "-"}|${routeTemplate}`;
    if (sent.has(fingerprint) || !errorAllowed()) return false;
    sent.add(fingerprint);
    sentry.captureEvent({
      message: requestId
        ? `Omega Console error (${requestId})`
        : "Omega Console error",
      level: "error",
      tags: {
        product: CONSOLE_TELEMETRY_PRODUCT,
        "omega.name": name,
        "omega.code": code,
        "omega.route": routeTemplate,
        "omega.release": release,
        ...(requestId ? { "omega.request_id": requestId } : {}),
      },
      fingerprint: [CONSOLE_TELEMETRY_PRODUCT, name, code, routeTemplate],
    });
    return true;
  }

  function vital(message) {
    const { name, routeTemplate, release, valueMs } = message;
    if (!vitalAllowed()) return false;
    const attributes = {
      product: CONSOLE_TELEMETRY_PRODUCT,
      "omega.route": routeTemplate,
      "omega.release": release,
      "omega.value_ms": valueMs,
    };
    sentry.addBreadcrumb({
      category: `${CONSOLE_TELEMETRY_PRODUCT}.vital`,
      message: name,
      level: "info",
      data: attributes,
    });
    // A span of the measured length, ending now, in the performance view.
    const end = now();
    const span = sentry.startInactiveSpan({
      name: `${CONSOLE_TELEMETRY_PRODUCT} ${name}`,
      op: `ui.${CONSOLE_TELEMETRY_PRODUCT}.vital`,
      startTime: end - valueMs,
      forceTransaction: true,
      attributes,
    });
    span?.end(end);
    return true;
  }

  return {
    report(message) {
      try {
        if (message?.kind === "error") return error(message);
        if (message?.kind === "vital") return vital(message);
      } catch {
        // A tracker that fails must never break the Console page.
      }
      return false;
    },
  };
}
