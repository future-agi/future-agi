/* eslint-env node */
import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/browser";
import { createSpanEnvelope, SentrySpan } from "@sentry/core";
import {
  startTrackingINP,
  registerInpInteractionListener,
} from "@sentry-internal/browser-utils";

import { redactEmbedSecrets, scrubEmbedSecrets } from "../telemetry/guard";
import { sentryShouldTraceRequest } from "../telemetry/sdk-guards";
import { guardSentryTransport } from "../telemetry/sentry-replay-transport";

vi.mock("@sentry-internal/browser-utils", async (importOriginal) => ({
  ...(await importOriginal()),
  startTrackingINP: vi.fn(),
  registerInpInteractionListener: vi.fn(),
}));

// Read only the tracing expression, never execute main or print its configuration.
const source = fs.readFileSync(
  path.resolve(__dirname, "../../../../main.jsx"),
  "utf8",
);
const optionsText = source.match(
  /Sentry\.browserTracingIntegration\((\{[\s\S]*?)\n {4}\}\),/,
);
// Bind the production switches without evaluating unrelated application code.
const tracingOptions = () => ({
  enableInp: !/enableInp:\s*false/.test(optionsText[1]),
  beforeStartSpan: optionsText[1].includes(
    "name: redactEmbedSecrets(options.name)",
  )
    ? (options) => ({ ...options, name: redactEmbedSecrets(options.name) })
    : undefined,
  shouldCreateSpanForRequest: sentryShouldTraceRequest,
});
const configuredSpanHook = () =>
  /beforeSendSpan:\s*scrubEmbedSecrets/.test(source)
    ? scrubEmbedSecrets
    : undefined;
const clients = [];
const makeClient = (
  integrations = [],
  beforeSendSpan = configuredSpanHook(),
) => {
  const base = {
    send: vi.fn().mockResolvedValue({ statusCode: 200 }),
    flush: vi.fn().mockResolvedValue(true),
  };
  const client = Sentry.init({
    dsn: "https://fake_public_key@sentry.example.invalid/1",
    defaultIntegrations: false,
    integrations,
    tracesSampleRate: 1,
    beforeSendSpan,
    transport: () => guardSentryTransport(base),
  });
  clients.push(client);
  return { client, base };
};
afterEach(async () => {
  await Promise.all(clients.splice(0).map((client) => client.close()));
  vi.clearAllMocks();
});

describe("installed Sentry 9.28.1 standalone pipeline", () => {
  it("disables the real INP producer and creates no standalone span envelope", async () => {
    const options = tracingOptions();
    const { client, base } = makeClient([
      Sentry.browserTracingIntegration({
        ...options,
        instrumentPageLoad: false,
        instrumentNavigation: false,
        enableLongTask: false,
        enableLongAnimationFrame: false,
      }),
    ]);
    document.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await client.flush();
    expect(options.enableInp).toBe(false);
    expect(startTrackingINP).not.toHaveBeenCalled();
    expect(registerInpInteractionListener).not.toHaveBeenCalled();
    expect(
      base.send.mock.calls
        .flatMap(([envelope]) => envelope[1])
        .filter(([header]) => header.type === "span"),
    ).toEqual([]);
  });

  it("scrubs standalone spans at the SDK beforeSendSpan boundary", () => {
    const { client } = makeClient();
    const span = new SentrySpan({
      name: "/shared/fake-credential-for-unit-test",
      attributes: { transaction: "/review/fake-credential-for-unit-test" },
    });
    const envelope = createSpanEnvelope([span], client);
    expect(envelope[1][0][0].type).toBe("span");
    expect(envelope[1][0][1].data.transaction).toBe(
      "/review/auth-secret-redacted",
    );
    expect(envelope[1][0][1].description).toBe("/shared/auth-secret-redacted");
  });

  it("scrubs a real span envelope even when the client hook is absent", async () => {
    const { client, base } = makeClient([], null);
    const span = new SentrySpan({
      name: "/shared/fake-credential-for-unit-test",
      attributes: { transaction: "/review/fake-credential-for-unit-test" },
    });
    await client.sendEnvelope(createSpanEnvelope([span], client));
    expect(base.send).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(base.send.mock.calls[0][0])).not.toContain(
      "fake-credential-for-unit-test",
    );
  });

  it("cleans the route name before it enters scope and Web Vital attribution", () => {
    const options = tracingOptions();
    const input = {
      name: "/shared/fake-credential-for-unit-test",
      op: "pageload",
    };
    expect(typeof options.beforeStartSpan).toBe("function");
    expect(options.beforeStartSpan(input)).toEqual({
      name: "/shared/auth-secret-redacted",
      op: "pageload",
    });
    expect(input.name).toContain("fake-credential-for-unit-test");
  });
});
