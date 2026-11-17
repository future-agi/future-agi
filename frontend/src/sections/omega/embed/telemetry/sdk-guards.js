// Per-SDK hooks built on the telemetry guard. Each analytics set-up file
// (utils/PostHog, utils/Mixpanel, newrelic.jsx, main.jsx for Sentry) wires the
// hooks for its own SDK, so frame pages are never autocaptured or replayed and
// no payload carries a connect fragment or a local-server URL.
import {
  CONNECT_LINK_RULES,
  EMBED_FRAME_REPLAY_URL_BLOCKLIST,
  EMBED_FRAME_URL_PATTERNS,
  LOCAL_SERVER_ORIGIN,
  REDACTED_LOCAL_SERVER,
  isEmbedFramePath,
  isLocalServerUrl,
  isOnEmbedFramePath,
  scrubEmbedSecrets,
} from "./guard";

import { AUTH_REPLAY_URL_BLOCKLIST, hasCredentialUrl } from "./auth-url-rules";

// --- PostHog -------------------------------------------------------------

// Events PostHog records on its own from what the user does on the page.
export const POSTHOG_AUTOCAPTURED_EVENTS = Object.freeze([
  "$autocapture",
  "$rageclick",
  "$dead_click",
  "$copy_autocapture",
]);

const eventPathname = (event) => {
  const props = event?.properties || {};
  if (typeof props.$pathname === "string") return props.$pathname;
  if (typeof props.$current_url === "string") {
    try {
      return new URL(props.$current_url).pathname;
    } catch {
      return "";
    }
  }
  return typeof window === "undefined" ? "" : window.location.pathname;
};

// 1.360.2 gzip-packs DOM data before capture unless compress_events is false.
// Never treat compressed bytes as inspectable strings. The config disables
// packing; this boundary fails closed if a recorder still supplies it.
const inspectableSnapshot = (snapshot) => {
  if (
    !snapshot ||
    typeof snapshot !== "object" ||
    snapshot.cv !== undefined ||
    !Number.isInteger(snapshot.type) ||
    snapshot.type < 0 ||
    snapshot.type > 6 ||
    !snapshot.data ||
    typeof snapshot.data !== "object" ||
    Array.isArray(snapshot.data)
  )
    return false;
  if (snapshot.type === 3 && snapshot.data.source === 0) {
    return ["texts", "attributes", "removes", "adds"].every((key) =>
      Array.isArray(snapshot.data[key]),
    );
  }
  return true;
};

// PostHog before_send: drop opaque replay and frame autocapture; scrub all
// inspectable strings, including uncompressed DOM text, attributes and adds.
export function posthogBeforeSend(event) {
  if (!event) return event;
  if (event.event === "$snapshot") {
    const data = event.properties?.$snapshot_data;
    const snapshots = Array.isArray(data) ? data : [data];
    if (!snapshots.every(inspectableSnapshot)) return null;
  }
  if (
    POSTHOG_AUTOCAPTURED_EVENTS.includes(event.event) &&
    isEmbedFramePath(eventPathname(event))
  ) {
    return null;
  }
  return scrubEmbedSecrets(event);
}

// Session-replay network capture: never keep a request to a local server, and
// scrub the rest. The navigation timing entry keeps the URL the page was
// opened with, so it can still carry a connect fragment after the intake.
export function posthogMaskNetworkRequest(request) {
  if (!request) return request;
  if (isLocalServerUrl(request.name) || isLocalServerUrl(request.url)) {
    return null;
  }
  return scrubEmbedSecrets(request);
}

export const POSTHOG_AUTOCAPTURE_URL_IGNORELIST = EMBED_FRAME_URL_PATTERNS;
// PostHog 1.360.2 sends $snapshot through before_send and leaves meta events
// uncompressed. These patterns are suitable for the REMOTE replay blocklist;
// 1.360.2 does not enforce the local session_recording.urlBlocklist option.
// Initialization separately disables replay when opened on a credential URL.
export const POSTHOG_REPLAY_URL_BLOCKLIST = Object.freeze([
  ...EMBED_FRAME_REPLAY_URL_BLOCKLIST,
  ...AUTH_REPLAY_URL_BLOCKLIST,
]);

// --- Mixpanel -------------------------------------------------------------

export const shouldDropMixpanelEvent = (pathname) => {
  const currentUrl = typeof window === "undefined" ? "/" : window.location.href;
  return (
    isEmbedFramePath(
      pathname ??
        (typeof window === "undefined" ? "/" : window.location.pathname),
    ) ||
    hasCredentialUrl(pathname ?? currentUrl) ||
    hasCredentialUrl(currentUrl)
  );
};

