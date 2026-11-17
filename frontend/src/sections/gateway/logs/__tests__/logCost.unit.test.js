import { describe, expect, it } from "vitest";
import { costSourceOf, noChargeComputed } from "../logCost";

// logCost.js's rule: no charge was computed for a request with a cost of 0
// and no cost source; one priced at zero carries a cost source. An amount
// other than zero, and a request with no price found, are never "no charge".

const BASE = {
  model: "gpt-4o-mini",
  provider: "openai",
  latency_ms: 12,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  session_id: "",
  started_at: "2026-11-11T10:02:00Z",
  metadata: null,
};

// Refused before it was priced: a cost of 0, no cost source; the route had
// already set its billing mode.
const REFUSED = {
  ...BASE,
  id: "refused",
  request_id: "req-refused",
  status_code: 403,
  is_error: true,
  error_message: "Forbidden",
  cost: "0.000000",
  cost_source: null,
  billing_mode: "managed",
};

// Priced by the gateway at zero: a cache hit.
const CACHE_HIT = {
  ...BASE,
  id: "cache-hit",
  request_id: "req-cache-hit",
  status_code: 200,
  cache_hit: true,
  cost: "0.000000",
  cost_source: "cache_hit",
  billing_mode: "byok",
};

// An amount with no cost source (an older log Django priced) keeps it.
const LEGACY_PRICED = {
  ...BASE,
  id: "legacy",
  request_id: "req-legacy",
  status_code: 200,
  cost: "0.001000",
  cost_source: null,
  billing_mode: null,
};

describe("noChargeComputed", () => {
  it("tells a request no charge was computed for from one priced at zero", () => {
    expect(noChargeComputed(REFUSED)).toBe(true);
    expect(noChargeComputed({ ...REFUSED, billing_mode: null })).toBe(true);
    expect(noChargeComputed({ ...REFUSED, cost: undefined })).toBe(true);
    expect(noChargeComputed(CACHE_HIT)).toBe(false);
    expect(
      noChargeComputed({ ...REFUSED, cost_source: "catalog", cost: "0" }),
    ).toBe(false);
    // The detail's billing block and a v1 log's metadata carry it too.
    expect(
      noChargeComputed({
        ...REFUSED,
        billing: { mode: "byok", cost_source: "cache_hit" },
      }),
    ).toBe(false);
    expect(
      noChargeComputed({ ...REFUSED, metadata: { cost_source: "catalog" } }),
    ).toBe(false);
    // Never hides an amount, and leaves No price found to its own rule.
    expect(noChargeComputed(LEGACY_PRICED)).toBe(false);
    expect(noChargeComputed({ ...REFUSED, cost_source: "unpriced" })).toBe(
      false,
    );
    expect(noChargeComputed(undefined)).toBe(false);
    expect(costSourceOf({ cost_source: "  " })).toBeNull();
  });
});
