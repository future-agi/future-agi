import { describe, expect, it } from "vitest";
import { buildCatalogRows } from "../catalogRows";
import { comparisonFacts } from "./comparisonFacts";
import { compareSearch, parseCompareQuery, toggleCompareId } from "./compareQuery";

const model = {
  id: "openai/example",
  vendor: "openai",
  state: "active",
  prices: { prompt: 0.000002, completion: 0.000004 },
  billed_input_per_mtok: 2.1,
  billed_output_per_mtok: 4.2,
  context_length: 128000,
  max_output_tokens: 8192,
  supported_parameters: ["tools"],
  input_modalities: ["text", "image"],
};
const rowFor = (patch = {}, options = {}) => buildCatalogRows([{ ...model, ...patch }], {
  managedStatus: { available: true, enabled: true },
  providers: [{ id: "openai", models: ["example"] }],
  ...options,
})[0];

describe("model comparison query", () => {
  it("bounds selection at three and still allows deselection", () => {
    const ids = ["openai/a", "google/b", "cohere/c"];
    expect(toggleCompareId(ids, "openai/d")).toEqual(ids);
    expect(toggleCompareId(ids, "google/b")).toEqual(["openai/a", "cohere/c"]);
    expect(toggleCompareId([], "openai/a")).toEqual(["openai/a"]);
  });

  it("round-trips exact IDs and keeps other browse parameters", () => {
    const ids = ["openai/a:free", "google/a+b", "cohere/folder/model"];
    const search = compareSearch("?q=a+b&provider=openai&provider=google&sort=newest", ids);
    expect(parseCompareQuery(search)).toEqual(ids);
    const query = new URLSearchParams(search);
    expect(query.get("q")).toBe("a b");
    expect(query.getAll("provider")).toEqual(["openai", "google"]);
    expect(query.get("sort")).toBe("newest");
    const closed = new URLSearchParams(compareSearch(search, []));
    expect(closed.has("compare")).toBe(false);
    expect(closed.get("sort")).toBe("newest");
  });

  it("deduplicates, ignores empty IDs, caps pasted links, and retains missing catalog IDs", () => {
    expect(parseCompareQuery("?compare=,openai/a,openai/a,missing/model,google/b,cohere/c"))
      .toEqual(["openai/a", "missing/model", "google/b"]);
    expect(parseCompareQuery("?compare=")).toEqual([]);
  });
});

