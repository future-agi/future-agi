import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { resetReplaySessionsStore } from "../../SessionsView/ReplaySessions/store";
import ReplayTraces from "../ReplayTraces";
import { resetTraceGridStore, useTraceGridStore } from "../states";

describe("ReplayTraces", () => {
  const gridApi = {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };

  beforeEach(() => {
    resetTraceGridStore();
    resetReplaySessionsStore();
    vi.clearAllMocks();
  });

  it("labels a select-all lower bound without claiming an exact replay count", () => {
    useTraceGridStore.setState({
      selectAll: true,
      toggledNodes: ["trace-a", "trace-b"],
      totalRowCount: null,
      totalRowCountLowerBound: 26,
      totalRowCountIsLowerBound: true,
    });

    render(<ReplayTraces gridApi={gridApi} />);

    expect(
      screen.getByRole("button", { name: "Replay Traces (≥24)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Replay Traces (24)" }),
    ).not.toBeInTheDocument();
  });
});
