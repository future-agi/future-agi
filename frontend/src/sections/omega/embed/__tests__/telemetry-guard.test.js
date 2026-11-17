import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isEmbedFramePath,
  isLocalServerUrl,
  redactEmbedSecrets,
  scrubEmbedSecrets,
} from "../telemetry/guard";
import {
  NEW_RELIC_OBFUSCATE_RULES,
  posthogBeforeSend,
  posthogMaskNetworkRequest,
  sentryBeforeAddRecordingEvent,
  sentryBeforeBreadcrumb,
  sentryScrubEvent,
  sentryShouldTraceRequest,
  shouldDropMixpanelEvent,
} from "../telemetry/sdk-guards";
import { guardSentryTransport } from "../telemetry/sentry-replay-transport";

const TOKEN = "tok_9f2cAb";
const LINK = `https://app.futureagi.com/dashboard/studio#connect=http://127.0.0.1:4190&t=${TOKEN}`;
// The same link with the token first; the index.html intake reads either order.
const LINK_TOKEN_FIRST = `https://app.futureagi.com/dashboard/studio#t=${TOKEN}&connect=http://127.0.0.1:4190`;
const visit = (url) => window.history.replaceState(null, "", url);

afterEach(() => visit("/"));

describe("telemetry guard", () => {
  it("knows which pages frame an Omega surface", () => {
    [
      "/dashboard/studio",
      "/dashboard/studio/runs/1",
      "/dashboard/console",
      "/dashboard/console/agents?x=1",
      "/dashboard/omega/studio/local/runs",
    ].forEach((path) => expect(isEmbedFramePath(path)).toBe(true));
    ["/dashboard/studios", "/dashboard/observe", "/", "/dashboard"].forEach(
      (path) => expect(isEmbedFramePath(path)).toBe(false),
    );
  });

  it("treats loopback servers other than this page as local servers", () => {
    const own = window.location.origin; // jsdom: http://localhost:3000
    expect(
      isLocalServerUrl("http://127.0.0.1:4190/studio/v1/openapi.json"),
    ).toBe(true);
    expect(isLocalServerUrl("http://localhost:4190/")).toBe(true);
    expect(isLocalServerUrl("http://[::1]:4190/")).toBe(true);
    expect(isLocalServerUrl(`${own}/api/x`)).toBe(false);
    expect(isLocalServerUrl("https://api.futureagi.com/x")).toBe(false);
    expect(isLocalServerUrl("not a url")).toBe(false);
  });

  it("redacts connect fragments, raw and percent-encoded, with their token", () => {
    expect(redactEmbedSecrets(LINK)).toBe(
      "https://app.futureagi.com/dashboard/studio#omega-link-redacted",
    );
    const encoded = `dl=${encodeURIComponent(LINK)}&x=1`;
    const out = redactEmbedSecrets(encoded);
    expect(out).not.toContain(TOKEN);
    expect(out).not.toContain("127.0.0.1");
    expect(redactEmbedSecrets("GET http://127.0.0.1:4190/x")).toBe(
      "GET local-server/x",
    );
    expect(redactEmbedSecrets(`${window.location.origin}/a`)).toBe(
      `${window.location.origin}/a`,
    );
  });

  it.each([
    "127.0.0.1:39025",
    "127.12.34.56:4191",
    "[::1]:4191",
    "localhost:4191",
    "LOCALHOST:4191",
    "127.0.0.1%3A39025",
    "127%2E0%2E0%2E1%3A39025",
    "%5B%3A%3A1%5D%3A4191",
    "%5b%3a%3a1%5d%3a4191",
    "localhost%3A4191",
  ])("scrubs a resource span's bare server.address %s", (address) => {
    const span = {
      op: "resource.iframe",
      description: "local-server/?embed=1",
      data: { "server.address": address, "url.scheme": "http" },
    };
    const clean = {
      ...span,
      data: { "server.address": "local-server", "url.scheme": "http" },
    };
    expect(scrubEmbedSecrets(span)).toEqual(clean);
    expect(sentryScrubEvent({ spans: [span] }).spans).toEqual([clean]);
    expect(span.data["server.address"]).toBe(address);
    expect(redactEmbedSecrets(`address="${address}"`)).toBe(
      'address="local-server"',
    );
    expect(scrubEmbedSecrets(clean)).toBe(clean);
  });

  it.each(["127.0.0.1:39025", "localhost:4191", "[::1]:4191"])(
    "scrubs %s inside percent-encoded delimiters without decoding the payload",
    (address) => {
      const text = encodeURIComponent(`address="${address}"&keep=1`);
      expect(redactEmbedSecrets(text)).toBe(
        encodeURIComponent('address="local-server"&keep=1'),
      );
    },
  );

  it("keeps this page's bare host and port, including encoded spelling", () => {
    const host = window.location.host;
    for (const address of [host, host.toUpperCase(), encodeURIComponent(host)]) {
      const span = {
        op: "resource.iframe",
        data: { "server.address": address, "url.scheme": "http" },
      };
      expect(scrubEmbedSecrets(span)).toBe(span);
    }
    expect(redactEmbedSecrets(`${host} 127.0.0.1:39025 ${host}`)).toBe(
      `${host} local-server ${host}`,
    );
  });

  it.each([
    "A local server can use localhost or 127.0.0.1 or [::1].",
    "10.127.0.0.1x",
    "10.127.0.0.1:1",
    "10%2E127.0.0.1%3A1",
    "foo127.0.0.1:1",
    "foo%66127.0.0.1%3A1",
    "foo-localhost:4191",
    "example.localhost:4191",
    "localhost:4191x",
    "127.0.0.1:39025.example",
    "127.0.0.1:390250",
  ])("leaves prose and larger hostname/identifier tokens alone: %s", (text) => {
    expect(redactEmbedSecrets(text)).toBe(text);
  });

  it("keeps hostname-only prose and non-local server addresses unchanged", () => {
    const data = {
      message: "Use localhost or 127.0.0.1 or [::1] for a local server",
      hostname: "localhost",
      "server.address": "api.futureagi.com",
    };
    expect(scrubEmbedSecrets(data)).toBe(data);
    expect(redactEmbedSecrets("localhost")).toBe("localhost");
    expect(redactEmbedSecrets("127.0.0.1")).toBe("127.0.0.1");
  });

  it("keeps a standalone server.port without naming the local address", () => {
    const data = { "server.port": 39025, port: "39025" };
    expect(scrubEmbedSecrets(data)).toBe(data);
  });

  it("redacts the whole fragment when the token comes before connect=", () => {
    expect(redactEmbedSecrets(LINK_TOKEN_FIRST)).toBe(
      "https://app.futureagi.com/dashboard/studio#omega-link-redacted",
    );
    expect(
      redactEmbedSecrets(`/dashboard/studio#x=1&t=${TOKEN}&connect=u`),
    ).toBe("/dashboard/studio#omega-link-redacted");
    const encoded = redactEmbedSecrets(
      `dl=${encodeURIComponent(LINK_TOKEN_FIRST)}&x=1`,
    );
    expect(encoded).not.toContain(TOKEN);
    expect(encoded).toContain("%23omega-link-redacted");
    // A fragment without connect= is left alone, and so is a later one.
    expect(redactEmbedSecrets(`/a#t=${TOKEN}`)).toBe(`/a#t=${TOKEN}`);
    expect(redactEmbedSecrets("/a#/runs/1 /b#connect=u&t=x")).toBe(
      "/a#/runs/1 /b#omega-link-redacted",
    );
  });

  it("stays fast on long strings full of fragment marks", () => {
    // Each `#` or `%23` starts a look-ahead for `connect=`; it must stop at the
    // next mark, or this 400 KB string takes seconds instead of milliseconds.
    const noisy = `${"%23a&".repeat(40000)}${"#b&".repeat(40000)}`;
    const started = performance.now();
    expect(redactEmbedSecrets(noisy)).toBe(noisy);
    expect(performance.now() - started).toBeLessThan(1000);
  });

  it("scrubs nested payloads and returns untouched objects as they are", () => {
    const clean = { a: 1, b: ["x", { c: "y" }] };
    expect(scrubEmbedSecrets(clean)).toBe(clean);
    const dirty = { props: { $current_url: LINK, list: [LINK] }, n: 3 };
    const scrubbed = scrubEmbedSecrets(dirty);
    expect(JSON.stringify(scrubbed)).not.toContain(TOKEN);
    expect(dirty.props.$current_url).toBe(LINK); // input not mutated
  });
});

