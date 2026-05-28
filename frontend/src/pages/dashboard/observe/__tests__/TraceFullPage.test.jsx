import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import TraceFullPage from "../TraceFullPage";

const mocks = vi.hoisted(() => ({
  locationSearch: "",
  mutate: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("react-router", () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => ({
    observeId: "observe-1",
    traceId: "trace-1",
  }),
  useLocation: () => ({
    search: mocks.locationSearch,
  }),
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }) => children,
}));

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => <div data-testid="trace-detail-drawer" />,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.mutate,
  }),
}));

describe("TraceFullPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.locationSearch = "";
  });

  it("records a trace review activation event when opened", async () => {
    render(<TraceFullPage />);

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith({
        eventName: "trace_detail_opened",
        primaryPath: "observe",
        stage: "review_first_trace",
        source: "trace_full_page",
        artifactType: "trace",
        artifactId: "trace-1",
        projectId: "observe-1",
        isSample: false,
        metadata: {
          entry: "trace_full_page",
          is_sample_route: false,
        },
      }),
    );
  });

  it("records a sample trace review activation event for sample routes", async () => {
    mocks.locationSearch = "?sample=true&from=onboarding";

    render(<TraceFullPage />);

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith({
        eventName: "sample_trace_detail_opened",
        primaryPath: "sample",
        stage: "review_first_trace",
        source: "sample_trace_full_page",
        artifactType: "trace",
        artifactId: "trace-1",
        projectId: "observe-1",
        isSample: true,
        metadata: {
          entry: "trace_full_page",
          is_sample_route: true,
        },
      }),
    );
  });
});
