/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { PROJECT_SOURCE } from "src/utils/constants";
import { SharedIssueContext } from "../../sharedIssueContext";
import TracesTab from "../TracesTab";

// Keep this spec JSX-free so node --check can validate its syntax without
// running the suite. The real component and pagination footer are rendered.
const h = React.createElement;
const mocks = vi.hoisted(() => ({
  useErrorFeedTraces: vi.fn(),
  useGetProjectDetails: vi.fn(),
  useVoiceCallDetail: vi.fn(),
}));

vi.mock("ag-grid-react", async () => {
  const { createElement, forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function MockAgGridReact(
      { rowData, onRowClicked },
      _ref,
    ) {
      return createElement(
        "div",
        null,
        rowData.map((row) =>
          createElement(
            "button",
            {
              key: row.id,
              type: "button",
              onClick: () => onRowClicked({ data: row }),
            },
            `Row ${row.id}`,
          ),
        ),
      );
    }),
  };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTraces: mocks.useErrorFeedTraces,
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: mocks.useGetProjectDetails,
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: mocks.useVoiceCallDetail,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: ({ open, traceId }) =>
    open ? h("div", { "data-testid": "trace-drawer" }, traceId) : null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: ({ data }) =>
    h("div", { "data-testid": "voice-drawer" }, data.trace_id),
}));

const error = { cluster_id: "cluster-a", project_id: "project-a" };
const otherError = { cluster_id: "cluster-b", project_id: "project-b" };
let totals;
let fetching;

const pageData = (clusterId, { limit = 200, offset = 0 } = {}) => {
  const total = totals[clusterId] ?? 0;
  return {
    traces: Array.from(
      { length: Math.min(limit, Math.max(0, total - offset)) },
      (_, index) => ({ id: `${clusterId}-${offset + index + 1}` }),
    ),
    total,
    aggregates: {
      total_traces: total,
      avg_score: null,
      avg_turns: null,
      p50_latency: 0,
      p95_latency: 0,
    },
  };
};
const nextPage = () => screen.getByRole("button", { name: "Next page" });
const previousPage = () =>
  screen.getByRole("button", { name: "Previous page" });

