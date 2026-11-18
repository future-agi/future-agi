import { describe, expect, it } from "vitest";
import {
  scrubProtocolUrl,
  scrubSamlBreadcrumb,
  scrubSamlRecordingEvent,
  scrubSamlTelemetryEvent,
  scrubSamlSpan,
  scrubSamlEnvelopeHeaders,
  samlUrlPrivacyIntegration,
} from "../saml-telemetry-urls";

const initial = "a".repeat(43);
const late = "b".repeat(43);
const initialUrl = `https://app.owned.invalid/auth/jwt/login#saml_code=${initial}`;
const lateUrl = `/dashboard/develop?keep=1&saml_code=${late}`;

describe("known Sentry protocol URL fields", () => {
  it.each([
    [initialUrl, "https://app.owned.invalid/auth/jwt/login"],
    [lateUrl, "/dashboard/develop?keep=1"],
    [`GET ${initialUrl}`, "GET https://app.owned.invalid/auth/jwt/login"],
    [
      `https://api.owned.invalid/%73aml2_auth/complete/?c=${late}`,
      "https://api.owned.invalid/%73aml2_auth/complete/",
    ],
    [`/saml2_auth%2Fcomplete/?c=${late}`, "/saml2_auth%2Fcomplete/"],
    [
      `/backend/%73aml2_auth%2fcomplete/?C=${late}&keep=1`,
      "/backend/%73aml2_auth%2fcomplete/?keep=1",
    ],
    [
      `https://idp.owned.invalid/app/okta/sso/saml/redirect?SAMLRequest=${initial}&RelayState=${late}&keep=1`,
      "https://idp.owned.invalid/app/okta/sso/saml/redirect?keep=1",
    ],
    [`/saml2_auth/%ZZ/complete/?c=${late}`, "[Filtered protocol URL]"],
    [`/%73aml2_auth/%ZZ/complete/?c=${late}`, "[Filtered protocol URL]"],
    [
      `https://api.owned.invalid/saml2_auth/complete/?c=${late}&keep=1`,
      "https://api.owned.invalid/saml2_auth/complete/?keep=1",
    ],
    [
      `https://api.owned.invalid/backend/saml2_auth/complete/?c=${late}`,
      "https://api.owned.invalid/backend/saml2_auth/complete/",
    ],
    [
      `https://app.owned.invalid/?%73aml_code=${late}#ordinary`,
      "https://app.owned.invalid/#ordinary",
    ],
    [
      `/auth/jwt/login?access_token=${late}&refresh_token=${initial}`,
      "/auth/jwt/login",
    ],
    [`https://[broken/?saml_code=${late}`, "[Filtered protocol URL]"],
    [`Failed to execute history for ${initialUrl}`, "[Filtered protocol URL]"],
    [
      `https://app.owned.invalid/dashboard?keep=1&%73AML_CODE=${late}&%52elayState=${initial}`,
      "https://app.owned.invalid/dashboard?keep=1",
    ],
    // An encoded capability key nested in another parameter's value is not a
    // removable key; the URL field fails closed instead of keeping it.
    [
      `/dashboard?next=%2Fauth%2Fjwt%2Flogin%3F%73aml_code%3D${late}`,
      "[Filtered protocol URL]",
    ],
    // The same holds when a sibling capability key was projected out.
    [
      `/x?saml_code=${initial}&next=%2Fy%3Fsaml_code%3D${late}`,
      "[Filtered protocol URL]",
    ],
    [
      `/x?saml_code=${initial}&next=/y?saml_code=${late}`,
      "[Filtered protocol URL]",
    ],
    [
      `/x?saml_code=${initial}#/route?access_token=${late}`,
      "[Filtered protocol URL]",
    ],
    [
      `/x?saml_code=${initial}&next=%2Fy%3Fcode%3Dordinary`,
      "/x?next=%2Fy%3Fcode%3Dordinary",
    ],
  ])("redacts structured protocol URL %s", (input, expected) => {
    expect(scrubProtocolUrl(input)).toBe(expected);
    expect(scrubProtocolUrl(expected)).toBe(expected);
  });

  it.each([
    "https://docs.owned.invalid/?code=javascript#ordinary",
    "/dashboard/?keep=https://docs.owned.invalid/guide#section",
    "Ordinary error with no protocol URL",
    "/saml2_authentication/complete/?c=ordinary",
    "/SAML2_AUTH/complete/?c=case-sensitive-unrelated",
    "a".repeat(40000),
    null,
    undefined,
    42,
  ])("preserves nonprotocol diagnostic %s", (value) => {
    expect(scrubProtocolUrl(value)).toBe(value);
  });

  it.each([
    `Failed to navigate to https://other.invalid/%73aml2_auth%2Fcomplete/?c=${late}`,
    `Cannot navigate to /backend/%73aml2_auth%2fcomplete/?%63=${late}&keep=1`,
    `Cannot navigate to https://other.invalid/%73aml2_auth?state=${late}`,
    `Cannot navigate to /%73aml2_auth%2Fcomplete/#code=${late}`,
    `Cannot navigate to /%73aml2_auth/%ZZ/complete/?c=${late}`,
  ])("projects encoded callback URLs in error text: %s", (message) => {
    expect(scrubProtocolUrl(message)).toBe("[Filtered protocol URL]");
    const event = {
      message,
      logentry: { message, formatted: message },
      exception: { values: [{ type: "SecurityError", value: message }] },
    };
    const original = JSON.stringify(event);
    const safe = scrubSamlTelemetryEvent(event);
    expect(safe).toEqual({
      message: "[Filtered protocol URL]",
      logentry: {
        message: "[Filtered protocol URL]",
        formatted: "[Filtered protocol URL]",
      },
      exception: {
        values: [{ type: "SecurityError", value: "[Filtered protocol URL]" }],
      },
    });
    expect(JSON.stringify(event)).toBe(original);
    expect(scrubSamlTelemetryEvent(safe)).toEqual(safe);
  });

  it.each([
    "Failed to navigate to https://docs.invalid/%67uide/?code=javascript",
    "Failed to navigate to https://docs.invalid/?next=%2Fsaml2_auth%2F&c=ordinary",
    "Cannot navigate to /%73aml2_authentication/complete/?c=ordinary",
    "Cannot navigate to /%53AML2_AUTH/complete/?c=ordinary",
    "Cannot navigate to /%73aml2_auth%2Fcomplete/?keep=ordinary",
  ])("preserves ordinary error diagnostics: %s", (message) => {
    expect(scrubProtocolUrl(message)).toBe(message);
    expect(scrubSamlTelemetryEvent({ message })).toEqual({ message });
  });

  it.each([
    `Failed to navigate to https://other.invalid/auth/jwt/login?%73aml_code=${late}`,
    `Cannot navigate to /dashboard/develop?keep=1&%73AML_CODE=${late}`,
    `Cannot navigate to https://other.invalid/dashboard#%6Fauth_code=${late}`,
    `Cannot navigate to https://other.invalid/#/route?%61ccess_token=${late}`,
    `Navigation rejected ?%73aml_code=${late}`,
    `Navigation rejected ?keep=1&%52elayState=${late}`,
  ])("projects encoded callback parameters in error text: %s", (message) => {
    expect(scrubProtocolUrl(message)).toBe("[Filtered protocol URL]");
    const event = {
      message,
      logentry: { message, formatted: message },
      exception: { values: [{ type: "SecurityError", value: message }] },
    };
    const original = JSON.stringify(event);
    const safe = scrubSamlTelemetryEvent(event);
    expect(safe).toEqual({
      message: "[Filtered protocol URL]",
      logentry: {
        message: "[Filtered protocol URL]",
        formatted: "[Filtered protocol URL]",
      },
      exception: {
        values: [{ type: "SecurityError", value: "[Filtered protocol URL]" }],
      },
    });
    expect(JSON.stringify(safe)).not.toContain(late);
    expect(JSON.stringify(event)).toBe(original);
    expect(scrubSamlTelemetryEvent(safe)).toEqual(safe);
  });

  it.each([
    "Failed to navigate to https://docs.invalid/guide/?%63ode=javascript",
    "Failed to navigate to https://docs.invalid/guide/?keep=1&%73tate=ordinary",
    "Navigation rejected ?%63ode=ordinary&%73tate=ordinary",
    "Failed to navigate to https://docs.invalid/guide/?%73aml_codex=ordinary",
    "Failed to navigate to https://docs.invalid/guide/?keep=%73aml_code",
    "Ordinary 100% complete diagnostic with %ZZ and %",
  ])("preserves ordinary encoded error diagnostics: %s", (message) => {
    expect(scrubProtocolUrl(message)).toBe(message);
    expect(scrubSamlTelemetryEvent({ message })).toEqual({ message });
  });

  it.each([
    [
      `https://other.invalid/auth/jwt/login?saml_code=${late}`,
      "https://other.invalid/auth/jwt/login",
    ],
    [
      `https://other.invalid/auth/jwt/login?%73aml_code=${late}`,
      "https://other.invalid/auth/jwt/login",
    ],
    [
      `Failed to navigate to /dashboard?%73so_token=${late}`,
      "[Filtered protocol URL]",
    ],
    [
      `/backend/%73aml2_auth%2fcomplete/?%63=${late}`,
      "/backend/%73aml2_auth%2fcomplete/",
    ],
  ])("projects parameterized logentry value %s", (param, expected) => {
    // Shape of an actual parameterized captureMessage event.
    const template = "Cannot navigate to %s (attempt %s)";
    const event = { logentry: { message: template, params: [param, 2] } };
    const original = JSON.stringify(event);
    const safe = scrubSamlTelemetryEvent(event);
    expect(safe).toEqual({
      logentry: { message: template, params: [expected, 2] },
    });
    expect(JSON.stringify(safe)).not.toContain(late);
    expect(JSON.stringify(event)).toBe(original);
    expect(scrubSamlTelemetryEvent(safe)).toEqual(safe);
  });

  it("preserves ordinary parameterized logentry values", () => {
    const event = {
      logentry: {
        message: "Cannot navigate to %s after %s attempts (%s)",
        params: [
          "https://docs.invalid/guide/?code=javascript&state=ordinary",
          3,
          null,
        ],
      },
    };
    expect(scrubSamlTelemetryEvent(event)).toEqual(event);
  });

  it("covers actual event/transaction/performance fields without mutating input", () => {
    const event = {
      type: "transaction",
      transaction: initialUrl,
      request: { url: lateUrl, method: "GET" },
      breadcrumbs: [
        {
          category: "navigation",
          data: { from: initialUrl, to: lateUrl, extra: "keep" },
        },
      ],
      spans: [
        {
          op: "browser.load",
          description: initialUrl,
          data: { "http.url": lateUrl, duration: 23 },
          attributes: { "url.full": initialUrl },
        },
      ],
      contexts: {
        trace: {
          op: "navigation",
          description: initialUrl,
          "url.full": lateUrl,
        },
        custom: { diagnostic: "keep" },
      },
      urls: [initialUrl, lateUrl],
      exception: {
        values: [
          { type: "SecurityError", value: `Cannot navigate to ${initialUrl}` },
        ],
      },
      message: "Ordinary safe diagnostic",
    };
    const original = JSON.stringify(event);
    const safe = scrubSamlTelemetryEvent(event);
    expect(JSON.stringify(safe)).not.toContain(initial);
    expect(JSON.stringify(safe)).not.toContain(late);
    expect(safe.spans[0].data.duration).toBe(23);
    expect(safe.exception.values[0].type).toBe("SecurityError");
    expect(safe.message).toBe("Ordinary safe diagnostic");
    expect(JSON.stringify(event)).toBe(original);
  });

  it("redacts navigation before breadcrumb buffering", () => {
    const result = scrubSamlBreadcrumb({
      category: "navigation",
      data: { from: initialUrl, to: lateUrl },
      level: "info",
    });
    expect(result.data).toEqual({
      from: "https://app.owned.invalid/auth/jwt/login",
      to: "/dashboard/develop?keep=1",
    });
    expect(result.level).toBe("info");
  });

  it.each(["data", "attributes"])(
    "projects captured Long Animation Frame URL fields in %s",
    (key) => {
      // Field names/shape from installed SDK9.47.1's actual full-app transaction.
      // Credentials and URL values are synthetic; no captured capability is stored.
      const span = {
        op: "ui.long-animation-frame",
        description: "Main UI thread blocked",
        [key]: {
          "browser.script.invoker": initialUrl,
          "browser.script.invoker_type": "classic-script",
          "code.filepath": initialUrl,
          "code.function": "renderApplication",
          "browser.script.source_char_position": 42,
        },
      };
      const original = JSON.stringify(span);
      const expected = {
        ...span,
        [key]: {
          ...span[key],
          "browser.script.invoker": "https://app.owned.invalid/auth/jwt/login",
          "code.filepath": "https://app.owned.invalid/auth/jwt/login",
        },
      };
      expect(scrubSamlSpan(span)).toEqual(expected);
      expect(
        scrubSamlTelemetryEvent({ type: "transaction", spans: [span] }).spans,
      ).toEqual([expected]);
      expect(
        scrubSamlRecordingEvent({
          type: 5,
          data: { tag: "performanceSpan", payload: span },
        }).data.payload,
      ).toEqual(expected);
      expect(JSON.stringify(span)).toBe(original);
      expect(scrubSamlSpan(expected)).toEqual(expected);
    },
  );

  it.each([
    ["Window.requestAnimationFrame", "src/components/App.jsx"],
    ["setTimeout", "https://assets.owned.invalid/app.js?build=7"],
    ["onClick", "/src/ordinary-file.ts"],
    [null, 42],
  ])(
    "preserves ordinary script invoker %s and source %s",
    (invoker, filepath) => {
      const span = {
        description: "Main UI thread blocked",
        data: {
          "browser.script.invoker": invoker,
          "code.filepath": filepath,
          "code.function": "renderApplication",
          "custom.diagnostic": initialUrl,
        },
      };
      expect(scrubSamlSpan(span)).toEqual(span);
    },
  );

  it("provides a public event processor for Replay metadata URL lists", () => {
    const integration = samlUrlPrivacyIntegration();
    expect(integration.name).toBe("SamlUrlPrivacy");
    expect(
      integration.processEvent({
        type: "replay_event",
        urls: [initialUrl, lateUrl],
      }).urls,
    ).toEqual([
      "https://app.owned.invalid/auth/jwt/login",
      "/dashboard/develop?keep=1",
    ]);
  });

  it("projects actual standalone DSC transaction without mutating cached sampling context", () => {
    const trace = Object.freeze({
      transaction: initialUrl,
      trace_id: "1".repeat(32),
      public_key: "owned",
      sampled: "true",
      sample_rate: "1",
      sample_rand: "0.25",
      release: "owned-release",
      environment: "staging",
    });
    const headers = Object.freeze({ trace, sent_at: "2026-11-03T00:00:00Z" });
    const safe = scrubSamlEnvelopeHeaders(headers);
    expect(safe).toEqual({
      ...headers,
      trace: {
        ...trace,
        transaction: "https://app.owned.invalid/auth/jwt/login",
      },
    });
    expect(headers.trace).toBe(trace);
    expect(trace.transaction).toBe(initialUrl);
    expect(safe.trace).not.toBe(trace);
    expect(scrubSamlEnvelopeHeaders(safe)).toBe(safe);
  });

  it.each([
    { trace: { transaction: "Main UI thread blocked", sampled: "true" } },
    {
      trace: {
        transaction: "/ordinary?code=javascript",
        trace_id: "1".repeat(32),
      },
    },
    { event_id: "2".repeat(32), sent_at: "2026-11-03T00:00:00Z" },
  ])("preserves nonprotocol envelope headers %s", (headers) => {
    expect(scrubSamlEnvelopeHeaders(headers)).toBe(headers);
  });

  it("registers the public transport hook and preserves envelope item identities", () => {
    let hook;
    samlUrlPrivacyIntegration().setup({
      on(name, callback) {
        expect(name).toBe("beforeEnvelope");
        hook = callback;
      },
    });
    const header = {
      trace: { transaction: initialUrl, trace_id: "1".repeat(32) },
    };
    const items = [[{ type: "span" }, { data: { diagnostic: "keep" } }]];
    const envelope = [header, items];
    hook(envelope);
    expect(envelope[0].trace.transaction).toBe(
      "https://app.owned.invalid/auth/jwt/login",
    );
    expect(header.trace.transaction).toBe(initialUrl);
    expect(envelope[1]).toBe(items);
  });

  it.each([
    { type: 4, data: { href: initialUrl, width: 1280, height: 720 } },
    {
      type: 5,
      data: {
        tag: "breadcrumb",
        payload: {
          category: "navigation",
          data: { from: initialUrl, to: lateUrl },
        },
      },
    },
    {
      type: 5,
      data: {
        tag: "performanceSpan",
        payload: {
          op: "browser.load",
          description: initialUrl,
          data: { previous: lateUrl },
        },
      },
    },
  ])("covers actual rrweb URL fields before compression", (input) => {
    const result = scrubSamlRecordingEvent(input);
    expect(JSON.stringify(result)).not.toContain(initial);
    expect(JSON.stringify(result)).not.toContain(late);
    expect(result.type).toBe(input.type);
  });

  it("does not replace Replay DOM masking or arbitrary application content", () => {
    const snapshot = {
      type: 2,
      data: { node: { textContent: "ordinary rendered content" } },
    };
    expect(scrubSamlRecordingEvent(snapshot)).toBe(snapshot);
    const custom = {
      type: 5,
      data: {
        tag: "custom",
        payload: { message: "ordinary content", data: { counter: 2 } },
      },
    };
    expect(scrubSamlRecordingEvent(custom)).toEqual(custom);
  });
});
