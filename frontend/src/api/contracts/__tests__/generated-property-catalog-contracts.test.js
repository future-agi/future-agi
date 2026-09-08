import { describe, expect, it } from "vitest";

import {
  TracerDashboardFilterValuesResponse,
  TracerDashboardMetricsResponse,
} from "src/generated/api-contracts/api.zod";

const ACTIVATION = "a".repeat(64);

describe("generated unified property catalog contracts", () => {
  it("accepts an exact terminal definition page with nullable cursor metadata", () => {
    const parsed = TracerDashboardMetricsResponse.safeParse({
      status: true,
      result: {
        metrics: [
          {
            name: "model",
            property_id: "system_attribute:traces:model",
            property_kind: "system_attribute",
            attribute_types: ["string"],
            attribute_types_exact: true,
          },
        ],
        total: null,
        total_is_exact: false,
        page_size: 50,
        has_more: false,
        next_cursor: null,
        catalog_epoch: 1,
        catalog_revision: 7,
        activation_fingerprint: ACTIVATION,
        query_complete: true,
        query_exact: true,
        query_status: "complete",
        query_provenance: "activated_property_catalog",
      },
    });
    expect(
      parsed.success,
      parsed.success ? "" : JSON.stringify(parsed.error.issues),
    ).toBe(true);
  });

  it("accepts a terminal native-value page with nullable cursor metadata", () => {
    const parsed = TracerDashboardFilterValuesResponse.safeParse({
      status: true,
      result: {
        values: [{ value: "gpt-5", label: "gpt-5", type: "string" }],
        query_complete: true,
        query_status: "complete",
        query_window_start: "2026-08-14T00:00:00Z",
        query_window_end: "2026-08-15T00:00:00Z",
        query_count: 4,
        has_more: false,
        browse_status: "exhausted",
        next_cursor: null,
        attribute_types: ["string"],
        attribute_types_exact: true,
        catalog_epoch: 1,
        catalog_revision: 7,
        activation_fingerprint: ACTIVATION,
        query_provenance: "activated_property_catalog",
      },
    });
    expect(
      parsed.success,
      parsed.success ? "" : JSON.stringify(parsed.error.issues),
    ).toBe(true);
  });
});
