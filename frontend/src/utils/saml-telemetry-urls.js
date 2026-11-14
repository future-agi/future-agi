// Normalizes sign-in callback URLs in the Sentry URL fields listed below. It
// does not rewrite other strings or DOM content; normal diagnostics and the
// Replay text/media masking are unchanged.
const FILTERED = "[Filtered protocol URL]";
const CAPABILITY_KEYS = new Set([
  "saml_code",
  "oauth_code",
  "sso_token",
  "access_token",
  "refresh_token",
  "samlresponse",
  "samlrequest",
  "relaystate",
]);
const CALLBACK_KEYS = new Set(["c", "code", "state"]);
const URL_FIELDS = [
  "url",
  "href",
  "from",
  "to",
  "previous",
  "referrer",
  "referer",
  "http.url",
  "url.full",
  "http.target",
  "http.route",
  "http.referrer",
  "url.original",
  "document.referrer",
  "performance.entry.name",
  // Long Animation Frame script metadata can retain the original page URL.
  "browser.script.invoker",
  "code.filepath",
];
const MARKER =
  /[?#&](?:saml_code|oauth_code|sso_token|access_token|refresh_token|samlresponse|samlrequest|relaystate)=/i;
// Capability parameter names are recognized on any path. Error text can carry
// them percent-encoded (`?%73aml_code=`), so also scan one decoding level, the
// same level URLSearchParams applies to structured URL fields. Ordinary
// c/code/state handling stays limited to callback paths below.
const decodeOnce = (value) =>
  value.replace(/%[0-9a-f]{2}/gi, (escape) =>
    String.fromCharCode(parseInt(escape.slice(1), 16)),
  );
const hasCapabilityMarker = (value) =>
  MARKER.test(value) || (value.includes("%") && MARKER.test(decodeOnce(value)));
const BASE = "https://protocol-url.invalid/";
const isCallbackPath = (pathname) =>
  pathname === "/saml2_auth" || pathname.includes("/saml2_auth/");
const hasCallbackMarker = (value) => {
  // Error diagnostics can contain a URL rather than be one. Classify its
  // decoded pathname, not the whole message/query, just as for URL fields.
  for (const [raw] of value.matchAll(/(?:https?:\/\/|\/)[^\s<>"']+/gi)) {
    let parsed;
    try {
      parsed = new URL(raw, BASE);
    } catch {
      const namespace = raw.indexOf("/saml2_auth/");
      if (
        namespace >= 0 &&
        /[?&](?:c|code|state)=/i.test(raw.slice(namespace))
      ) {
        return true;
      }
      continue;
    }
    const fragment = new URLSearchParams(parsed.hash.slice(1));
    if (
      ![...parsed.searchParams.keys(), ...fragment.keys()].some((key) =>
        CALLBACK_KEYS.has(key.toLowerCase()),
      )
    ) {
      continue;
    }
    try {
      if (isCallbackPath(decodeURIComponent(parsed.pathname))) return true;
    } catch {
      // Same fail-closed classification as malformed structured URL paths.
      return true;
    }
  }
  return false;
};

export function scrubProtocolUrl(value) {
  if (typeof value !== "string") return value;
  const marker = hasCapabilityMarker(value) || hasCallbackMarker(value);
  const match = value.match(
    /^(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?((?:https?:\/\/|\/|\?|#)[\s\S]*)$/i,
  );
  if (!match) return marker ? FILTERED : value;
  if (value.length > 32768) return FILTERED;
  const prefix = match[1] ? `${match[1]} ` : "";
  const raw = match[2];
  let parsed;
  try {
    parsed = new URL(raw, BASE);
  } catch {
    return marker ? FILTERED : value;
  }
  let pathname;
  try {
    pathname = decodeURIComponent(parsed.pathname);
  } catch {
    // A malformed path cannot be classified; replace the whole field when it
    // carries callback parameters.
    return marker ||
      Array.from(parsed.searchParams.keys()).some((key) =>
        CALLBACK_KEYS.has(key.toLowerCase()),
      )
      ? FILTERED
      : value;
  }
  const callback = isCallbackPath(pathname);
  const secretKey = (key) =>
    CAPABILITY_KEYS.has(key.toLowerCase()) ||
    (callback && CALLBACK_KEYS.has(key.toLowerCase()));
  let changed = false;
  for (const key of Array.from(parsed.searchParams.keys())) {
    if (secretKey(key)) {
      parsed.searchParams.delete(key);
      changed = true;
    }
  }
  const fragment = new URL(BASE + "?" + parsed.hash.slice(1)).searchParams;
  if (Array.from(fragment.keys()).some(secretKey)) {
    parsed.hash = "";
    changed = true;
  }
  if (!changed) return marker ? FILTERED : value;
  const result =
    /^https?:\/\//i.test(raw) || raw.startsWith("//")
      ? parsed.href
      : parsed.pathname + parsed.search + parsed.hash;
  // A removed key can still appear inside another parameter's value or a
  // fragment route; replace the whole field in that case.
  if (hasCapabilityMarker(result)) return FILTERED;
  return prefix + result;
}

function urlsIn(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return data;
  const result = { ...data };
  for (const key of URL_FIELDS) {
    if (key in result) result[key] = scrubProtocolUrl(result[key]);
  }
  return result;
}

export function scrubSamlBreadcrumb(breadcrumb) {
  if (!breadcrumb || typeof breadcrumb !== "object") return breadcrumb;
  return { ...breadcrumb, data: urlsIn(breadcrumb.data) };
}

export function scrubSamlSpan(span) {
  if (!span || typeof span !== "object") return span;
  return {
    ...span,
    ...(typeof span.description === "string"
      ? { description: scrubProtocolUrl(span.description) }
      : {}),
    ...(span.data ? { data: urlsIn(span.data) } : {}),
    ...(span.attributes ? { attributes: urlsIn(span.attributes) } : {}),
  };
}

export function scrubSamlTelemetryEvent(event) {
  if (!event || typeof event !== "object") return event;
  return {
    ...event,
    ...(event.request ? { request: urlsIn(event.request) } : {}),
    ...(typeof event.message === "string"
      ? { message: scrubProtocolUrl(event.message) }
      : {}),
    ...(event.logentry
      ? {
          logentry: {
            ...event.logentry,
            ...(typeof event.logentry.message === "string"
              ? { message: scrubProtocolUrl(event.logentry.message) }
              : {}),
            ...(typeof event.logentry.formatted === "string"
              ? { formatted: scrubProtocolUrl(event.logentry.formatted) }
              : {}),
            // Parameterized captureMessage keeps the template in message and
            // the interpolated values, such as a URL, in params.
            ...(Array.isArray(event.logentry.params)
              ? { params: event.logentry.params.map(scrubProtocolUrl) }
              : {}),
          },
        }
      : {}),
    ...(Array.isArray(event.exception?.values)
      ? {
          exception: {
            ...event.exception,
            values: event.exception.values.map((value) => ({
              ...value,
              value: scrubProtocolUrl(value.value),
            })),
          },
        }
      : {}),
    ...(typeof event.transaction === "string"
      ? { transaction: scrubProtocolUrl(event.transaction) }
      : {}),
    ...(Array.isArray(event.breadcrumbs)
      ? { breadcrumbs: event.breadcrumbs.map(scrubSamlBreadcrumb) }
      : {}),
    ...(Array.isArray(event.spans)
      ? { spans: event.spans.map(scrubSamlSpan) }
      : {}),
    ...(Array.isArray(event.urls)
      ? { urls: event.urls.map(scrubProtocolUrl) }
      : {}),
    ...(event.contexts
      ? {
          contexts: Object.fromEntries(
            Object.entries(event.contexts).map(([key, value]) => [
              key,
              scrubSamlSpan(urlsIn(value)),
            ]),
          ),
        }
      : {}),
  };
}

export function scrubSamlEnvelopeHeaders(headers) {
  if (typeof headers?.trace?.transaction !== "string") return headers;
  const transaction = scrubProtocolUrl(headers.trace.transaction);
  if (transaction === headers.trace.transaction) return headers;
  // DSC can be cached/shared with other spans. Project this outgoing header
  // without changing the SDK's source object or unrelated sampling metadata.
  return { ...headers, trace: { ...headers.trace, transaction } };
}

export function samlUrlPrivacyIntegration() {
  return {
    name: "SamlUrlPrivacy",
    setup(client) {
      // Standalone span DSC is built before beforeSendSpan. The public hook
      // runs synchronously before transport serialization for every envelope.
      client.on("beforeEnvelope", (envelope) => {
        envelope[0] = scrubSamlEnvelopeHeaders(envelope[0]);
      });
    },
    // Replay metadata does not use beforeSend/Transaction in SDK9.47. Its public
    // event-processor path still covers replay_event.urls before transport.
    processEvent: scrubSamlTelemetryEvent,
  };
}

export function scrubSamlRecordingEvent(event) {
  if (!event || typeof event !== "object") return event;
  // rrweb Meta has data.href; Sentry Custom events include navigation
  // breadcrumbs and performance spans in data.payload. Do not rewrite DOM
  // snapshots, mutation text, or arbitrary recorded application objects.
  if (event.type === 4) return { ...event, data: urlsIn(event.data) };
  if (event.type === 5 && event.data && typeof event.data === "object") {
    return {
      ...event,
      data: {
        ...event.data,
        payload: scrubSamlSpan(scrubSamlBreadcrumb(urlsIn(event.data.payload))),
      },
    };
  }
  return event;
}
