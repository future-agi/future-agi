import { describe, expect, it } from "vitest";

import { perRequestPrices, pricingRows } from "./modelFacts";

const pricedModel = {
  state: "active",
  prices: {
    prompt: 0.000003,
    completion: 0.000015,
    cache_read: 0.000001,
    cache_write: 0.000004,
    request: 0.04,
    web_search: 0.01,
  },
  price_tiers: [{ min_prompt_tokens: 100000, prices: { prompt: 0.000006 } }],
};

const providerRows = [
  { label: "Input", provider: 3, managed: null },
  { label: "Output", provider: 15, managed: null },
  { label: "Cached input", provider: 1, managed: null },
  { label: "Cache write", provider: 4, managed: null },
  { label: "Input above 100K prompt tokens", provider: 6, managed: null },
];
const requestRows = [
  { label: "Request fee (per call)", provider: 0.04, managed: null },
  { label: "Web search, per search", provider: 0.01, managed: null },
];

describe("availability of model detail prices", () => {
  it.each([false, true])(
    "omits every legacy price for an explicitly blocked offer, sold=%s",
    (sold) => {
      const model = { ...pricedModel, state: "blocked" };
      const options = { sold, markupPercent: 5 };
      expect(pricingRows(model, options)).toEqual([]);
      expect(perRequestPrices(model, options)).toEqual([]);
    },
  );

  it.each([
    ["active", {}],
    ["not offered on credits", { managed_allowed: false }],
    ["dynamic", { dynamic_price: true }],
    ["unpriced on credits", { unpriced: true }],
    ["stale on credits", { stale_price: true }],
  ])("preserves provider prices for a valid own-key route: %s", (_, flags) => {
    const model = { ...pricedModel, ...flags };
    const options = { sold: false, markupPercent: 5 };
    expect(pricingRows(model, options)).toEqual(providerRows);
    expect(perRequestPrices(model, options)).toEqual(requestRows);
  });
});
