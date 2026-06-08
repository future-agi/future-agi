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
            data-tour-anchor={onboardingBanner.primaryAction.tourAnchor}
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
    mocks.locationSearch =
      "?sample=true&from=onboarding&quick_start_goal=explore_sample_data&quick_start_id=sample_preview&quick_start_primary_path=sample";

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
        quickStartGoal: "explore_sample_data",
        quickStartId: "sample_preview",
        quickStartPrimaryPath: "sample",
        metadata: {
          entry: "trace_full_page",
          is_sample_route: true,
        },
      }),
    );
  });

  it("routes sample trace users back to real setup", async () => {
    mocks.locationSearch =
      "?sample=true&from=onboarding&quick_start_goal=explore_sample_data&quick_start_id=sample_preview&quick_start_primary_path=sample";
    const realSetupHref =
      "/dashboard/observe?setup=true&source=sample_trace_review&quick_start_goal=explore_sample_data&quick_start_id=sample_preview&quick_start_primary_path=sample";

    const { getByRole, getByText } = render(<TraceFullPage />);

    expect(getByText("Sample trace review")).toBeVisible();
    expect(getByRole("button", { name: /connect your app/i })).toHaveAttribute(
      "data-tour-anchor",
      "sample_connect_real_data_button",
    );
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
      quickStartGoal: "explore_sample_data",
      quickStartId: "sample_preview",
      quickStartPrimaryPath: "sample",
      metadata: {
        entry: "trace_full_page",
        target_route: realSetupHref,
      },
    });
    expect(mocks.navigate).toHaveBeenCalledWith(realSetupHref);
  });

  it("routes onboarding trace reviews directly to quality check creation", async () => {
    mocks.locationSearch = "?source=onboarding&onboarding=review-first-trace";

    const { getByRole, getByText } = render(<TraceFullPage />);

    expect(getByText("First trace received")).toBeVisible();
    expect(
      getByText(
        "Review spans, latency, cost, inputs, outputs, and errors here. Next, create a quality check from this trace.",
      ),
    ).toBeVisible();

    getByRole("button", { name: /create quality check/i }).click();

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=observe-1&trace_id=trace-1",
    );
  });

  it("keeps package intent through trace review and quality check creation", async () => {
    mocks.locationSearch =
      "?source=onboarding&onboarding=review-first-trace&provider=anthropic&language=python";

    const { getByRole, getByText } = render(<TraceFullPage />);

    expect(getByText("Anthropic Python trace received")).toBeVisible();
    expect(
      getByText(
        "Review this Anthropic Python trace for spans, latency, cost, inputs, outputs, and errors. Next, create a quality check from it.",
      ),
    ).toBeVisible();

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith(
        expect.objectContaining({
          metadata: {
            entry: "trace_full_page",
            is_sample_route: false,
            setup_language: "python",
            setup_provider: "anthropic",
          },
        }),
      ),
    );

    getByRole("button", { name: /create quality check/i }).click();

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=observe-1&trace_id=trace-1&provider=anthropic&language=python",
    );
  });

  it("shows trace-review guidance from Home journey-step params", () => {
    mocks.locationSearch =
      "?tour_anchor=observe_trace_review_link&journey_step=review_first_trace";

    const { getByText } = render(<TraceFullPage />);

    expect(getByText("First trace received")).toBeVisible();
    expect(
      getByText(
        "Review spans, latency, cost, inputs, outputs, and errors here. Next, create a quality check from this trace.",
      ),
    ).toBeVisible();
  });
});
