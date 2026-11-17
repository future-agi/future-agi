import { afterEach, describe, expect, it } from "vitest";

import { hasCredentialUrl } from "../telemetry/auth-url-rules";
import { redactEmbedSecrets } from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  POSTHOG_REPLAY_URL_BLOCKLIST,
  shouldDropMixpanelEvent,
} from "../telemetry/sdk-guards";

const ORIGIN = "https://app.example.test";
const MARKER = "auth-secret-redacted";
// The model catalog's Access filter writes these into the page address, one
// access= per chosen value; read and write are the same kind of setting.
const SETTINGS = [
  "managed",
  "own_key",
  "both",
  "none",
  "unknown",
  "read",
  "write",
];
// Values shaped like a session: dotted like a JWT, a UUID, hyphenated words,
// digits, a word longer than 24 letters, and a raw value that runs on past an
// encoded & into a refresh value.
const SESSION_SHAPED = [
  "fake.jwt.shape",
  "11111111-2222-3333-4444-555555555555",
  "fake-credential-for-unit-test",
  "fake123",
  "abcdefghijklmnopqrstuvwxyz",
  "managed%26refresh%3Dfake",
];

const encode = (value, depth) => {
  let result = value;
  for (let i = 0; i < depth; i += 1) result = encodeURIComponent(result);
  return result;
};
// The page address itself, and the same address inside next= one and two
// encodings deep, as a sign-in redirect carries it.
const layers = (path) => [
  path,
  `/auth/jwt/login?next=${encode(path, 1)}&outer=1`,
  `/auth/jwt/login?next=${encode(path, 2)}&outer=1`,
];
const obfuscate = (text) =>
  NEW_RELIC_OBFUSCATE_RULES.reduce(
    (out, rule) => out.replace(rule.regex, rule.replacement),
    text,
  );
const replayBlocked = (url) =>
  POSTHOG_REPLAY_URL_BLOCKLIST.some((rule) => new RegExp(rule.url).test(url));

afterEach(() => window.history.replaceState(null, "", "/"));

describe("access= as a page setting", () => {
  it.each(SETTINGS)(
    "sends Mixpanel events and keeps the address with access=%s",
    (value) => {
      const page = `/dashboard/gateway/models?access=${value}&sort=name`;
      window.history.replaceState(null, "", page);
      expect(shouldDropMixpanelEvent()).toBe(false);
      for (const url of layers(page)) {
        expect(hasCredentialUrl(url)).toBe(false);
        expect(redactEmbedSecrets(url)).toBe(url);
        expect(obfuscate(url)).toBe(url);
        expect(replayBlocked(`${ORIGIN}${url}`)).toBe(false);
      }
    },
  );

  it("keeps two Access filters and an access= at the end of the address", () => {
    for (const page of [
      "/dashboard/gateway/models?access=managed&access=own_key&sort=name",
      "/dashboard/gateway/models?sort=name&access=unknown",
      "/dashboard/gateway/models?access=both#models",
    ]) {
      window.history.replaceState(null, "", page);
      expect(shouldDropMixpanelEvent()).toBe(false);
      expect(redactEmbedSecrets(page)).toBe(page);
    }
  });
});

describe("access= values that stay credentials", () => {
  it.each(SESSION_SHAPED)(
    "drops Mixpanel events and redacts access=%s",
    (value) => {
      const page = `/dashboard/gateway/models?access=${value}&sort=name`;
      window.history.replaceState(null, "", page);
      expect(shouldDropMixpanelEvent()).toBe(true);
      expect(redactEmbedSecrets(page)).toBe(
        `/dashboard/gateway/models?access=${MARKER}&sort=name`,
      );
      layers(page).forEach((url, depth) => {
        // The value as it appears at this layer.
        const shown = encode(value, depth);
        expect(url).toContain(shown);
        expect(hasCredentialUrl(url)).toBe(true);
        expect(redactEmbedSecrets(url)).not.toContain(shown);
        expect(obfuscate(url)).not.toContain(shown);
        expect(replayBlocked(`${ORIGIN}${url}`)).toBe(true);
      });
    },
  );

  it("still treats every refresh= value as a credential, a plain word included", () => {
    for (const value of ["read", "fake.jwt.shape"]) {
      const page = `/dashboard/gateway/models?refresh=${value}&sort=name`;
      window.history.replaceState(null, "", page);
      expect(shouldDropMixpanelEvent()).toBe(true);
      expect(redactEmbedSecrets(page)).toBe(
        `/dashboard/gateway/models?refresh=${MARKER}&sort=name`,
      );
    }
  });

  it("drops events on a legacy support sign-in link and redacts both values", () => {
    const page = "/sos?access=fake.jwt.shape&refresh=fake.jwt.shape";
    window.history.replaceState(null, "", page);
    expect(shouldDropMixpanelEvent()).toBe(true);
    expect(redactEmbedSecrets(page)).toBe(
      `/sos?access=${MARKER}&refresh=${MARKER}`,
    );
  });
});
