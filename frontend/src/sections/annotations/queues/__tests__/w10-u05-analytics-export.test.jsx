import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
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

const ANALYTICS = {
  total: 3,
  status_breakdown: {
    pending: 2,
    in_review: 0,
    needs_changes: 0,
    resubmitted: 0,
    completed: 1,
    skipped: 0,
  },
  throughput: { avg_per_day: 1, total_completed: 1, daily: [] },
  annotator_performance: [],
  label_distribution: {},
};

describe("QueueAnalyticsTab export (W10-ANNOTATIONS-U05)", () => {
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
    mockUseQueueAnalytics.mockReturnValue({
      isLoading: false,
      data: ANALYTICS,
    });
  });

  it("shows progress and blocks repeat clicks while an export runs", async () => {
    const user = userEvent.setup();
    let resolveExport;
    mockAxiosGet.mockReturnValue(
      new Promise((resolve) => {
        resolveExport = resolve;
      }),
    );

    render(<QueueAnalyticsTab queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    const activeButton = await screen.findByRole("button", {
      name: /exporting\.\.\./i,
    });
    expect(activeButton).toBeDisabled();
    expect(screen.getByRole("button", { name: /export json/i })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /export csv/i }),
    ).not.toBeInTheDocument();

    // A disabled button ignores clicks, so a second click starts no request.
    fireEvent.click(activeButton);
    expect(mockAxiosGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveExport({ data: "item_id\nitem-1" });
    });

    const csvButton = await screen.findByRole("button", {
      name: /export csv/i,
    });
    await waitFor(() => expect(csvButton).toBeEnabled());
    expect(screen.getByRole("button", { name: /export json/i })).toBeEnabled();
    expect(mockCreateObjectURL).toHaveBeenCalledTimes(1);
  });

  it("explains an oversized export using the app's flattened error shape", async () => {
    const user = userEvent.setup();
    // utils/axios rejects with {...errData, statusCode, transportCode,
    // responseHeaders}. With responseType "blob" the 413 body is a Blob, which
    // spreads to no fields, so only statusCode tells the handler what happened.
    mockAxiosGet.mockRejectedValue({
      statusCode: 413,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: { "x-request-id": undefined },
    });

    render(<QueueAnalyticsTab queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    await waitFor(() => {
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "This queue has too many items to export in one file. Use Export > Export to Dataset at the top of the page to export them all.",
        { variant: "error" },
      );
    });
    expect(mockEnqueueSnackbar).not.toHaveBeenCalledWith(
      "Export failed",
      expect.anything(),
    );
    expect(mockCreateObjectURL).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: /export csv/i }),
    ).toBeEnabled();
  });
});
