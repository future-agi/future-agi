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
  default: ({ onboardingBanner }) => (
    <div data-testid="trace-detail-drawer">
      {onboardingBanner ? (
        <div data-testid="trace-onboarding-banner">
          <span>{onboardingBanner.title}</span>
          <span>{onboardingBanner.description}</span>
          <button
            type="button"
            onClick={onboardingBanner.primaryAction.onClick}
          >
            {onboardingBanner.primaryAction.label}
          </button>
        </div>
      ) : null}
    </div>
  ),
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

  it("routes sample trace users back to real setup", async () => {
    mocks.locationSearch = "?sample=true&from=onboarding";

    const { getByRole, getByText } = render(<TraceFullPage />);

    expect(getByText("Sample trace review")).toBeVisible();
    getByRole("button", { name: /connect your app/i }).click();

    expect(mocks.mutate).toHaveBeenCalledWith({
      eventName: "sample_to_real_setup_clicked",
      primaryPath: "sample",
      stage: "connect_real_data",
      source: "sample_trace_full_page",
      artifactType: "trace",
      artifactId: "trace-1",
      projectId: "observe-1",
      isSample: true,
      metadata: {
        entry: "trace_full_page",
        target_route:
          "/dashboard/observe?setup=true&source=sample_trace_review",
      },
    });
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/observe?setup=true&source=sample_trace_review",
    );
  });
});
