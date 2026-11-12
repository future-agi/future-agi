import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  catalogFreshness,
  filterModels,
  formatPerMillion,
  formatTokenCount,
  modelCapabilities,
  modelDisplayName,
  sortModels,
  vendorLabel,
  vendorOptions,
} from "./catalog";
import { curlSnippet, gatewayBaseUrl, pythonSnippet } from "./snippets";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

const ids = (models) => models.map((m) => m.id);
const filter = (overrides) =>
  ids(filterModels(CATALOG_MODELS, { ...DEFAULT_FILTERS, ...overrides }));

describe("filterModels", () => {
  it("returns every model with no filters", () => {
    expect(filter({})).toHaveLength(CATALOG_MODELS.length);
  });

  it("matches every search term against id, name and vendor", () => {
    expect(filter({ search: "claude" })).toEqual([
      "anthropic/claude-sonnet-5.5",
    ]);
    expect(filter({ search: "OpenAI mini" })).toEqual(["openai/gpt-5-mini"]);
    // The vendor's display name counts, not just its slug.
    expect(filter({ search: "meta" })).toEqual([
      "meta-llama/llama-4-scout:free",
    ]);
    expect(filter({ search: "claude mini" })).toEqual([]);
  });

  it("filters by vendor, including models with no vendor", () => {
    expect(filter({ vendor: "openai" })).toEqual(["openai/gpt-5-mini"]);
    expect(filter({ vendor: "__none__" })).toEqual(["mystery-model"]);
  });

  it("requires every selected capability", () => {
    expect(filter({ capabilities: ["tools"] })).toEqual([
      "anthropic/claude-sonnet-5.5",
      "openai/gpt-5-mini",
    ]);
    // response_format counts as structured output as well as structured_outputs.
    expect(filter({ capabilities: ["structured"] })).toEqual([
      "anthropic/claude-sonnet-5.5",
      "openai/gpt-5-mini",
    ]);
    expect(filter({ capabilities: ["vision", "reasoning"] })).toEqual([
      "anthropic/claude-sonnet-5.5",
    ]);
    expect(filter({ capabilities: ["reasoning"] })).toEqual([
      "anthropic/claude-sonnet-5.5",
      "perplexity/sonar-deep-research",
    ]);
  });

  it("filters by minimum context", () => {
    expect(filter({ minContext: 200000 })).toEqual([
      "anthropic/claude-sonnet-5.5",
      "openai/gpt-5-mini",
    ]);
    expect(filter({ minContext: 1000000 })).toEqual([
      "anthropic/claude-sonnet-5.5",
    ]);
  });
});

describe("sortModels", () => {
  it("puts the newest first by default", () => {
    expect(ids(sortModels(CATALOG_MODELS)).slice(0, 3)).toEqual([
      "anthropic/claude-sonnet-5.5",
      "perplexity/sonar-deep-research",
      "openai/gpt-5-mini",
    ]);
  });

  it("sorts by billed input price, unpriced models last either way", () => {
    expect(ids(sortModels(CATALOG_MODELS, "price_asc"))).toEqual([
      "meta-llama/llama-4-scout:free",
      "openai/gpt-5-mini",
      "mystery-model",
      "anthropic/claude-sonnet-5.5",
      "perplexity/sonar-deep-research",
    ]);
    expect(ids(sortModels(CATALOG_MODELS, "price_desc"))).toEqual([
      "anthropic/claude-sonnet-5.5",
      "mystery-model",
      "openai/gpt-5-mini",
      "meta-llama/llama-4-scout:free",
      "perplexity/sonar-deep-research",
    ]);
  });

  it("sorts by context, largest first", () => {
    expect(ids(sortModels(CATALOG_MODELS, "context_desc")).slice(0, 2)).toEqual(
      ["anthropic/claude-sonnet-5.5", "openai/gpt-5-mini"],
    );
  });

  it("does not reorder its input", () => {
    const before = ids(CATALOG_MODELS);
    sortModels(CATALOG_MODELS, "price_asc");
    expect(ids(CATALOG_MODELS)).toEqual(before);
  });
});

