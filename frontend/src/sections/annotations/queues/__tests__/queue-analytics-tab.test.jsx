import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import QueueAnalyticsTab from "../view/queue-analytics-tab";

const {
  mockAxiosGet,
  mockCreateObjectURL,
  mockRevokeObjectURL,
  mockUseQueueAnalytics,
  mockEnqueueSnackbar,
} = vi.hoisted(() => ({
  mockAxiosGet: vi.fn(),
  mockCreateObjectURL: vi.fn(),
  mockRevokeObjectURL: vi.fn(),
  mockUseQueueAnalytics: vi.fn(),
  mockEnqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  annotationQueueEndpoints: {
    export: (queueId) => `/model-hub/annotation-queues/${queueId}/export/`,
  },
  useQueueAnalytics: mockUseQueueAnalytics,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mockAxiosGet,
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: mockEnqueueSnackbar,
}));

describe("QueueAnalyticsTab", () => {
  beforeEach(() => {
    mockAxiosGet.mockReset();
    mockCreateObjectURL.mockReset();
    mockRevokeObjectURL.mockReset();
    mockEnqueueSnackbar.mockReset();
    mockCreateObjectURL.mockReturnValue("blob:queue-export");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: mockCreateObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: mockRevokeObjectURL,
    });
  });

  it("renders pending-review items as a separate in-review bucket", () => {
    mockUseQueueAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        total: 4,
        status_breakdown: {
          pending: 1,
          in_review: 1,
          needs_changes: 1,
          resubmitted: 1,
          completed: 1,
          skipped: 0,
        },
        throughput: { avg_per_day: 0, daily: [] },
        annotator_performance: [],
        label_distribution: {},
      },
    });

    render(<QueueAnalyticsTab queueId="queue-1" />);

    expect(screen.getByText("In Review: 1")).toBeInTheDocument();
    expect(screen.queryByText(/In Progress:/)).not.toBeInTheDocument();
    expect(screen.getByText("Needs Changes: 1")).toBeInTheDocument();
    expect(screen.getByText("Resubmitted: 1")).toBeInTheDocument();
    expect(screen.getByText("Pending Annotation: 1")).toBeInTheDocument();
  });

  it("renders completed analytics as item counts, not label score counts", () => {
    mockUseQueueAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        total: 10,
        status_breakdown: {
          pending: 6,
          in_review: 0,
          needs_changes: 0,
          resubmitted: 0,
          completed: 4,
          skipped: 0,
        },
        throughput: { avg_per_day: 0.1, total_completed: 4, daily: [] },
        annotator_performance: [
          {
            user_id: "user-1",
            name: "Kartik",
            completed: 4,
            last_active: null,
          },
        ],
        label_distribution: {},
      },
    });

    render(<QueueAnalyticsTab queueId="queue-1" />);

    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("Completed: 4")).toBeInTheDocument();
    const performanceRow = screen.getByRole("row", {
      name: /Kartik\s+4/i,
    });
    expect(within(performanceRow).getByText("4")).toBeInTheDocument();
  });

  it("downloads CSV exports from the backend export endpoint", async () => {
    const user = userEvent.setup();
    mockUseQueueAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        total: 1,
        status_breakdown: {
          pending: 0,
          in_review: 0,
          needs_changes: 0,
          resubmitted: 0,
          completed: 1,
          skipped: 0,
        },
        throughput: { avg_per_day: 1, total_completed: 1, daily: [] },
        annotator_performance: [],
        label_distribution: {},
      },
    });
    mockAxiosGet.mockResolvedValue({
      data: 'item_id,review_status,value\nitem-1,approved,"{""value"":""up""}"',
    });

    render(<QueueAnalyticsTab queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    await waitFor(() => {
      expect(mockAxiosGet).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/export/",
        { params: { export_format: "csv" }, responseType: "blob" },
      );
    });
    expect(mockCreateObjectURL).toHaveBeenCalled();
    expect(mockRevokeObjectURL).toHaveBeenCalledWith("blob:queue-export");
  });

  it("surfaces the backend 413 cap message instead of the generic failure", async () => {
    const user = userEvent.setup();
    mockUseQueueAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        total: 1,
        status_breakdown: {
          pending: 0,
          in_review: 0,
          needs_changes: 0,
          resubmitted: 0,
          completed: 1,
          skipped: 0,
        },
        throughput: { avg_per_day: 1, total_completed: 1, daily: [] },
        annotator_performance: [],
        label_distribution: {},
      },
    });
    // The export uses responseType:"blob", so the 413 body arrives as a Blob —
    // the handler must read and parse it to surface the server's cap message.
    const capMessage =
      "This export has 5,000 rows, over the 2,000-row cap. Narrow the queue.";
    const payload = JSON.stringify({ result: capMessage, code: "export_too_large" });
    const errorBody = new Blob([payload], { type: "application/json" });
    // jsdom's Blob has no `.text()` (real browsers do); stub it so the Blob
    // still passes `instanceof Blob` and reads back the browser way.
    errorBody.text = async () => payload;
    mockAxiosGet.mockRejectedValue({
      response: { status: 413, data: errorBody },
    });

    render(<QueueAnalyticsTab queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    // Reverting the Blob-parsing branch to a bare `enqueueSnackbar("Export
    // failed")` would surface the generic string and fail this assertion.
    await waitFor(() => {
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(capMessage, {
        variant: "error",
      });
    });
    expect(mockCreateObjectURL).not.toHaveBeenCalled();
  });
});
