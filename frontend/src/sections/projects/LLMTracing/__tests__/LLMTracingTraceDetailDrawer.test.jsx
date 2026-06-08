import { describe, expect, it, vi } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import LLMTracingTraceDetailDrawer from "../LLMTracingTraceDetailDrawer";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  setTraceDetailDrawerOpen: vi.fn(),
}));

vi.mock("react-router", () => ({
  useParams: () => ({
    observeId: "observe-1",
  }),
}));

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => <div data-testid="trace-detail-drawer" />,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.mutate,
  }),
}));

vi.mock("../states", () => ({
  useLLMTracingStoreShallow: (selector) =>
    selector({
      traceDetailDrawerOpen: {
        traceId: "trace-1",
        filters: [],
      },
      setTraceDetailDrawerOpen: mocks.setTraceDetailDrawerOpen,
      visibleTraceIds: ["trace-1"],
    }),
}));

describe("LLMTracingTraceDetailDrawer", () => {
  it("records a trace review activation event when the drawer opens", async () => {
    render(<LLMTracingTraceDetailDrawer />);

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith({
        eventName: "trace_detail_opened",
        primaryPath: "observe",
        stage: "review_first_trace",
        source: "trace_drawer",
        artifactType: "trace",
        artifactId: "trace-1",
        projectId: "observe-1",
        metadata: {
          entry: "trace_drawer",
        },
      }),
    );
  });
});
