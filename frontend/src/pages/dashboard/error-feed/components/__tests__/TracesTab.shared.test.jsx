/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import TracesTab from "../TracesTab";
import { SharedIssueContext } from "../../sharedIssueContext";

const mocks = vi.hoisted(() => ({
  useErrorFeedTraces: vi.fn(() => ({ data: undefined, isLoading: false })),
  useGetProjectDetails: vi.fn(() => ({ data: undefined })),
  drawerProps: vi.fn(),
}));

vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  const AgGridReact = forwardRef(function MockAgGridReact(
    { rowData, onRowClicked },
    _ref,
  ) {
    return (
      <div>
        {rowData.map((row) => (
          <button
            key={row.id}
            type="button"
            onClick={() => onRowClicked({ data: row })}
          >
            {`Row ${row.id}`}
          </button>
        ))}
      </div>
    );
  });
  return { AgGridReact };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTraces: mocks.useErrorFeedTraces,
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: mocks.useGetProjectDetails,
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    mocks.drawerProps(props);
    return props.open ? <div data-testid="trace-drawer" /> : null;
  },
}));

const shared = {
  token: "tok",
  traces: {
    traces: [{ id: "trace-a" }, { id: "trace-b" }],
    total: 2,
    aggregates: {
      total_traces: 2,
      avg_score: 0,
      avg_turns: 0,
      p50_latency: 0,
      p95_latency: 0,
    },
  },
};

describe("TracesTab on a shared issue", () => {
  it("lists the shared traces and opens one read-only through the token", async () => {
    render(
      <SharedIssueContext.Provider value={shared}>
        <TracesTab error={{ cluster_id: "E-1", project_id: "p-1" }} />
      </SharedIssueContext.Provider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Row trace-b" }));

    expect(screen.getByTestId("trace-drawer")).toBeInTheDocument();
    const props = mocks.drawerProps.mock.lastCall[0];
    expect(props).toMatchObject({
      traceId: "trace-b",
      readOnly: true,
      sharedToken: "tok",
      hasPrev: true,
      hasNext: false,
    });
    for (const [clusterId] of mocks.useErrorFeedTraces.mock.calls) {
      expect(clusterId).toBeFalsy();
    }
    for (const [projectId, enabled] of mocks.useGetProjectDetails.mock.calls) {
      expect(projectId && enabled).toBeFalsy();
    }
  });
});