describe("PostHog hooks", () => {
  it("drops autocaptured events on frame pages only", () => {
    const click = (path) => ({
      event: "$autocapture",
      properties: { $pathname: path },
    });
    expect(posthogBeforeSend(click("/dashboard/studio/runs/1"))).toBeNull();
    expect(
      posthogBeforeSend({
        event: "$rageclick",
        properties: { $pathname: "/dashboard/console" },
      }),
    ).toBeNull();
    expect(posthogBeforeSend(click("/dashboard/observe"))).toEqual(
      click("/dashboard/observe"),
    );
    const pageview = {
      event: "$pageview",
      properties: { $pathname: "/dashboard/studio" },
    };
    expect(posthogBeforeSend(pageview)).toBe(pageview);
  });

  it("scrubs every other event", () => {
    const event = {
      event: "$pageview",
      properties: { $current_url: LINK, $pathname: "/dashboard/studio" },
    };
    expect(JSON.stringify(posthogBeforeSend(event))).not.toContain(TOKEN);
  });

  it("scrubs the navigation entry that still holds the opened URL", () => {
    const entry = { name: LINK, initiatorType: "navigation" };
    const out = posthogMaskNetworkRequest(entry);
    expect(out.name).toBe(
      "https://app.futureagi.com/dashboard/studio#omega-link-redacted",
    );
  });

  it("keeps local-server requests out of replay network capture", () => {
    expect(
      posthogMaskNetworkRequest({
        name: "http://127.0.0.1:4190/studio/v1/runs",
      }),
    ).toBeNull();
    const own = { name: "https://api.futureagi.com/x" };
    expect(posthogMaskNetworkRequest(own)).toBe(own);
  });
});

