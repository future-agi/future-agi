import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
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

vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => {
  const enqueueSnackbar = vi.fn();
  return {
    ...(await importOriginal()),
    enqueueSnackbar,
    useSnackbar: () => ({ enqueueSnackbar }),
  };
});

vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
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

describe("Error Feed detail: Copy cluster ID", () => {
  beforeEach(() => {
    vi.mocked(copyToClipboard).mockReset();
    vi.mocked(enqueueSnackbar).mockReset();
  });

  it("copies the cluster ID and briefly turns the button into a check", async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(true);
    render(<ErrorFeedDetailView />);

    await userEvent.click(
      screen.getByRole("button", { name: "Copy cluster ID" }),
    );

    expect(copyToClipboard).toHaveBeenCalledWith(CLUSTER_ID);
    expect(
      await screen.findByRole("button", { name: "Copied" }),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: "Copy cluster ID" }),
        ).toBeInTheDocument(),
      { timeout: 3000 },
    );
  });

  it("says so when the copy fails", async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(false);
    render(<ErrorFeedDetailView />);

    await userEvent.click(
      screen.getByRole("button", { name: "Copy cluster ID" }),
    );

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Couldn't copy the cluster ID",
      { variant: "error" },
    );
  });
});
