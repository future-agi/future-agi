import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  PROVIDER_GROUPS,
  PROVIDER_PRESETS,
  REGISTRY_HOSTS,
  claimsEndpoints,
} from "./byok-presets";

// The gateway this console ships with fills a provider's defaults from
// agentcc-gateway/internal/config/provider_presets.go (KnownProviders), and an
// explicit base URL, format and path prefix in the provider config always
// win. The console's presets therefore send each of them, and never
// disagree with presets.go on a host's wire format.
const PRESETS_GO = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../../agentcc-gateway/internal/config/provider_presets.go",
);

function gatewayPresets() {
  const source = readFileSync(PRESETS_GO, "utf8");
  const rows = {};
  const row = /^\s*"([a-z0-9-]+)":\s*\{([^}]*)\},?\s*$/gm;
  let match = row.exec(source);
  while (match) {
    const fields = match[2];
    const field = (name) =>
      (fields.match(new RegExp(`${name}:\\s*"([^"]*)"`)) || [])[1];
    rows[match[1]] = {
      baseUrl: field("BaseURL"),
      apiFormat: field("APIFormat"),
      pathPrefix: field("PathPrefix"),
    };
    match = row.exec(source);
  }
  return rows;
}

describe("BYOK presets against the gateway's presets.go", () => {
  it("reads the gateway's known providers", () => {
    const known = gatewayPresets();
    expect(Object.keys(known)).toEqual(
      expect.arrayContaining(["groq", "together", "azure", "openrouter"]),
    );
    expect(known.azure.apiFormat).toBe("azure");
    expect(known.groq).toEqual({
      baseUrl: "https://api.groq.com/openai",
      apiFormat: "openai",
      pathPrefix: "/v1",
    });
  });

  it("never disagrees with presets.go on a host's wire format", () => {
    const known = gatewayPresets();
    const shared = Object.keys(PROVIDER_PRESETS).filter((id) => known[id]);
    expect(shared.length).toBeGreaterThan(5);
    for (const id of shared) {
      expect(PROVIDER_PRESETS[id].apiFormat, id).toBe(known[id].apiFormat);
    }
  });

  it("sends the base URL and path prefix of every host it offers", () => {
    for (const host of REGISTRY_HOSTS.filter(claimsEndpoints)) {
      const preset = PROVIDER_PRESETS[host.id];
      // Azure's URL is the customer's own resource, entered in the dialog.
      if (preset.apiFormat === "azure") continue;
      expect(preset.baseUrl, host.id).toMatch(/^https:\/\//);
      expect(typeof preset.apiPathPrefix, host.id).toBe("string");
    }
  });

  it("offers a registry host only when it claims an endpoint", () => {
    for (const id of [
      "azure",
      "cerebras",
      "deepinfra",
      "fireworks",
      "groq",
      "huggingface",
      "mistral",
      "openrouter",
      "together",
      "vercel-ai-gateway",
      "xai",
    ]) {
      expect(PROVIDER_PRESETS[id], id).toBeDefined();
    }
    // No route their vendors document: the gateway never sends a key there.
    for (const id of ["anyscale", "perplexity", "replicate"]) {
      expect(claimsEndpoints(REGISTRY_HOSTS.find((h) => h.id === id))).toBe(
        false,
      );
      expect(PROVIDER_PRESETS[id], id).toBeUndefined();
    }
  });

  it("carries the registry's base URL, path prefix, format and docs", () => {
    expect(PROVIDER_PRESETS.together).toMatchObject({
      label: "Together AI",
      baseUrl: "https://api.together.ai",
      apiPathPrefix: "/v1",
      apiFormat: "openai",
      docsUrl: "https://docs.together.ai/docs/inference/openai-compatibility",
      source: "registry",
    });
    expect(PROVIDER_PRESETS.deepinfra).toMatchObject({
      baseUrl: "https://api.deepinfra.com",
      apiPathPrefix: "/v1/openai",
    });
    expect(PROVIDER_PRESETS.groq).toMatchObject({
      baseUrl: "https://api.groq.com/openai",
      apiPathPrefix: "/v1",
      keyPlaceholder: "gsk_...",
    });
  });

  it("restricts a host to the wire formats the registry lists for it", () => {
    expect(PROVIDER_PRESETS.azure.supportedFormats).toEqual(["azure"]);
    expect(PROVIDER_PRESETS.azure.apiFormat).toBe("azure");
    expect(PROVIDER_PRESETS.cerebras.supportedFormats).toEqual(["openai"]);
  });

  it("keeps the native providers the gateway has its own adapters for", () => {
    expect(PROVIDER_PRESETS.openai).toMatchObject({
      label: "OpenAI",
      baseUrl: "https://api.openai.com/v1",
      apiFormat: "openai",
      source: "native",
    });
    expect(PROVIDER_PRESETS.vertex).toMatchObject({ authType: "gcp" });
    expect(PROVIDER_PRESETS.bedrock).toMatchObject({ authType: "aws" });
    for (const id of ["anthropic", "google", "cohere", "custom"]) {
      expect(PROVIDER_PRESETS[id], id).toBeDefined();
    }
  });

  it("lists every preset in exactly one picker group, custom last", () => {
    const grouped = PROVIDER_GROUPS.flatMap((g) => g.ids);
    expect([...grouped].sort()).toEqual(Object.keys(PROVIDER_PRESETS).sort());
    expect(new Set(grouped).size).toBe(grouped.length);
    expect(grouped.at(-1)).toBe("custom");
  });
});
