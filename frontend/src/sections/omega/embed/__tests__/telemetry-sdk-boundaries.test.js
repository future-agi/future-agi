import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTH_REPLAY_URL_BLOCKLIST } from "../telemetry/auth-url-rules";
import { redactEmbedSecrets } from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  posthogBeforeSend,
  sentryBeforeAddRecordingEvent,
  sentryBeforeBreadcrumb,
  sentryScrubEvent,
} from "../telemetry/sdk-guards";
import { guardSentryTransport } from "../telemetry/sentry-replay-transport";

const secret = "fake-credential-for-unit-test";
const marker = "auth-secret-redacted";
const encode = (value, depth) => {
  let result = value;
  for (let i = 0; i < depth; i += 1) result = encodeURIComponent(result);
  return result;
};
const apiCases = [
  "/accounts/accept-invitation/fake-user/",
  "/accounts/password-reset-confirm/fake-user/",
  "/model-hub/review-links/",
].flatMap((prefix) => [0, 1, 2, 3].map((depth) => [prefix, depth]));
const transport = () => {
  const base = { send: vi.fn().mockResolvedValue({}), flush: vi.fn() };
  return { base, guarded: guardSentryTransport(base) };
};
afterEach(() => window.history.replaceState(null, "", "/"));

describe("backend credential URL boundaries", () => {
  it.each(apiCases)(
    "scrubs %s at encoding depth %i in every SDK path",
    async (prefix, depth) => {
      const url = encode(
        `https://api.example.invalid${prefix}${secret}/?keep=1`,
        depth,
      );
      const clean = encode(
        `https://api.example.invalid${prefix}${marker}/?keep=1`,
        depth,
      );
      expect(redactEmbedSecrets(url)).toBe(clean);
      for (const category of ["xhr", "fetch"]) {
        expect(
          sentryBeforeBreadcrumb({ category, data: { url } }).data.url,
        ).toBe(clean);
      }
      const span = {
        op: "http.client",
        description: `GET ${url}`,
        data: { url, "http.url": url },
      };
      const result = sentryScrubEvent({ spans: [span] }).spans[0];
      expect(result.description).toBe(`GET ${clean}`);
      expect(result.data).toEqual({ url: clean, "http.url": clean });
      const event = {
        type: 5,
        data: { tag: "performanceSpan", payload: span },
      };
      expect(
        sentryBeforeAddRecordingEvent(event).data.payload.description,
      ).toBe(`GET ${clean}`);
      const { base, guarded } = transport();
      await guarded.send([
        {},
        [[{ type: "replay_recording" }, `{}\n${JSON.stringify([event])}`]],
      ]);
      expect(base.send).toHaveBeenCalledTimes(1);
      expect(JSON.stringify(base.send.mock.calls[0][0])).not.toContain(secret);
      expect(
        NEW_RELIC_OBFUSCATE_RULES.reduce(
          (out, rule) => out.replace(rule.regex, rule.replacement),
          url,
        ),
      ).toBe(clean);
    },
  );
});

describe("non-value and encoding boundaries", () => {
  it("scrubs heatmap URL keys while preserving aliases, cycles and the input", () => {
    const url = `https://app.example.invalid/shared/${secret}`;
    const points = [{ x: 1, y: 2 }];
    const data = { [url]: points };
    data.self = data;
    const input = { event: "$$heatmap", properties: { $heatmap_data: data } };
    const result = posthogBeforeSend(input).properties.$heatmap_data;
    expect(Object.keys(result)).not.toContain(url);
    expect(result[`https://app.example.invalid/shared/${marker}`]).toBe(points);
    expect(result.self).toBe(result);
    expect(data[url]).toBe(points);
  });

  it.each(["#ACCESS_TOKEN=", ";refresh_token=", "?sso_token="])(
    "scrubs %s including triple encoding and case-insensitive replay patterns",
    (separator) => {
      const raw = `/callback${separator}${secret};keep=1`;
      const clean = raw.replace(secret, marker);
      for (const depth of [0, 1, 2, 3]) {
        const encoded = encode(raw, depth).replace(/%[0-9A-F]{2}/g, (match) =>
          match.toLowerCase(),
        );
        const expected = encode(clean, depth).replace(
          /%[0-9A-F]{2}/g,
          (match) => match.toLowerCase(),
        );
        expect(redactEmbedSecrets(encoded)).toBe(expected);
        expect(
          AUTH_REPLAY_URL_BLOCKLIST.some((rule) =>
            new RegExp(rule.url).test(encoded),
          ),
        ).toBe(true);
      }
    },
  );
});

describe("every Sentry envelope item", () => {
  it.each(["span", "transaction", "event", "profile", "log"])(
    "scrubs object and serialized %s items and their headers",
    async (type) => {
      const { base, guarded } = transport();
      const payload = {
        description: `/shared/${secret}`,
        data: { transaction: `/review/${secret}` },
      };
      await guarded.send([
        { trace: { transaction: `/shared/${secret}` } },
        [
          [{ type, filename: `/review/${secret}` }, payload],
          [{ type, length: 1 }, JSON.stringify(payload)],
        ],
      ]);
      expect(base.send).toHaveBeenCalledTimes(1);
      const sent = base.send.mock.calls[0][0];
      expect(JSON.stringify(sent)).not.toContain(secret);
      expect(sent[1][0][1].description).toBe(`/shared/${marker}`);
      expect(JSON.parse(sent[1][1][1]).data.transaction).toBe(
        `/review/${marker}`,
      );
      expect(sent[1][1][0].length).toBe(
        new TextEncoder().encode(sent[1][1][1]).length,
      );
      expect(payload.description).toContain(secret);
    },
  );

  it("fails closed on unreadable non-replay payloads without discarding safe errors", async () => {
    const { base, guarded } = transport();
    await guarded.send([
      {},
      [
        [{ type: "span" }, `not-json-${secret}`],
        [{ type: "attachment" }, new TextEncoder().encode(`/shared/${secret}`)],
        [{ type: "event" }, { message: "ordinary error" }],
      ],
    ]);
    expect(base.send).toHaveBeenCalledWith([
      {},
      [[{ type: "event" }, { message: "ordinary error" }]],
    ]);
  });

  it("scrubs custom serialization before it can bypass the transport", async () => {
    const { base, guarded } = transport();
    const payload = { toJSON: () => ({ url: `/shared/${secret}` }) };
    await guarded.send([{}, [[{ type: "span" }, payload]]]);
    expect(JSON.stringify(base.send.mock.calls[0][0])).not.toContain(secret);
    expect(base.send.mock.calls[0][0][1][0][1].url).toBe(`/shared/${marker}`);
  });

  it("does not reapply the current frame route to previously recorded replay events", async () => {
    const { base, guarded } = transport();
    const event = { type: 4, data: { href: `/shared/${secret}` } };
    window.history.replaceState(null, "", "/dashboard/studio");
    await guarded.send([
      {},
      [[{ type: "replay_recording" }, `{}\n${JSON.stringify([event])}`]],
    ]);
    const events = JSON.parse(
      base.send.mock.calls[0][0][1][0][1].split("\n")[1],
    );
    expect(events).toEqual([{ type: 4, data: { href: `/shared/${marker}` } }]);
  });
});
