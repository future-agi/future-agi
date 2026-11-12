import React from "react";
import { describe, expect, it, vi } from "vitest";
import { format } from "date-fns";
import { render } from "src/utils/test-utils";
import TracesTab from "../TracesTab";

// OBS-SW-154: issues span days, so the Start Time column dates each trace.
const grid = vi.hoisted(() => ({ columnDefs: [] }));

vi.mock("ag-grid-react", async (importOriginal) => {
  const { forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    AgGridReact: forwardRef(function RecordingAgGridReact(
      { columnDefs },
      _ref,
    ) {
      grid.columnDefs = columnDefs;
      return null;
    }),
  };
});
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedTraces: () => ({
    data: {
      traces: [{ id: "synthetic-start-time-trace" }],
      total: 1,
      aggregates: {
        total_traces: 1,
        avg_score: null,
        avg_turns: null,
        p50_latency: 0,
        p95_latency: 0,
      },
    },
    isLoading: false,
  }),
}));
vi.mock("src/api/project/project-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetProjectDetails: () => ({ data: { source: "prototype" } }),
}));
vi.mock("src/sections/agents/helper", async (importOriginal) => ({
  ...(await importOriginal()),
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));

describe("TracesTab Start Time column", () => {
  it("shows the date with the time, and a dash when the time is unknown", () => {
    render(
      <TracesTab error={{ cluster_id: "E-1", project_id: "project-1" }} />,
    );
    const startTime = grid.columnDefs.find((col) => col.field === "timestamp");
    const recordedAt = "2026-03-04T09:12:00Z";

    expect(startTime.valueFormatter({ value: recordedAt })).toBe(
      format(new Date(recordedAt), "MMM d, HH:mm"),
    );
    expect(startTime.valueFormatter({ value: null })).toBe("—");
  });
});
