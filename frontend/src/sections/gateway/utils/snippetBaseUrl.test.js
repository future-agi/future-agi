import { describe, expect, it } from "vitest";

import {
  autoSnippets,
  gatewayBaseUrl,
  gatewayExtrasSnippet,
  modelSnippets,
} from "../models/snippets";
import { baseUrlOf, quickstartSnippets } from "../overview/quickstart";
import {
  BASE_URL_UNAVAILABLE,
  PLACEHOLDER_BASE_URL,
  snippetBaseUrl,
  trimBaseUrl,
} from "./snippetBaseUrl";

// R-LIT classified (UI copy): the placeholder a self-hosted deployment's
// requests show.
const PLACEHOLDER_URL = "https://YOUR_GATEWAY_URL/v1";

// R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host),
// the value AGENTCC_GATEWAY_PUBLIC_URL would give a hosted deployment.
const PUBLIC_URL = "https://gateway.example.test/v1";
// R-LIT synthetic: the local address the platform reports when no public URL
// is set (agentcc/views/gateway.py builds base_url from it).
const LOCAL_URL = "http://localhost:8090/v1";
// R-LIT synthetic: model ids no catalog lists.
const CHAT_MODEL = "example-vendor/example-chat-model";
const EMBEDDING_MODEL = "example-vendor/example-embedding-model";

// Every request example the console builds on a base URL: a model's chat
// (plain and streamed) and embeddings requests, the gateway-headers example,
// the Auto routing example and the Overview quickstart.
const examples = (baseUrl) => [
  ...modelSnippets(baseUrl, CHAT_MODEL, { type: "chat" }),
  ...modelSnippets(baseUrl, CHAT_MODEL, { type: "chat", stream: true }),
  ...modelSnippets(baseUrl, EMBEDDING_MODEL, { type: "embeddings" }),
  { key: "headers", code: gatewayExtrasSnippet(baseUrl, CHAT_MODEL) },
  ...autoSnippets(baseUrl),
  ...quickstartSnippets(baseUrl, CHAT_MODEL),
];

describe("trimBaseUrl", () => {
  it("trims the URL and its trailing slashes", () => {
    expect(trimBaseUrl(`  ${PUBLIC_URL}//  `)).toBe(PUBLIC_URL);
    expect(trimBaseUrl(PUBLIC_URL)).toBe(PUBLIC_URL);
  });

  it("reads no URL as an empty string", () => {
    expect(trimBaseUrl(undefined)).toBe("");
    expect(trimBaseUrl(null)).toBe("");
    expect(trimBaseUrl("   ")).toBe("");
  });
});

