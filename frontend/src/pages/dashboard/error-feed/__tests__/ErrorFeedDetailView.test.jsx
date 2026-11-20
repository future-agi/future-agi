import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import ErrorFeedDetailView from "../ErrorFeedDetailView";
import { downloadFile } from "../exportTracesCsv";

const CLUSTER_ID = "E-9C01E76F";
const exportMutate = vi.hoisted(() => vi.fn());

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
      description: "The agent reads the region from the stale profile.",
      fix_hypothesis: "Read the region from the current request.",
      falsifier: "Wrong regions continue after the agent reads the request.",
    },
    isLoading: false,
  }),
  useUpdateErrorFeedIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useExportErrorFeedTraces: () => ({ mutate: exportMutate, isPending: false }),
}));

vi.mock("../exportTracesCsv", async (importOriginal) => ({
  ...(await importOriginal()),
  downloadFile: vi.fn(),
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

describe("Error Feed detail: Export", () => {
  const overview = {
    pattern_summary: { insights: [] },
    representative_traces: [
      { id: "t-1", timestamp: "2026-11-18T10:00:00Z", evidence: {} },
    ],
    representative_total: 5,
  };

  beforeEach(() => {
    exportMutate.mockReset();
    exportMutate.mockImplementation((_vars, { onSuccess }) =>
      onSuccess(overview),
    );
    vi.mocked(downloadFile).mockReset();
  });

  const choose = async (item) => {
    render(<ErrorFeedDetailView />);
    await userEvent.click(screen.getByRole("button", { name: "Export" }));
    await userEvent.click(screen.getByRole("menuitem", { name: item }));
  };

  it("downloads the traces as CSV", async () => {
    await choose(/Traces/);

    expect(exportMutate).toHaveBeenCalledWith(
      { clusterId: CLUSTER_ID },
      expect.any(Object),
    );
    expect(downloadFile).toHaveBeenCalledWith(
      expect.stringMatching(/^trace_id,timestamp,/),
      `error-feed-${CLUSTER_ID}-traces.csv`,
      "text/csv;charset=utf-8",
    );
  });

  it("downloads the stored analysis as Markdown", async () => {
    await choose(/Analysis/);

    expect(exportMutate).toHaveBeenCalledWith(
      { clusterId: CLUSTER_ID, repLimit: 10 },
      expect.any(Object),
    );
    const [markdown, filename, type] = vi.mocked(downloadFile).mock.calls[0];
    expect(filename).toBe(`error-feed-${CLUSTER_ID}-analysis.md`);
    expect(type).toBe("text/markdown;charset=utf-8");
    expect(markdown).toContain(
      "## Suggested change\n\nRead the region from the current request.",
    );
    expect(markdown).toContain("### t-1");
  });
});
