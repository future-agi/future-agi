import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import RerunDialog from "../RerunDialog";

const mocks = vi.hoisted(() => ({
  startRun: vi.fn(),
  count: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/projects/ScopeFilterBar", () => ({
  default: () => null,
}));
vi.mock("../insightsApi", async (importOriginal) => ({
  ...(await importOriginal()),
  startRun: (...args) => mocks.startRun(...args),
  usePopulationCount: (...args) => mocks.count(...args),
}));

const theme = createTheme({ palette: palette("light"), typography });

const config = {
  id: "config-1",
  name: "Weekly failures",
  unit: "sessions",
  lens: "failures",
  sample_size: 300,
  scope: {},
  has_taxonomy: false,
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderDialog({ onClose, onStarted }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <RerunDialog
            open
            projectId="project-1"
            config={config}
            onClose={onClose}
            onStarted={onStarted}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.count.mockReturnValue({
    data: { total: 108, totalIsLowerBound: false, credits: null },
    isLoading: false,
    isPlaceholderData: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RerunDialog while a run is starting (W10-INSIGHTS-U07)", () => {
  it("stays open with no way to cancel until the server answers", async () => {
    const onClose = vi.fn();
    const onStarted = vi.fn();
    const first = deferred();
    mocks.startRun.mockReturnValueOnce(first.promise);
    renderDialog({ onClose, onStarted });
    const dialog = screen.getByRole("dialog");
    const runAgain = within(dialog).getByRole("button", { name: "Run again" });
    await waitFor(() => expect(runAgain).toBeEnabled());

    fireEvent.click(runAgain);
    expect(mocks.startRun).toHaveBeenCalledTimes(1);
    // The server may already be starting (and charging) the run: Cancel
    // would only stop the browser waiting, so nothing closes the dialog.
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    expect(cancel).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    // Refused: the reason shows and the dialog can be left again.
    first.reject({ statusCode: 429, message: "Too many reports this hour." });
    await waitFor(() => expect(cancel).toBeEnabled());
    const reason = within(dialog).getByRole("alert");
    expect(reason).toHaveTextContent("Too many reports this hour.");
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(onStarted).not.toHaveBeenCalled();

    // Started: the dialog hands over to the new run, never to a cancel.
    mocks.startRun.mockResolvedValueOnce({
      run_id: "run-2",
      config_id: "config-1",
    });
    fireEvent.click(runAgain);
    await waitFor(() =>
      expect(onStarted).toHaveBeenCalledWith("config-1", "run-2"),
    );
    expect(mocks.startRun).toHaveBeenCalledTimes(2);
    expect(onClose).not.toHaveBeenCalled();
  });
});
