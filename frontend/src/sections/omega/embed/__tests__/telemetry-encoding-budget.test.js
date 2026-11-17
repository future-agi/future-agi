// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { hasCredentialUrl } from "../telemetry/auth-url-rules";
import { redactEmbedSecrets } from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  posthogBeforeSend,
} from "../telemetry/sdk-guards";
import { guardSentryTransport } from "../telemetry/sentry-replay-transport";

const secret = "fake-credential-for-unit-test";
const encode = (value, depth) => {
  let result = value;
  for (let index = 0; index < depth; index += 1)
    result = encodeURIComponent(result);
  return result;
};
const urls = [
  `/shared/${secret}`,
  `/auth/jwt/invitation/accept/fake-user/${secret}`,
  `/accounts/password-reset-confirm/fake-user/${secret}/`,
  `/callback?access_token=${secret}&keep=1`,
  `/callback#REFRESH_TOKEN=${secret};keep=1`,
];

describe.each([4, 5, 8, 9, 32, 128])(
  "URL encoding inspection at depth %i",
  (depth) => {
    it.each(urls)(
      "scrubs %s across telemetry boundaries and fails closed beyond budget",
      async (raw) => {
        const url = encode(raw, depth);
        expect(hasCredentialUrl(url)).toBe(true);
        const clean = redactEmbedSecrets(url);
        expect(clean).not.toContain(secret);
        expect(clean).toContain("auth-secret-redacted");
        expect(redactEmbedSecrets(clean)).toBe(clean);
        expect(
          JSON.stringify(
            posthogBeforeSend({
              event: "fake event",
              properties: { destination: url },
            }),
          ),
        ).not.toContain(secret);
        expect(
          NEW_RELIC_OBFUSCATE_RULES.reduce(
            (value, rule) => value.replace(rule.regex, rule.replacement),
            url,
          ),
        ).not.toContain(secret);
        const base = { send: vi.fn().mockResolvedValue({}), flush: vi.fn() };
        await guardSentryTransport(base).send([
          {},
          [[{ type: "event" }, { message: url }]],
        ]);
        expect(base.send).toHaveBeenCalledTimes(1);
        expect(JSON.stringify(base.send.mock.calls)).not.toContain(secret);
      },
    );
  },
);