// Like the PostHog persistence guard, materialize each property's JSON before
// scrubbing: SDK serialization can expose URL/toJSON values and enumerable
// class/Error fields that the identity-preserving plain-data walker skips.
// Do this at the app's track boundary, not a send hook (batching persists first).
export const normalizeMixpanelProperties = (properties) => {
  if (!properties || typeof properties !== "object") return properties;
  return Object.fromEntries(
    Object.keys(properties).flatMap((key) => {
      try {
        const ancestors = [];
        const serialized = JSON.stringify(
          properties[key],
          function (_name, value) {
            if (typeof value === "bigint") return value.toString();
            if (!value || typeof value !== "object") return value;
            // Keep only this path, so a repeated non-cyclic alias is retained.
            while (
              ancestors.length &&
              ancestors[ancestors.length - 1] !== this
            ) {
              ancestors.pop();
            }
            // Never hand a cycle or an uninspected deep object back to the SDK.
            if (ancestors.length >= 32 || ancestors.includes(value))
              return null;
            ancestors.push(value);
            return value;
          },
        );
        return serialized === undefined ? [] : [[key, JSON.parse(serialized)]];
      } catch {
        // A throwing getter/toJSON must not discard unrelated safe properties
        // or fall back to passing the original, potentially credentialed value.
        return [];
      }
    }),
  );
};

// Retain the shared in-memory scrubber's identity/alias/cycle contract. Product
// event callers normalize first so only scrubbed JSON data reaches Mixpanel.
export const scrubMixpanelProperties = (properties) =>
  scrubEmbedSecrets(properties);

// --- Sentry ---------------------------------------------------------------

// Elements session replay must never record. The Omega frame carries both.
export const SENTRY_REPLAY_BLOCK_SELECTORS = Object.freeze([
  ".rr-block",
  ".ph-no-capture",
]);

export const sentryShouldTraceRequest = (url) => !isLocalServerUrl(url);

const breadcrumbUrls = (breadcrumb) => {
  const data = breadcrumb?.data || {};
  return [data.url, data.from, data.to].filter((v) => typeof v === "string");
};

export function sentryBeforeBreadcrumb(breadcrumb) {
  if (!breadcrumb) return breadcrumb;
  if (breadcrumbUrls(breadcrumb).some((url) => isLocalServerUrl(url))) {
    return null;
  }
  return scrubEmbedSecrets(breadcrumb);
}

const spanTouchesLocalServer = (span) => {
  const data = span?.data || {};
  return [span?.description, data.url, data["http.url"], data["url.full"]].some(
    (value) =>
      typeof value === "string" &&
      // Span descriptions read "GET http://127.0.0.1:4190/x"; test each word.
      value.split(/\s+/).some((part) => isLocalServerUrl(part)),
  );
};

export function sentryScrubEvent(event) {
  if (!event) return event;
  const next = Array.isArray(event.spans)
    ? { ...event, spans: event.spans.filter((s) => !spanTouchesLocalServer(s)) }
    : event;
  return scrubEmbedSecrets(next);
}

// rrweb metadata contains the original page href. Sentry 9.28.1 calls its
// recording hook only for custom events, so the transport also applies this
// hook to meta events at the final uncompressed envelope boundary.
const RRWEB_META_EVENT = 4;
const RRWEB_CUSTOM_EVENT = 5;

// Replay on a frame page sends nothing; elsewhere drop recorded requests to a
// local server (performance spans carry the URL in data or description) and
// scrub connect fragments from the small custom events (the navigation span
// keeps the URL the page was opened with). DOM snapshots are left alone.
export function sentryBeforeAddRecordingEvent(event) {
  if (isOnEmbedFramePath()) return null;
  return sentryScrubRecordingEvent(event);
}

// The transport flush may happen after navigating into a frame. Apply content
// filtering there, not the capture-time current-route decision a second time.
export function sentryScrubRecordingEvent(event) {
  const payload = event?.data?.payload;
  if (payload && spanTouchesLocalServer(payload)) return null;
  return event?.type === RRWEB_CUSTOM_EVENT || event?.type === RRWEB_META_EVENT
    ? scrubEmbedSecrets(event)
    : event;
}

// --- New Relic --------------------------------------------------------------

// Applied to strings on the agent's non-raw harvest paths. Raw session trace,
// replay and logging bypass these rules and are disabled in both init configs.
// The same connect-link rules as the guard, then local-server origins.
export const NEW_RELIC_OBFUSCATE_RULES = Object.freeze([
  ...CONNECT_LINK_RULES,
  Object.freeze({
    regex: LOCAL_SERVER_ORIGIN,
    replacement: REDACTED_LOCAL_SERVER,
  }),
]);

// AJAX calls the agent must not instrument: requests to servers on the user's
// computer (a local Studio and the frame page's diagnosis probe).
export const NEW_RELIC_LOCAL_DENY_LIST = Object.freeze([
  "localhost",
  "127.0.0.1",
  "[::1]",
]);
