/* eslint-env node */
import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { redactEmbedSecrets } from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  POSTHOG_REPLAY_URL_BLOCKLIST,
  posthogBeforeSend,
  scrubMixpanelProperties,
  sentryBeforeAddRecordingEvent,
  sentryBeforeBreadcrumb,
  sentryScrubEvent,
  shouldDropMixpanelEvent,
} from "../telemetry/sdk-guards";

const SECRET = "fake-credential-for-unit-test";
const MARKER = "auth-secret-redacted";
const PARAMS = [
  "sso_token",
  "access_token",
  "refresh_token",
  "onboarding_token",
  "onboarding_gcp_token",
  "access",
  "refresh",
];
const PATHS = [
  "/auth/jwt/invitation/accept/fake-user/",
  "/auth/jwt/invitation/set-password/fake-user/",
  "/auth/jwt/verify/fake-user/",
  "/shared/",
  "/review/",
];
const encode = (value, depth) => {
  let result = value;
  for (let i = 0; i < depth; i += 1) result = encodeURIComponent(result);
  return result;
};
const cases = [
  ...PARAMS.map((param) => [
    param,
    `/callback?keep=1&${param}=${SECRET}&after=2#tab`,
  ]),
  ...PATHS.map((prefix) => [prefix, `${prefix}${SECRET}?keep=1#tab`]),
].flatMap(([label, url]) =>
  [0, 1, 2].flatMap((depth) =>
    (depth ? ["next", "returnTo"] : ["direct"]).map((param) => [
      `${label} encoding=${depth} via=${param}`,
      depth ? `/auth/jwt/login?${param}=${encode(url, depth)}&outer=1` : url,
      depth
        ? `/auth/jwt/login?${param}=${encode(url.replace(SECRET, MARKER), depth)}&outer=1`
        : url.replace(SECRET, MARKER),
    ]),
  ),
);
const obfuscate = (text) =>
  NEW_RELIC_OBFUSCATE_RULES.reduce(
    (out, rule) => out.replace(rule.regex, rule.replacement),
    text,
  );

afterEach(() => window.history.replaceState(null, "", "/"));

describe.each(cases)("credential URL %s", (label, url, clean) => {
  it(`redacts only the secret in ${label} and preserves the rest of the URL`, () => {
    expect(redactEmbedSecrets(url)).toBe(clean);
    expect(redactEmbedSecrets(clean)).toBe(clean);
  });

  it("scrubs Sentry errors, transactions, names, spans and all breadcrumb URLs", () => {
    for (const type of [undefined, "transaction"]) {
      const input = {
        type,
        request: { url },
        transaction: url,
        exception: { values: [{ value: `Failed ${url}` }] },
        spans: [{ description: `GET ${url}`, data: { "url.full": url } }],
        breadcrumbs: [{ category: "navigation", data: { from: url, to: url } }],
      };
      const result = sentryScrubEvent(input);
      expect(result.request.url).toBe(clean);
      expect(result.transaction).toBe(clean);
      expect(result.exception.values[0].value).toBe(`Failed ${clean}`);
      expect(result.spans[0].description).toBe(`GET ${clean}`);
      expect(result.spans[0].data["url.full"]).toBe(clean);
      expect(result.breadcrumbs[0].data).toEqual({ from: clean, to: clean });
      expect(input.request.url).toBe(url);
    }
    for (const category of ["navigation", "fetch", "xhr"]) {
      expect(
        sentryBeforeBreadcrumb({ category, data: { from: url, to: url, url } }),
      ).toEqual({
        category,
        data: { from: clean, to: clean, url: clean },
      });
    }
  });

  it("scrubs Sentry replay meta href and custom navigation spans", () => {
    const meta = {
      type: 4,
      timestamp: 1,
      data: { href: url, width: 800, height: 600 },
    };
    expect(sentryBeforeAddRecordingEvent(meta)).toEqual({
      ...meta,
      data: { ...meta.data, href: clean },
    });
    expect(
      sentryBeforeAddRecordingEvent({
        type: 5,
        data: { payload: { description: url } },
      }),
    ).toEqual({ type: 5, data: { payload: { description: clean } } });
  });

  it("scrubs PostHog page properties, initial properties, nested strings and replay meta", () => {
    const properties = {
      $current_url: url,
      $pathname: url,
      $referrer: url,
      $initial_current_url: url,
      $initial_pathname: url,
      $initial_referrer: url,
      nested: [{ text: url }],
    };
    const result = posthogBeforeSend({ event: "$pageview", properties });
    for (const key of Object.keys(properties).filter(
      (key) => key !== "nested",
    )) {
      expect(result.properties[key]).toBe(clean);
    }
    expect(result.properties.nested[0].text).toBe(clean);
    const snapshot = posthogBeforeSend({
      event: "$snapshot",
      properties: {
        $snapshot_data: [{ type: 4, data: { href: url } }],
      },
    });
    expect(snapshot.properties.$snapshot_data[0].data.href).toBe(clean);
  });

  it("scrubs Mixpanel supplied properties and New Relic harvested strings", () => {
    expect(scrubMixpanelProperties({ url, nested: [url] })).toEqual({
      url: clean,
      nested: [clean],
    });
    expect(obfuscate(url)).toBe(clean);
  });
});

