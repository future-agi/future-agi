import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { TraceGraphView } from "../OverviewTab";

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: vi.fn(),
}));

describe("Feed trace graph", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a load error instead of claiming the trace has no spans", () => {
    useGetTraceDetail.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<TraceGraphView traceId="trace-1" mode="graph" />);
    expect(screen.getByText("Could not load trace spans. Please retry.")).toBeInTheDocument();
    expect(screen.queryByText("No span data available for this trace")).not.toBeInTheDocument();
  });

  it("reserves the empty state for a successful response with no spans", () => {
    useGetTraceDetail.mockReturnValue({
      data: { observation_spans: [] }, isLoading: false, isError: false,
    });
    render(<TraceGraphView traceId="trace-1" mode="graph" />);
    expect(screen.getByText("No span data available for this trace")).toBeInTheDocument();
  });
});
