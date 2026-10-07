import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { useErrorFeedTraces } from "src/api/errorFeed/error-feed";
import TracesTab from "../TracesTab";

// Render the grid as a plain table so the real column valueFormatters run.
vi.mock("ag-grid-react", () => {
  const MockAgGridReact = ({ rowData, columnDefs }) => (
    <table>
      <thead>
        <tr>
          {columnDefs.map((col) => (
            <th key={col.field}>{col.headerName}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rowData.map((row) => (
          <tr key={row.id}>
            {columnDefs.map((col) => (
              <td key={col.field} data-testid={`cell-${col.field}`}>
                {col.valueFormatter
                  ? col.valueFormatter({ value: row[col.field], data: row })
                  : null}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
  MockAgGridReact.propTypes = {
    rowData: PropTypes.array.isRequired,
    columnDefs: PropTypes.array.isRequired,
  };
  return { AgGridReact: MockAgGridReact };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTraces: vi.fn(),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "observe" } }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));

const mockTraces = (traces, aggregates = {}) =>
  useErrorFeedTraces.mockReturnValue({
    isLoading: false,
    data: {
      total: traces.length,
      traces,
      aggregates: {
        total_traces: traces.length,
        avg_score: 0,
        avg_turns: 0,
        p50_latency: 0,
        p95_latency: 0,
        ...aggregates,
      },
    },
  });

const error = { cluster_id: "cluster-1", project_id: "project-1" };

describe("Error feed TracesTab duration", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a voice-call length as minutes and seconds, not raw milliseconds", () => {
    mockTraces([{ id: "t1", input: "My name is Elena", latency_ms: 139833 }]);
    render(<TracesTab error={error} />);

    const cell = screen.getByTestId("cell-latency_ms");
    expect(cell).toHaveTextContent("2m 20s");
    expect(cell).not.toHaveTextContent("ms");
  });

  it("keeps sub-second and sub-minute durations in their natural unit", () => {
    mockTraces([
      { id: "fast", latency_ms: 850 },
      { id: "mid", latency_ms: 12400 },
    ]);
    render(<TracesTab error={error} />);

    const cells = screen.getAllByTestId("cell-latency_ms");
    expect(cells.map((c) => c.textContent)).toEqual(["850ms", "12.4s"]);
  });

  it("shows a dash when the trace has no duration", () => {
    mockTraces([{ id: "t1", latency_ms: null }]);
    render(<TracesTab error={error} />);

    expect(screen.getByTestId("cell-latency_ms")).toHaveTextContent("—");
  });

  it("formats the P50/P95 cards with the same unit as the Duration column", () => {
    mockTraces([{ id: "t1", latency_ms: 139833 }], {
      p50_latency: 139833,
      p95_latency: 139833,
    });
    render(<TracesTab error={error} />);

    const p50Card = screen.getByText("P50 latency").parentElement;
    const p95Card = screen.getByText("P95 latency").parentElement;
    expect(within(p50Card).getByText("2m 20s")).toBeInTheDocument();
    expect(within(p95Card).getByText("2m 20s")).toBeInTheDocument();
  });
});