describe("comparison uses catalog facts, not guesses", () => {
  it("shows one price, the billed credits rate, and does not apply markup twice", () => {
    const facts = comparisonFacts(rowFor(), 5);
    expect(facts.input.value).toBe("$2.10");
    expect(facts.output.value).toBe("$4.20");
    expect(facts.input.reason).toMatch(/Future AGI credits: what a request is charged/);
    // Never the provider's quote or the margin beside it.
    expect(Object.keys(facts)).not.toContain("providerInput");
    for (const fact of [facts.input, facts.output]) {
      expect(`${fact.value} ${fact.reason}`).not.toMatch(/markup|%|\$2\.00|\$4\.00/i);
    }
    expect(facts.context.value).toBe("128K tokens");
    expect(facts.capabilities.value).toBe("Tools, Image input");
  });

  it("shows own-key prices without making an unavailable managed quote", () => {
    const facts = comparisonFacts(rowFor({ managed_allowed: false }), 5);
    expect(facts.input.value).toBe("$2.00");
    expect(facts.output.value).toBe("$4.00");
    expect(facts.input.reason).toMatch(/does not offer/);
    expect(facts.input.reason).toMatch(/your provider bills you directly/);
    expect(facts.access.value).toBe("Own key");
  });

  it("shows unknown facts with reasons and never turns catalog active into observed health", () => {
    const facts = comparisonFacts(rowFor({ context_length: null, max_output_tokens: null,
      supported_parameters: undefined, input_modalities: undefined }), 5);
    for (const key of ["context", "maxOutput", "capabilities", "health"]) {
      expect(facts[key].value).toBe("—");
      expect(facts[key].reason).toBeTruthy();
    }
    expect(facts.catalogState.value).toBe("Active");
    expect(facts.health.reason).toMatch(/not included in the catalog/i);
    for (const fact of Object.values(comparisonFacts(undefined, 5))) {
      expect(fact.value).toBe("—");
      expect(fact.reason).toMatch(/not in the loaded catalog/i);
    }
  });

  it("discloses credits off and does not verify retained own-key access", () => {
    const off = rowFor({}, { managedStatus: { available: true, enabled: false } });
    expect(comparisonFacts(off, 5).access.value).toMatch(/credits · off/);
    const unverified = rowFor({ managed_allowed: false }, { providerAccessState: "error" });
    const facts = comparisonFacts(unverified, 5);
    expect(facts.access.value).toBe("—");
    expect(facts.access.reason).toMatch(/not verified/i);
  });

  it.each([
    { state: "blocked" }, { dynamic_price: true }, { unpriced: true }, { stale_price: true },
  ])("does not publish an unreliable price as a fixed quote: %j", (patch) => {
    const facts = comparisonFacts(rowFor(patch), 5);
    for (const key of ["input", "output"]) {
      expect(facts[key].value).toBe("—");
      expect(facts[key].reason).toBeTruthy();
    }
  });

  it("retains real zero quotes without calling the whole model free", () => {
    const facts = comparisonFacts(rowFor({ prices: { prompt: 0, completion: 0 },
      billed_input_per_mtok: 0, billed_output_per_mtok: 0 }), 5);
    expect(facts.input.value).toBe("$0.00");
    expect(facts.output.value).toBe("$0.00");
  });

  it("keeps request-only prices out of token cells and names the billing unit", () => {
    const facts = comparisonFacts(rowFor({ prices: { request: 0.04, prompt: 0, completion: 0 },
      billed_input_per_mtok: 0, billed_output_per_mtok: 0 }), 5);
    expect(facts.input.value).toBe("—");
    expect(facts.output.value).toBe("—");
    // The fee on credits ($0.04 from the provider + 5%), not both.
    expect(facts.otherPrices.value).toBe("Request fee (per call): $0.042");
    expect(facts.otherPrices.value).toMatch(/per call/);
  });

  it("preserves inclusive dimensions and never uses legacy zero token rates", () => {
    const facts = comparisonFacts(rowFor({ billed_input_per_mtok: 0, billed_output_per_mtok: 0,
      inclusive_price: { model: model.id, contract: "openrouter-inclusive-r601-image-1",
        prompt: "0.000002", image: "0.04" } }), 5);
    expect(facts.input.value).toBe("—");
    expect(facts.output.value).toBe("—");
    expect(facts.otherPrices.value).toContain("Image: $0.042 / image");
    expect(facts.otherPrices.value).not.toContain("$0.04 ");
    expect(facts.otherPrices.value).not.toMatch(/provider/i);
    expect(facts.otherPrices.reason).toMatch(/not a sum/i);
    expect(facts.otherPrices.reason).not.toMatch(/markup/i);
  });

  it("keeps dynamic inclusive router quotes unknown", () => {
    const facts = comparisonFacts(rowFor({ dynamic_price: true,
      billed_input_per_mtok: 0, billed_output_per_mtok: 0,
      inclusive_price: { model: model.id, contract: "openrouter-inclusive-r601-router-1",
        router_snapshot: "a".repeat(64) } }), 5);
    expect(facts.input.value).toBe("—");
    expect(facts.otherPrices.value).toBe("—");
    expect(facts.otherPrices.reason).toMatch(/no fixed quote/i);
    expect(facts.otherPrices.reason).not.toMatch(/markup/i);
  });

  // The provider's lower quote is never shown as the price on credits.
  it("does not substitute zero markup when both markup and a billed rate are missing", () => {
    const row = rowFor();
    delete row.model.billed_input_per_mtok;
    const facts = comparisonFacts(row, undefined);
    expect(facts.input.value).toBe("—");
    expect(facts.input.reason).toMatch(/not reported/i);
    expect(facts.output.value).toBe("$4.20");
  });
});