describe("credential telemetry integration", () => {
  it("scrubs arbitrarily nested strings without the former depth-eight escape", () => {
    let nested = { url: `/callback?sso_token=${SECRET}&keep=1` };
    for (let i = 0; i < 64; i += 1) nested = { child: nested };
    const result = posthogBeforeSend({
      event: "fake event",
      properties: nested,
    });
    let leaf = result.properties;
    for (let i = 0; i < 64; i += 1) leaf = leaf.child;
    expect(leaf.url).toBe(`/callback?sso_token=${MARKER}&keep=1`);
    expect(result.properties).not.toBe(nested);
  });

  it("preserves shared references and cycles without leaving an unscrubbed alias", () => {
    const properties = { url: `/shared/${SECRET}`, safe: { label: "public" } };
    properties.self = properties;
    const result = scrubMixpanelProperties(properties);
    expect(result.url).toBe(`/shared/${MARKER}`);
    expect(result.self).toBe(result);
    expect(result.safe).toBe(properties.safe);
    expect(properties.url).toBe(`/shared/${SECRET}`);
    const clean = { label: "public" };
    clean.self = clean;
    expect(scrubMixpanelProperties(clean)).toBe(clean);
  });
  it.each(PARAMS)(
    "removes all of an encoded %s value without leaking its suffix",
    (param) => {
      const url = `/callback?${param}=fake%26credential%3Dsuffix%23end&keep=1`;
      const clean = `/callback?${param}=${MARKER}&keep=1`;
      expect(redactEmbedSecrets(url)).toBe(clean);
      expect(redactEmbedSecrets(encodeURIComponent(url))).toBe(
        encodeURIComponent(clean),
      );
      expect(obfuscate(url)).toBe(clean);
    },
  );
  it.each(PARAMS)(
    "drops Mixpanel tracking when %s is in the current URL",
    (param) => {
      window.history.replaceState(null, "", `/callback?${param}=${SECRET}`);
      expect(shouldDropMixpanelEvent()).toBe(true);
    },
  );

  it.each(PATHS)("blocks replay and Mixpanel tracking on %s", (prefix) => {
    const url = `https://app.example.test${prefix}${SECRET}?keep=1`;
    expect(shouldDropMixpanelEvent(`${prefix}${SECRET}`)).toBe(true);
    expect(
      POSTHOG_REPLAY_URL_BLOCKLIST.some((rule) =>
        new RegExp(rule.url).test(url),
      ),
    ).toBe(true);
  });

  it("does not treat similarly named public routes or parameters as credentials", () => {
    for (const value of [
      "/reviews/public",
      "/shared",
      "/review",
      "/auth/jwt/verify/fake-user",
      "/callback?not_access_token=public&is_new_user=true",
    ]) {
      expect(redactEmbedSecrets(value)).toBe(value);
      expect(shouldDropMixpanelEvent(value)).toBe(false);
    }
  });

  it("keeps the installed SDK wiring at the actual outbound boundaries", () => {
    const read = (relative) =>
      fs.readFileSync(path.resolve(__dirname, relative), "utf8");
    // Boolean assertions deliberately never dump complete application sources
    // on a red run: those files may include deployment configuration.
    const contains = (source, expected) =>
      expect(source.includes(expected)).toBe(true);
    const main = read("../../../../main.jsx");
    contains(main, "return sentryScrubEvent(event)");
    contains(main, "beforeSendTransaction: sentryScrubEvent");
    contains(main, "beforeBreadcrumb: sentryBeforeBreadcrumb");
    contains(main, "beforeAddRecordingEvent: sentryBeforeAddRecordingEvent");
    contains(main, "useCompression: false");
    contains(main, "guardSentryTransport(Sentry.makeFetchTransport(options))");
    const mixpanel = read("../../../../utils/Mixpanel/mixpanel.js");
    contains(
      mixpanel,
      'property_blacklist: ["$current_url", "$referrer", "$initial_referrer"]',
    );
    contains(mixpanel, "shouldDropMixpanelEvent(pathname)");
    const posthog = read("../../../../utils/PostHog/posthog.js");
    contains(posthog, "before_send: posthogBeforeSend");
    contains(posthog, "...POSTHOG_REPLAY_URL_BLOCKLIST");
    const newrelic = read("../../../../newrelic.jsx");
    contains(newrelic, "NEW_RELIC_OBFUSCATE_RULES");
    NEW_RELIC_OBFUSCATE_RULES.forEach((rule) => {
      expect(rule.regex).toBeInstanceOf(RegExp);
      expect(typeof rule.replacement).toBe("string");
    });
  });
});
