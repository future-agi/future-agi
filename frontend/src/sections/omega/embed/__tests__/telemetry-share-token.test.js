// A remix link's share token never reaches an analytics or error payload:
// not from the Console page, and not from the sign-in page, whose returnTo=
// holds the same path while the user signs in (that page is not a frame page,
// so autocapture runs there).
import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";

import {
  CONNECT_LINK_RULES,
  REDACTED_CONNECT,
  redactEmbedSecrets,
} from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  posthogBeforeSend,
  sentryBeforeBreadcrumb,
  sentryScrubEvent,
} from "../telemetry/sdk-guards";

// The shape EE's replay-share-route.ts signs: rpl_v1.<base64url claim>.<base64url HMAC-SHA256>.
const TOKEN = `rpl_v1.${Buffer.from(
  JSON.stringify({ c: "conv_8f2a", s: "shr_31c9", exp: 1792500000000 }),
).toString(
  "base64url",
)}.${"Qk9tY2F0LXNpZ25hdHVyZS1ub3QtcmVhbC0wMTIzNDU2Nzg5".slice(0, 43)}`;
const REMIX_PATH = `/dashboard/console/remix/${TOKEN}`;
const LOGIN_URL = `http://localhost:3061/auth/jwt/login?${new URLSearchParams({
  returnTo: REMIX_PATH,
})}`;

const applyRules = (rules, text) =>
  rules.reduce((out, rule) => out.replace(rule.regex, rule.replacement), text);

describe("share tokens in telemetry", () => {
  it("removes the token from a remix path and from an encoded returnTo", () => {
    expect(redactEmbedSecrets(REMIX_PATH)).toBe(
      `/dashboard/console/remix/${REDACTED_CONNECT}`,
    );
    expect(LOGIN_URL).toContain(TOKEN);
    const scrubbed = redactEmbedSecrets(LOGIN_URL);
    expect(scrubbed).not.toContain("rpl_v1");
    expect(scrubbed).toContain(REDACTED_CONNECT);
    expect(redactEmbedSecrets(encodeURIComponent(REMIX_PATH))).not.toContain(
      "rpl_v1",
    );
  });

  it("is applied by every SDK hook", () => {
    const posthog = posthogBeforeSend({
      event: "$pageview",
      properties: { $current_url: LOGIN_URL, $pathname: "/auth/jwt/login" },
    });
    expect(JSON.stringify(posthog)).not.toContain("rpl_v1");

    const sentry = sentryScrubEvent({
      request: { url: LOGIN_URL },
      transaction: REMIX_PATH,
      breadcrumbs: [{ category: "navigation", data: { to: REMIX_PATH } }],
    });
    expect(JSON.stringify(sentry)).not.toContain("rpl_v1");

    const crumb = sentryBeforeBreadcrumb({
      category: "navigation",
      data: { from: "/dashboard", to: REMIX_PATH },
    });
    expect(JSON.stringify(crumb)).not.toContain("rpl_v1");

    expect(applyRules(NEW_RELIC_OBFUSCATE_RULES, LOGIN_URL)).not.toContain(
      "rpl_v1",
    );
  });

  it("leaves text that is not a share token alone", () => {
    for (const text of [
      "/dashboard/console/agents",
      "rpl_v1 is the share token prefix",
      "rpl_v1.short.sig",
      "/dashboard/console/remix/",
    ]) {
      expect(applyRules(CONNECT_LINK_RULES, text)).toBe(text);
    }
  });
});
