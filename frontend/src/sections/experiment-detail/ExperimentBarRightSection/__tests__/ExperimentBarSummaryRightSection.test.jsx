import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExperimentBarSummaryRightSection from "../ExperimentBarSummaryRightSection";

const mockGet = vi.fn();
const mockTrackEvent = vi.fn();
const mockEnqueueSnackbar = vi.fn();
const mockSetChooseWinnerOpen = vi.fn();
let anchorClick;

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
  },
  endpoints: {
    develop: {
      experiment: {
        downloadExperiment: (experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/download/`,
      },
    },
  },
}));

vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    trackEvent: (...args) => mockTrackEvent(...args),
  };
});

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("src/sections/experiment-detail/experiment-context", () => ({
  useExperimentDetailContext: () => ({
    setChooseWinnerOpen: mockSetChooseWinnerOpen,
  }),
}));

function renderComponent({
  initialEntry = "/dashboard/develop/experiment/exp-123/summary",
  routePath = "/dashboard/develop/experiment/:experimentId/summary",
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path={routePath}
            element={<ExperimentBarSummaryRightSection />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ExperimentBarSummaryRightSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    vi.stubGlobal("URL", {
      ...window.URL,
      createObjectURL: vi.fn(() => "blob:experiment-csv"),
      revokeObjectURL: vi.fn(),
    });
  });

  it("renders Download CSV before the unchanged Choose winner action", () => {
    renderComponent();

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveAccessibleName("Download CSV");
    expect(buttons[1]).toHaveAccessibleName("Choose winner");
  });

  it("hides Download CSV on the individual experiment summary route", () => {
    renderComponent({
      initialEntry:
        "/dashboard/develop/individual-experiment/ind-123/summary",
      routePath:
        "/dashboard/develop/individual-experiment/:individualExperimentId/summary",
    });

    expect(
      screen.queryByRole("button", { name: "Download CSV" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Choose winner" }),
    ).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("tracks and starts the experiment CSV request once while disabled", async () => {
    let resolveDownload;
    mockGet.mockReturnValue(
      new Promise((resolve) => {
        resolveDownload = resolve;
      }),
    );
    const user = userEvent.setup();
    renderComponent();

    const downloadButton = screen.getByRole("button", { name: "Download CSV" });
    await user.click(downloadButton);

    expect(mockTrackEvent).toHaveBeenCalledWith("Experiment_download_clicked");
    expect(mockGet).toHaveBeenCalledWith(
      "/model-hub/experiments/v2/exp-123/download/",
      { responseType: "blob" },
    );
    expect(
      screen.getByRole("button", { name: "Downloading…" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Downloading…" }));
    expect(mockGet).toHaveBeenCalledTimes(1);

    resolveDownload({ data: "experiment,data" });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Download CSV" })).toBeEnabled();
    });
  });

  it("accepts one same-turn activation and releases the guard after success", async () => {
    let resolveDownload;
    mockGet
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveDownload = resolve;
          }),
      )
      .mockResolvedValueOnce({ data: "second,download" });
    renderComponent();

    const downloadButton = screen.getByRole("button", {
      name: "Download CSV",
    });
    act(() => {
      downloadButton.click();
      downloadButton.click();
    });

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    expect(mockTrackEvent).toHaveBeenCalledTimes(1);

    resolveDownload({ data: "first,download" });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Download CSV" })).toBeEnabled();
    });

    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(mockTrackEvent).toHaveBeenCalledTimes(2);
  });

  it("releases the atomic guard after a failed request", async () => {
    let rejectDownload;
    mockGet
      .mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            rejectDownload = reject;
          }),
      )
      .mockResolvedValueOnce({ data: "retry,download" });
    renderComponent();

    const downloadButton = screen.getByRole("button", {
      name: "Download CSV",
    });
    act(() => {
      downloadButton.click();
      downloadButton.click();
    });

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    expect(mockTrackEvent).toHaveBeenCalledTimes(1);

    rejectDownload(new Error("network unavailable"));
    await waitFor(() => {
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Failed to download experiment",
        { variant: "error" },
      );
      expect(screen.getByRole("button", { name: "Download CSV" })).toBeEnabled();
    });

    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(mockTrackEvent).toHaveBeenCalledTimes(2);
  });

  it("downloads the response with the experiment filename and reports success", async () => {
    mockGet.mockResolvedValue({ data: "experiment,data" });
    const user = userEvent.setup();
    renderComponent();

    await user.click(screen.getByRole("button", { name: "Download CSV" }));

    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(1));
    const link = anchorClick.mock.instances[0];
    expect(link.download).toBe("experiment-exp-123.csv");
    expect(link.href).toBe("blob:experiment-csv");
    expect(window.URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(window.URL.revokeObjectURL).toHaveBeenCalledWith(
      "blob:experiment-csv",
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Experiment downloaded successfully",
      { variant: "success" },
    );
  });

  it("reports a failed request and restores the download action", async () => {
    mockGet.mockRejectedValue(new Error("network unavailable"));
    const user = userEvent.setup();
    renderComponent();

    await user.click(screen.getByRole("button", { name: "Download CSV" }));

    await waitFor(() => {
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Failed to download experiment",
        { variant: "error" },
      );
    });
    expect(screen.getByRole("button", { name: "Download CSV" })).toBeEnabled();
  });

  it("keeps the Choose winner behavior unchanged", async () => {
    const user = userEvent.setup();
    renderComponent();

    await user.click(screen.getByRole("button", { name: "Choose winner" }));

    expect(mockTrackEvent).toHaveBeenCalledWith(
      "Dataset_experiment_winner_clicked",
    );
    expect(mockSetChooseWinnerOpen).toHaveBeenCalledWith(true);
  });
});
