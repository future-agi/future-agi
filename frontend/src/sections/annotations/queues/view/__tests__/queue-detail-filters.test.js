import { describe, expect, it } from "vitest";
import {
  ALL_SOURCE_VALUES,
  ALL_STATUS_VALUES,
  ITEM_STATUS_FILTER_OPTIONS,
  NO_FILTER_MATCH_VALUE,
  buildQueueItemQueryFilters,
} from "../queue-item-filters";

describe("QueueDetailView item filters", () => {
  it("keeps item statuses workflow-based and hides internal in-progress state", () => {
    const statusValues = ITEM_STATUS_FILTER_OPTIONS.map(
      (option) => option.value,
    );

    expect(statusValues).toContain("in_review");
    expect(statusValues).not.toContain("in_progress");
  });

  it("treats all selected statuses and sources as no API filter", () => {
    const filters = buildQueueItemQueryFilters({
      status: ALL_STATUS_VALUES,
      source_type: ALL_SOURCE_VALUES,
      assigned_to: "",
      review_status: "",
    });

    expect(filters.status).toBeUndefined();
    expect(filters.source_type).toBeUndefined();
  });

  it("passes partial multi-select status and source filters through as arrays", () => {
    const filters = buildQueueItemQueryFilters({
      status: ["pending", "in_review"],
      source_type: ["trace", "trace_session"],
      assigned_to: "me",
      review_status: "pending_review",
    });

    expect(filters).toEqual({
      status: ["pending", "in_review"],
      source_type: ["trace", "trace_session"],
      assigned_to: "me",
      review_status: "pending_review",
    });
  });

  it("uses a no-match sentinel when every option is deselected", () => {
    const filters = buildQueueItemQueryFilters({
      status: [],
      source_type: [],
      assigned_to: "",
      review_status: "",
    });

    expect(filters.status).toBe(NO_FILTER_MATCH_VALUE);
    expect(filters.source_type).toBe(NO_FILTER_MATCH_VALUE);
  });
});