describe("formatting", () => {
  it("formats per-1M prices", () => {
    expect(formatPerMillion(3)).toBe("$3.00");
    expect(formatPerMillion(3.15)).toBe("$3.15");
    expect(formatPerMillion(15.75)).toBe("$15.75");
    expect(formatPerMillion(0.2625)).toBe("$0.2625");
    expect(formatPerMillion(0.4)).toBe("$0.40");
    expect(formatPerMillion(0.07875)).toBe("$0.07875");
    expect(formatPerMillion(0)).toBe("Free");
    expect(formatPerMillion(0.00001)).toBe("$0.00001");
    expect(formatPerMillion(null)).toBe("—");
  });

  it("formats token counts", () => {
    expect(formatTokenCount(1_000_000)).toBe("1M");
    expect(formatTokenCount(1_048_576)).toBe("1.05M");
    expect(formatTokenCount(200_000)).toBe("200K");
    expect(formatTokenCount(131_072)).toBe("131K");
    expect(formatTokenCount(512)).toBe("512");
    expect(formatTokenCount(undefined)).toBe("—");
  });

  it("drops the vendor prefix from catalog names", () => {
    expect(modelDisplayName(CATALOG_MODELS[0])).toBe("Claude Sonnet 5.5");
    expect(modelDisplayName({ id: "x/y" })).toBe("x/y");
  });

  it("labels vendors", () => {
    expect(vendorLabel("x-ai")).toBe("xAI");
    expect(vendorLabel("some-new-lab")).toBe("Some New Lab");
    expect(vendorLabel("")).toBe("Other");
  });

  it("counts models per vendor", () => {
    const options = vendorOptions(CATALOG_MODELS);
    expect(options.map((o) => o.label)).toEqual([
      "Anthropic",
      "Meta",
      "OpenAI",
      "Other",
      "Perplexity",
    ]);
    expect(options.every((o) => o.count === 1)).toBe(true);
  });

  it("lists a model's capabilities", () => {
    expect(modelCapabilities(CATALOG_MODELS[0])).toEqual([
      "tools",
      "structured",
      "vision",
      "reasoning",
    ]);
    expect(modelCapabilities(CATALOG_MODELS[2])).toEqual([]);
  });
});

describe("snippets", () => {
  it("call the gateway's chat completions endpoint with the model", () => {
    const curl = curlSnippet("https://gw.example.com/v1/", "openai/gpt-5-mini");
    expect(curl).toContain("curl https://gw.example.com/v1/chat/completions");
    expect(curl).toContain('"model": "openai/gpt-5-mini"');
    expect(curl).toContain("Authorization: Bearer $AGENTCC_API_KEY");

    const py = pythonSnippet("https://gw.example.com/v1", "auto");
    expect(py).toContain('base_url="https://gw.example.com/v1"');
    expect(py).toContain('model="auto"');
  });

  it("use a placeholder when the gateway URL is unknown", () => {
    expect(gatewayBaseUrl("")).toBe("https://YOUR_GATEWAY_URL/v1");
  });
});

describe("vendorLabel reads like a name", () => {
  it("drops the catalog's leading ~ (an alias vendor)", () => {
    expect(vendorLabel("~perplexity")).toBe("Perplexity");
    expect(vendorLabel("~anthropic")).toBe("Anthropic");
  });

  it("keeps known acronyms upper case", () => {
    expect(vendorLabel("xingchen-agi")).toBe("Xingchen AGI");
    expect(vendorLabel("arcee-ai")).toBe("Arcee AI");
    expect(vendorLabel("some-llm")).toBe("Some LLM");
  });
});

describe("catalogFreshness: one line about where the prices come from", () => {
  const at = "2026-10-18T17:06:49-07:00";
  const later = "2026-10-20T09:00:00Z";

  it("names the bundled list when every provider uses it", () => {
    const f = catalogFreshness([
      { provider: "openrouter", source: "bundled", fetched_at: at },
    ]);
    expect(f.text).toBe("Bundled price list");
    expect(f.bundled).toBe(true);
    expect(f.tip).toMatch(/built into this gateway release/);
    expect(f.tip).not.toMatch(/unavailable/);
  });

  // One bundled source must not mark live prices as bundled.
  it("reads live prices as refreshed, naming any provider still on the bundled list", () => {
    const f = catalogFreshness([
      { provider: "openrouter", source: "live", fetched_at: later },
      { provider: "acme", source: "bundled", fetched_at: at },
    ]);
    expect(f.bundled).toBe(false);
    expect(f.text).toMatch(/^Prices refreshed /);
    expect(f.tip).toMatch(
      /acme: the price list built into this gateway release/,
    );
  });

  // A list read back from disk at boot is the last live one.
  it("counts a restored list as fetched, not bundled", () => {
    const f = catalogFreshness([
      { provider: "openrouter", source: "restored", fetched_at: later },
    ]);
    expect(f.bundled).toBe(false);
    expect(f.text).toMatch(/^Prices refreshed /);
  });

  it("says nothing without providers", () => {
    expect(catalogFreshness([])).toBeNull();
    expect(catalogFreshness(undefined)).toBeNull();
  });
});
