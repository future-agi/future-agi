import { describe, expect, it } from "vitest";

import {
  getQueryReadState,
  hasValidStatusPair,
} from "src/utils/queryReadState";

// The observed catalog answers `query_complete: false` / `query_status:
// "partial"` while an index is still being backfilled or is seconds behind
// ingestion. That pair must be a declared, well-formed state -- not something
// that renders as degraded only because the validator failed to recognise it.
const partialPage = {
  query_complete: false,
  query_status: "partial",
  query_exact: false,
  query_provenance: "current_property_catalog",
  coverage_reason: "source_predates_index",
  coverage_floor: "2026-09-01 00:00:00.000000",
};

describe("partial coverage is a declared read state", () => {
  it("is a valid status pair only when incomplete", () => {
    expect(hasValidStatusPair(partialPage)).toBe(true);
    expect(hasValidStatusPair({ ...partialPage, query_complete: true })).toBe(
      false,
    );
  });

  it("renders as degraded, so the picker shows its caveat", () => {
    expect(getQueryReadState(partialPage)).toBe("degraded");
  });

  it("does not disturb the complete state", () => {
    expect(
      getQueryReadState({
        ...partialPage,
        query_complete: true,
        query_status: "complete",
        coverage_reason: "covered",
      }),
    ).toBe("complete");
  });
});