describe("Sentry hooks", () => {
  it.each([
    ["http://127.0.0.1:80", "127.0.0.1"],
    ["https://localhost:443", "localhost"],
    ["http://localhost:80", "localhost"],
    ["http://[::1]:80", "[::1]"],
    ["https://[::1]:443", "::1"],
    ["http://127.12.34.56:80", "127%2E12%2E34%2E56"],
    ["http://[::1]:80", "%5B%3A%3A1%5D"],
  ])("scrubs normalized default-port addresses from %s", async (url, address) => {
    visit("/dashboard/studio");
    const normalized = new URL(url);
    expect(normalized.port).toBe("");
    const span = {
      op: "resource.iframe",
      description: "local-server/?embed=1",
      data: {
        "server.address": address,
        "url.scheme": normalized.protocol.slice(0, -1),
      },
    };
    const clean = {
      ...span,
      data: { ...span.data, "server.address": "local-server" },
    };
    expect(scrubEmbedSecrets(span)).toEqual(clean);
    expect(sentryScrubEvent({ spans: [span] }).spans).toEqual([clean]);
    expect(
      posthogBeforeSend({ event: "span", properties: span }).properties,
    ).toEqual(clean);
    const sent = [];
    const transport = guardSentryTransport({
      send: (envelope) => {
        sent.push(envelope);
        return Promise.resolve({});
      },
    });
    await transport.send([
      {},
      [
        [{ type: "span" }, span],
        [{ type: "span", length: 1 }, JSON.stringify(span)],
      ],
    ]);
    const serialized = JSON.stringify(clean);
    expect(sent).toEqual([
      [
        {},
        [
          [{ type: "span" }, clean],
          [
            { type: "span", length: new TextEncoder().encode(serialized).length },
            serialized,
          ],
        ],
      ],
    ]);
    expect(JSON.stringify(sent)).not.toContain(address);
    expect(span.data["server.address"]).toBe(address);
    expect(redactEmbedSecrets(`Use ${address} for a local server`)).toBe(
      `Use ${address} for a local server`,
    );
  });

  it.each([
    ["http://localhost", "localhost", "http", undefined, true],
    ["https://localhost", "localhost", "https", 443, true],
    ["http://[::1]", "::1", "http", 80, true],
    ["http://127.0.0.1", "127%2E0%2E0%2E1", "http", 80, true],
    ["http://localhost:3051", "localhost", "http", 3051, true],
    ["http://localhost:3051", "localhost", "http", undefined, false],
    ["http://localhost:3051", "localhost", "http", 80, false],
    ["http://localhost", "localhost", "https", undefined, false],
    ["http://localhost", "localhost", "http", 4191, false],
    ["https://app.futureagi.com", "localhost", "https", undefined, false],
  ])(
    "compares a hostname-only address with the page origin %s (%s, %s, %s)",
    async (origin, address, scheme, port, own) => {
      vi.stubGlobal("window", { location: new URL(origin) });
      try {
        const data = { "server.address": address, "url.scheme": scheme };
        if (port !== undefined) data["server.port"] = port;
        const span = { op: "resource.iframe", data };
        const clean = {
          ...span,
          data: { ...data, "server.address": own ? address : "local-server" },
        };
        expect(scrubEmbedSecrets(span)).toEqual(clean);
        if (own) expect(scrubEmbedSecrets(span)).toBe(span);
        const send = vi.fn().mockResolvedValue({});
        await guardSentryTransport({ send }).send([
          {},
          [[{ type: "span" }, span], [{ type: "span" }, JSON.stringify(span)]],
        ]);
        expect(send).toHaveBeenCalledWith([
          {},
          [[{ type: "span" }, clean], [{ type: "span" }, JSON.stringify(clean)]],
        ]);
        expect(data["server.address"]).toBe(address);
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );

  it("scrubs iframe addresses at the final envelope boundary on the Studio route", async () => {
    visit("/dashboard/studio");
    const sent = [];
    const transport = guardSentryTransport({
      send: (envelope) => {
        sent.push(envelope);
        return Promise.resolve({});
      },
    });
    const span = {
      op: "resource.iframe",
      description: "local-server/?embed=1",
      data: {
        "server.address": "127.0.0.1:39025",
        "server.port": 39025,
        "url.scheme": "http",
      },
    };
    const clean = {
      ...span,
      data: { ...span.data, "server.address": "local-server" },
    };
    await transport.send([
      {},
      [
        [{ type: "span" }, span],
        [{ type: "span" }, JSON.stringify(span)],
      ],
    ]);
    expect(sent).toEqual([
      [{}, [[{ type: "span" }, clean], [{ type: "span" }, JSON.stringify(clean)]]],
    ]);
    expect(JSON.stringify(sent)).not.toContain("127.0.0.1:39025");
    expect(span.data["server.address"]).toBe("127.0.0.1:39025");
  });

  it("does not trace or breadcrumb requests to local servers", () => {
    expect(
      sentryShouldTraceRequest("http://127.0.0.1:4190/studio/v1/openapi.json"),
    ).toBe(false);
    expect(sentryShouldTraceRequest("https://api.futureagi.com/x")).toBe(true);
    expect(
      sentryBeforeBreadcrumb({
        category: "fetch",
        data: { url: "http://127.0.0.1:4190/x" },
      }),
    ).toBeNull();
    const nav = sentryBeforeBreadcrumb({
      category: "navigation",
      data: { from: LINK, to: "/a" },
    });
    expect(JSON.stringify(nav)).not.toContain(TOKEN);
  });

  it("removes local-server spans and connect fragments from events", () => {
    const event = {
      request: { url: LINK },
      spans: [
        { description: "GET http://127.0.0.1:4190/studio/v1/openapi.json" },
        { description: "http://127.0.0.1:4191/child.html", data: {} },
        { description: "GET https://api.futureagi.com/x" },
      ],
    };
    const out = sentryScrubEvent(event);
    expect(out.spans).toEqual([
      { description: "GET https://api.futureagi.com/x" },
    ]);
    expect(JSON.stringify(out)).not.toContain(TOKEN);
  });

  it("records no replay events on frame pages", () => {
    const event = { type: 3, data: {} };
    visit("/dashboard/studio");
    expect(sentryBeforeAddRecordingEvent(event)).toBeNull();
    visit("/dashboard/observe");
    expect(sentryBeforeAddRecordingEvent(event)).toBe(event);
    const span = {
      type: 5,
      data: { payload: { description: "http://127.0.0.1:4190/x" } },
    };
    expect(sentryBeforeAddRecordingEvent(span)).toBeNull();
    const navigation = {
      type: 5,
      data: {
        tag: "performanceSpan",
        payload: { op: "navigation.navigate", description: LINK },
      },
    };
    expect(
      JSON.stringify(sentryBeforeAddRecordingEvent(navigation)),
    ).not.toContain(TOKEN);
  });
});

describe("New Relic and Mixpanel hooks", () => {
  const obfuscate = (text) =>
    NEW_RELIC_OBFUSCATE_RULES.reduce(
      (out, rule) => out.replace(rule.regex, rule.replacement),
      text,
    );

  it("obfuscates connect fragments and local-server origins in harvested strings", () => {
    const out = obfuscate(
      `${LINK} ${encodeURIComponent(LINK)} http://127.0.0.1:4191/child.html`,
    );
    expect(out).not.toContain(TOKEN);
    expect(out).not.toContain("127.0.0.1");
    expect(out).toContain(
      "https://app.futureagi.com/dashboard/studio#omega-link-redacted",
    );
  });

  it("obfuscates a link whose token comes before connect=, raw and encoded", () => {
    const out = obfuscate(
      `${LINK_TOKEN_FIRST} ${encodeURIComponent(LINK_TOKEN_FIRST)}`,
    );
    expect(out).not.toContain(TOKEN);
    expect(out).not.toContain("127.0.0.1");
    expect(out).toContain(
      "https://app.futureagi.com/dashboard/studio#omega-link-redacted",
    );
    NEW_RELIC_OBFUSCATE_RULES.forEach((rule) => {
      expect(rule.regex).toBeInstanceOf(RegExp);
      expect(typeof rule.replacement).toBe("string");
    });
  });

  it("drops Mixpanel events on frame pages", () => {
    expect(shouldDropMixpanelEvent("/dashboard/studio")).toBe(true);
    expect(shouldDropMixpanelEvent("/dashboard/observe")).toBe(false);
  });
});
