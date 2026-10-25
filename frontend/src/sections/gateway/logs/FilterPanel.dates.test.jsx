// The Request Logs date filter in the browser's time zone: the panel keeps
// what the user typed in local time, sends ISO 8601 with an offset, and says
// which zone the times are in.
// Node applies a TZ change at once; every date below is computed in a test.
const ORIGINAL_TZ = globalThis.process.env.TZ;
globalThis.process.env.TZ = "America/Los_Angeles";

import React from "react";
import { afterAll, describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import FilterPanel from "./FilterPanel";
import {
  dateFilterParam,
  isoToLocalInput,
  localInputToIso,
  timeZoneLabel,
} from "./explorerFilters";
import { buildFilterParams } from "./hooks/useRequestLogs";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
vi.mock("./hooks/useMetadataValues", () => ({
  default: () => ({ data: { application: [], service: [], tags: [] } }),
}));

// Leave the zone as this file found it for whatever runs next in the worker.
afterAll(() => {
  if (ORIGINAL_TZ === undefined) delete globalThis.process.env.TZ;
  else globalThis.process.env.TZ = ORIGINAL_TZ;
});

describe("the date helpers", () => {
  it("runs in America/Los_Angeles", () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(
      "America/Los_Angeles",
    );
  });

  it("sends what the user typed, in local time, as ISO 8601 with an offset", () => {
    expect(localInputToIso("2026-10-19T10:30")).toBe(
      "2026-10-19T17:30:00.000Z",
    );
  });

  it("shows an ISO value in the input as local time", () => {
    expect(isoToLocalInput("2026-10-19T17:30:00.000Z")).toBe(
      "2026-10-19T10:30",
    );
    expect(isoToLocalInput("2026-10-19T10:30:00-07:00")).toBe(
      "2026-10-19T10:30",
    );
  });

  it("reads a naive value from an older link as the local time it was typed in", () => {
    expect(isoToLocalInput("2026-10-19T10:30")).toBe("2026-10-19T10:30");
    expect(dateFilterParam("2026-10-19T10:30")).toBe(
      "2026-10-19T17:30:00.000Z",
    );
  });

  it("passes a value with an offset through and drops one it cannot read", () => {
    expect(dateFilterParam("2026-10-19T17:30:00.000Z")).toBe(
      "2026-10-19T17:30:00.000Z",
    );
    expect(dateFilterParam("2026-10-19T10:30:00+05:30")).toBe(
      "2026-10-19T10:30:00+05:30",
    );
    expect(localInputToIso("not a date")).toBe("");
  });

  it("names the zone with its offset", () => {
    expect(timeZoneLabel(new Date("2026-10-19T17:30:00Z"))).toBe(
      "America/Los_Angeles (UTC-07:00)",
    );
  });

  it("the table's and sessions' query sends the offset form", () => {
    expect(
      buildFilterParams({
        startedAfter: "2026-10-19T10:30",
        startedBefore: "2026-10-19T18:30:00.000Z",
        model: "gpt-4o",
      }),
    ).toEqual({
      started_after: "2026-10-19T17:30:00.000Z",
      started_before: "2026-10-19T18:30:00.000Z",
      model: "gpt-4o",
    });
  });
});

describe("the filter panel dates", () => {
  it("applies 10:30-11:30 local as 17:30Z-18:30Z", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <FilterPanel open onClose={vi.fn()} filters={{}} onApply={onApply} />,
    );
    await user.type(screen.getByLabelText("Start Date"), "2026-10-19T10:30");
    await user.type(screen.getByLabelText("End Date"), "2026-10-19T11:30");
    await user.click(screen.getByRole("button", { name: /Apply/ }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({
        startedAfter: "2026-10-19T17:30:00.000Z",
        startedBefore: "2026-10-19T18:30:00.000Z",
      }),
    );
  });

  it("opens with an applied filter shown in local time", () => {
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{ startedAfter: "2026-10-19T17:30:00.000Z" }}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-10-19T10:30");
  });

  it("says which time zone the dates are in", () => {
    render(
      <FilterPanel open onClose={vi.fn()} filters={{}} onApply={vi.fn()} />,
    );
    expect(
      screen.getByText(/Times are in America\/Los_Angeles \(UTC-0[78]:00\)/),
    ).toBeInTheDocument();
  });
});
