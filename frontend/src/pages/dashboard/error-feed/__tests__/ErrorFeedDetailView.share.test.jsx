import { describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { render, screen, userEvent } from "src/utils/test-utils";
import ErrorFeedDetailView from "../ErrorFeedDetailView";

const CLUSTER_ID = "E-9C01E76F";

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ id: CLUSTER_ID }),
}));

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedRedirect: () => ({
    data: { resolved_cluster_id: CLUSTER_ID },
    isLoading: false,
    isError: false,
  }),
  useErrorFeedDetail: () => ({
    data: {
      row: {
        cluster_id: CLUSTER_ID,
        error: { name: "Agent picks the wrong region", type: "llm" },
        status: "escalating",
        severity: "high",
        trace_count: 5,
      },
    },
    isLoading: false,
  }),
  useUpdateErrorFeedIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useExportErrorFeedTraces: () => ({ mutate: vi.fn(), isPending: false }),
}));

function FakeShareDialog({ open, resourceType, resourceId }) {
  return open ? (
    <div data-testid="share-dialog">{`${resourceType}:${resourceId}`}</div>
  ) : null;
}
FakeShareDialog.propTypes = {
  open: PropTypes.bool,
  resourceType: PropTypes.string,
  resourceId: PropTypes.string,
};

vi.mock("src/components/share-dialog", () => ({
  ShareDialog: (props) => FakeShareDialog(props),
}));

vi.mock("../useAnalyzeRunner", () => ({ useAnalyzeRunner: () => {} }));
vi.mock("../components/ErrorMetadataPanel", () => ({
  default: () => null,
  LinearTeamPicker: () => null,
}));
vi.mock("../components/OverviewTab", () => ({ default: () => null }));
vi.mock("../components/TracesTab", () => ({ default: () => null }));
vi.mock("../components/TrendsTab", () => ({ default: () => null }));
vi.mock("../components/ClusterHeadlineCard", () => ({ default: () => null }));
vi.mock("../components/AnalyzeTab", () => ({ default: () => null }));

describe("Error Feed detail: Share", () => {
  it("opens the Share dialog for this issue", async () => {
    render(<ErrorFeedDetailView />);
    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Share" }));

    expect(screen.getByTestId("share-dialog")).toHaveTextContent(
      `error_feed_issue:${CLUSTER_ID}`,
    );
  });
});