describe("snippetBaseUrl", () => {
  it("shows a hosted deployment's configured public URL", () => {
    expect(
      snippetBaseUrl(PUBLIC_URL, {
        hosted: true,
        deploymentModeConfirmed: true,
      }),
    ).toEqual({
      kind: "configured",
      url: PUBLIC_URL,
    });
    expect(
      snippetBaseUrl(`${PUBLIC_URL}/`, {
        hosted: true,
        deploymentModeConfirmed: true,
      }).url,
    ).toBe(PUBLIC_URL);
  });

  it("gives a hosted deployment with no URL the marked placeholder, never a real-looking address", () => {
    for (const none of [undefined, null, "", "   "]) {
      expect(
        snippetBaseUrl(none, { hosted: true, deploymentModeConfirmed: true }),
      ).toEqual({
        kind: "unavailable",
        url: PLACEHOLDER_URL,
      });
    }
  });

  // R-LIT synthetic: public addresses a self-hosted gateway may report: an
  // RFC 2606 .test name (with and without a trailing slash), an RFC 5737
  // documentation IPv4 address and an RFC 3849 documentation IPv6 address.
  // The two .test names whose labels merely contain "svc" and "local" show
  // that only the in-cluster suffixes are refused, not those words.
  it.each([
    [PUBLIC_URL, PUBLIC_URL],
    [`  ${PUBLIC_URL}/  `, PUBLIC_URL],
    ["http://203.0.113.10:8090/v1", "http://203.0.113.10:8090/v1"],
    ["http://[2001:db8::10]:8090/v1", "http://[2001:db8::10]:8090/v1"],
    ["https://svc.example.test/v1", "https://svc.example.test/v1"],
    ["https://local-gateway.example.test/v1", "https://local-gateway.example.test/v1"],
  ])(
    "shows a self-hosted deployment its gateway's public address, as the Endpoint row does: %s",
    (reported, url) => {
      expect(
        snippetBaseUrl(reported, {
          hosted: false,
          deploymentModeConfirmed: true,
        }),
      ).toEqual({ kind: "configured", url });
    },
  );

  it("shows a self-hosted deployment with no address the placeholder", () => {
    for (const reported of [undefined, null, "", "   "]) {
      expect(
        snippetBaseUrl(reported, {
          hosted: false,
          deploymentModeConfirmed: true,
        }),
      ).toEqual({
        kind: "placeholder",
        url: PLACEHOLDER_URL,
      });
    }
  });

  // R-LIT synthetic: addresses a reader outside the deployment cannot call:
  // the platform's local default and loopback forms, the placeholder itself,
  // a non-web scheme, an unparsable value, and in-cluster names (a
  // single-label compose or Kubernetes service name, with or without a
  // trailing dot, and .svc, .cluster.local, .internal and .local names).
  it.each([
    LOCAL_URL,
    "https://LOCALHOST.:8090/v1",
    "http://api.localhost:8090/v1",
    "http://127.0.0.1:8090/v1",
    "http://127.1:8090/v1",
    "http://2130706433:8090/v1",
    "http://[::1]:8090/v1",
    "http://[::ffff:127.0.0.1]:8090/v1",
    PLACEHOLDER_URL,
    "https://your_gateway_url/v1/",
    "ftp://gateway.example.test/v1",
    "not a url",
    "http://agentcc-gateway:8090/v1",
    "http://AGENTCC-GATEWAY.:8090/v1",
    "http://agentcc-gateway.agentcc.svc:8090/v1",
    "http://agentcc-gateway.agentcc.svc.cluster.local:8090/v1",
    "http://agentcc-gateway.agentcc.svc.cluster.local.:8090/v1",
    "http://gateway.corp.internal:8090/v1",
    "http://gateway.local:8090/v1",
  ])(
    "shows a self-hosted deployment the placeholder for an address a reader cannot call: %s",
    (reported) => {
      expect(
        snippetBaseUrl(reported, {
          hosted: false,
          deploymentModeConfirmed: true,
        }),
      ).toEqual({
        kind: "placeholder",
        url: PLACEHOLDER_URL,
      });
    },
  );

  it("uses the marked placeholder until deployment mode is confirmed", () => {
    for (const options of [
      undefined,
      {},
      { hosted: false },
      { hosted: true },
      { hosted: false, deploymentModeConfirmed: false },
      { hosted: true, deploymentModeConfirmed: false },
    ]) {
      expect(snippetBaseUrl(PUBLIC_URL, options)).toEqual({
        kind: "unavailable",
        url: PLACEHOLDER_URL,
      });
    }
  });

  // R-LIT synthetic: local/default addresses, including URL-normalized IPv4
  // and IPv6 loopback forms; the placeholder is classified UI copy.
  it.each([
    LOCAL_URL,
    "https://LOCALHOST.:8090/v1",
    "http://api.localhost:8090/v1",
    "http://127.0.0.1:8090/v1",
    "http://127.23.4.5:8090/v1",
    "http://127.1:8090/v1",
    "http://2130706433:8090/v1",
    "http://[::1]:8090/v1",
    "http://[0:0:0:0:0:0:0:1]:8090/v1",
    "http://[::ffff:127.0.0.1]:8090/v1",
    PLACEHOLDER_URL,
    "https://your_gateway_url/v1/",
  ])("builds a hosted request at a non-public default on the marked placeholder: %s", (baseUrl) => {
    expect(
      snippetBaseUrl(baseUrl, {
        hosted: true,
        deploymentModeConfirmed: true,
      }),
    ).toEqual({ kind: "unavailable", url: PLACEHOLDER_URL });
  });

  it("keeps one placeholder: the snippet builders fall back to the same text", () => {
    expect(PLACEHOLDER_BASE_URL).toBe(PLACEHOLDER_URL);
    expect(gatewayBaseUrl("")).toBe(PLACEHOLDER_URL);
    expect(baseUrlOf("")).toBe(PLACEHOLDER_URL);
  });

  it("words the no-URL note with the placeholder to replace, never a local address", () => {
    expect(BASE_URL_UNAVAILABLE).toMatch(/YOUR_GATEWAY_URL/);
    expect(BASE_URL_UNAVAILABLE).toMatch(/Replace it with your gateway's address/);
    expect(BASE_URL_UNAVAILABLE).not.toMatch(/localhost|127\.0\.0\.1/i);
  });
});

describe("request examples on the base URL a page resolves", () => {
  it("builds every example (a count the cases below rely on)", () => {
    expect(examples(PUBLIC_URL)).toHaveLength(16);
  });

  it("on a hosted deployment all carry the configured public URL, none the placeholder or localhost", () => {
    const { url } = snippetBaseUrl(PUBLIC_URL, {
      hosted: true,
      deploymentModeConfirmed: true,
    });
    for (const example of examples(url)) {
      expect(example.code, example.key).toContain(PUBLIC_URL);
      expect(example.code, example.key).not.toMatch(
        /YOUR_GATEWAY_URL|localhost/i,
      );
    }
  });

  it("on a self-hosted deployment with a public address all carry that address, none the placeholder or localhost", () => {
    const { url } = snippetBaseUrl(PUBLIC_URL, {
      hosted: false,
      deploymentModeConfirmed: true,
    });
    for (const example of examples(url)) {
      expect(example.code, example.key).toContain(PUBLIC_URL);
      expect(example.code, example.key).not.toMatch(
        /YOUR_GATEWAY_URL|localhost/i,
      );
    }
  });

  // R-LIT synthetic: the local default and an in-cluster service name.
  it.each([
    [LOCAL_URL, /localhost/i],
    ["http://agentcc-gateway:8090/v1", /agentcc-gateway/i],
  ])(
    "on a self-hosted deployment at %s all carry the placeholder, none the reported address",
    (reported, address) => {
      const { url } = snippetBaseUrl(reported, {
        hosted: false,
        deploymentModeConfirmed: true,
      });
      for (const example of examples(url)) {
        expect(example.code, example.key).toContain(PLACEHOLDER_URL);
        expect(example.code, example.key).not.toMatch(address);
      }
    },
  );
});