// Expected RED on the base: it has no pagination controls/offset and retains
// drawer selection across cluster changes. Shared snapshots are a green control.
describe("TracesTab server pagination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    totals = { "cluster-a": 201, "cluster-b": 3 };
    fetching = false;
    mocks.useErrorFeedTraces.mockImplementation((clusterId, params) => ({
      data: clusterId ? pageData(clusterId, params) : undefined,
      isLoading: false,
      isFetching: fetching,
    }));
    mocks.useGetProjectDetails.mockReturnValue({
      data: { source: "prototype" },
    });
    mocks.useVoiceCallDetail.mockReturnValue({
      data: undefined,
      isFetching: false,
    });
  });

  it("OBS-SW-25 requests offset 200 and opens the 201st trace", () => {
    render(h(TracesTab, { error }));
    expect(mocks.useErrorFeedTraces).toHaveBeenLastCalledWith("cluster-a", {
      limit: 200,
      offset: 0,
    });
    expect(screen.getAllByRole("button", { name: /^Row / })).toHaveLength(200);
    expect(previousPage()).toBeDisabled();
    expect(nextPage()).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Row cluster-a-1" }));
    expect(screen.getByTestId("trace-drawer")).toHaveTextContent("cluster-a-1");
    fireEvent.click(nextPage());

    expect(mocks.useErrorFeedTraces).toHaveBeenLastCalledWith("cluster-a", {
      limit: 200,
      offset: 200,
    });
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Row / })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Row cluster-a-201" }));
    expect(screen.getByTestId("trace-drawer")).toHaveTextContent(
      "cluster-a-201",
    );
    expect(nextPage()).toBeDisabled();
    expect(previousPage()).toBeEnabled();

    fireEvent.click(previousPage());
    expect(mocks.useErrorFeedTraces).toHaveBeenLastCalledWith("cluster-a", {
      limit: 200,
      offset: 0,
    });
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Row cluster-a-1" }),
    ).toBeInTheDocument();
    expect(previousPage()).toBeDisabled();
  });

  it("OBS-SW-25 reaches every server page using the returned total", () => {
    totals["cluster-a"] = 401;
    render(h(TracesTab, { error }));
    fireEvent.click(nextPage());
    expect(
      screen.getByRole("button", { name: "Row cluster-a-201" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Row / })).toHaveLength(200);
    expect(nextPage()).toBeEnabled();
    fireEvent.click(nextPage());

    expect(mocks.useErrorFeedTraces).toHaveBeenLastCalledWith("cluster-a", {
      limit: 200,
      offset: 400,
    });
    expect(screen.getAllByRole("button", { name: /^Row / })).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Row cluster-a-401" }),
    ).toBeInTheDocument();
    expect(nextPage()).toBeDisabled();
  });

  it("OBS-SW-25 resets the page and drawer before querying another cluster", () => {
    const { rerender } = render(h(TracesTab, { error }));
    fireEvent.click(nextPage());
    fireEvent.click(screen.getByRole("button", { name: "Row cluster-a-201" }));
    expect(screen.getByTestId("trace-drawer")).toBeInTheDocument();
    mocks.useErrorFeedTraces.mockClear();

    rerender(h(TracesTab, { error: otherError }));
    expect(mocks.useErrorFeedTraces).toHaveBeenCalled();
    for (const [clusterId, params] of mocks.useErrorFeedTraces.mock.calls) {
      expect(clusterId).toBe("cluster-b");
      expect(params).toEqual({ limit: 200, offset: 0 });
    }
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Row cluster-b-1" }),
    ).toBeInTheDocument();
    expect(previousPage()).toBeDisabled();

    // Returning to a previously viewed cluster must not revive its selection.
    rerender(h(TracesTab, { error }));
    expect(mocks.useErrorFeedTraces).toHaveBeenLastCalledWith("cluster-a", {
      limit: 200,
      offset: 0,
    });
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
  });

  it.each([
    ["text", "prototype", "trace-drawer"],
    ["voice", PROJECT_SOURCE.SIMULATOR, "voice-drawer"],
  ])(
    "OBS-SW-25 clears an open %s drawer when the cluster changes",
    (_, source, drawer) => {
      mocks.useGetProjectDetails.mockReturnValue({ data: { source } });
      const { rerender } = render(h(TracesTab, { error }));
      fireEvent.click(screen.getByRole("button", { name: "Row cluster-a-1" }));
      expect(screen.getByTestId(drawer)).toHaveTextContent("cluster-a-1");

      rerender(h(TracesTab, { error: otherError }));
      expect(screen.queryByTestId(drawer)).not.toBeInTheDocument();
      expect(mocks.useVoiceCallDetail.mock.lastCall[0]).toBeNull();
    },
  );

  it("OBS-SW-25 freezes page controls while a page is fetching", () => {
    const { rerender } = render(h(TracesTab, { error }));
    fireEvent.click(nextPage());
    expect(previousPage()).toBeEnabled();
    fetching = true;
    rerender(h(TracesTab, { error }));
    expect(previousPage()).toBeDisabled();
    expect(nextPage()).toBeDisabled();
    fireEvent.click(previousPage());
    expect(mocks.useErrorFeedTraces.mock.lastCall[1]).toEqual({
      limit: 200,
      offset: 200,
    });

    fetching = false;
    rerender(h(TracesTab, { error }));
    expect(previousPage()).toBeEnabled();
  });

  it.each([0, 200])("OBS-SW-25 bounds navigation for %i traces", (total) => {
    totals["cluster-a"] = total;
    render(h(TracesTab, { error }));
    expect(previousPage()).toBeDisabled();
    expect(nextPage()).toBeDisabled();
    expect(screen.queryAllByRole("button", { name: /^Row / })).toHaveLength(
      total,
    );
  });

  it("keeps shared snapshots read-only without authenticated pagination", () => {
    // A snapshot can advertise more traces than it embeds, but has no live
    // signed-in pagination endpoint. Do not pretend its next page is fetchable.
    const shared = {
      token: "shared-token",
      traces: {
        ...pageData("cluster-a"),
        traces: [{ id: "shared-trace" }],
      },
    };
    render(
      h(
        SharedIssueContext.Provider,
        { value: shared },
        h(TracesTab, { error }),
      ),
    );
    expect(
      screen.getByRole("button", { name: "Row shared-trace" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Next page" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Previous page" }),
    ).not.toBeInTheDocument();
    for (const [clusterId] of mocks.useErrorFeedTraces.mock.calls) {
      expect(clusterId).toBeNull();
    }
    for (const [, enabled] of mocks.useGetProjectDetails.mock.calls) {
      expect(enabled).toBe(false);
    }
  });
});
