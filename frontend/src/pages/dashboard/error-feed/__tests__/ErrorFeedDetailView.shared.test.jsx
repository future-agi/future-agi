import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ErrorFeedDetailView from "../ErrorFeedDetailView";
import { SharedIssueContext } from "../sharedIssueContext";
import { useErrorFeedStore } from "../store";

const mocks = vi.hoisted(() => ({
  useErrorFeedRedirect: vi.fn(() => ({ data: undefined, isLoading: false })),
  useErrorFeedDetail: vi.fn(() => ({ data: undefined, isLoading: false })),
  useAnalyzeRunner: vi.fn(),
}));

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedRedirect: mocks.useErrorFeedRedirect,
  useErrorFeedDetail: mocks.useErrorFeedDetail,
  useUpdateErrorFeedIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useExportErrorFeedTraces: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../useAnalyzeRunner", () => ({
  useAnalyzeRunner: mocks.useAnalyzeRunner,
}));
vi.mock("../components/ErrorMetadataPanel", () => ({
  default: () => null,
  LinearTeamPicker: () => null,
}));
vi.mock("../components/OverviewTab", () => ({ default: () => null }));
vi.mock("../components/TracesTab", () => ({ default: () => null }));
vi.mock("../components/TrendsTab", () => ({ default: () => null }));
vi.mock("../components/ClusterHeadlineCard", () => ({ default: () => null }));
vi.mock("../components/AnalyzeTab", () => ({
  default: () => <div>analysis tab</div>,
}));

const row = {
  cluster_id: "E-LOCAL01",
  error: { name: "Agent promises refunds outside the policy", type: "Policy" },
  status: "escalating",
  severity: "high",
  trace_count: 8,
};

const sharedWith = (rca) => ({
  token: "tok",
  issue: { row, rca },
  overview: null,
  traces: null,
  trends: null,
  sidebar: null,
});

const renderShared = (rca = null) =>
  render(
    <SharedIssueContext.Provider value={sharedWith(rca)}>
      <ErrorFeedDetailView />
    </SharedIssueContext.Provider>,
  );

describe("ErrorFeedDetailView on a shared issue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useErrorFeedStore.setState({ activeTab: "overview" });
  });

  it("renders the issue from the share with no actions", () => {
    renderShared();

    expect(
      screen.getByText("Agent promises refunds outside the policy"),
    ).toBeInTheDocument();
    for (const name of [/resolve/i, /acknowledge/i, /ignore/i, /^share$/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(
      screen.queryByRole("button", { name: /error feed/i }),
    ).not.toBeInTheDocument();
  });

  it("never fetches the issue or starts the analysis runner", () => {
    renderShared();

    for (const [id] of mocks.useErrorFeedRedirect.mock.calls) {
      expect(id).toBeFalsy();
    }
    for (const [, options] of mocks.useErrorFeedDetail.mock.calls) {
      expect(options?.enabled).toBe(false);
    }
    // Called only to seed the saved analysis; it must never run.
    for (const [, , options] of mocks.useAnalyzeRunner.mock.calls) {
      expect(options).toEqual({ enabled: false });
    }
  });

  it("opens the Fix tab as the read-only analysis tab", async () => {
    renderShared();

    await userEvent.click(screen.getByRole("tab", { name: /fix/i }));

    expect(screen.getByText("analysis tab")).toBeInTheDocument();
  });
});
