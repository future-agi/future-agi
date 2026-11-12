// K-2 (RT-09): the footer's `1 not recorded · Show`, its popover, Open and
// Record it.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import NotRecordedLink from "../NotRecordedLink";
import { notRecordedRowText } from "../recordModel";
import { recordFieldsOf } from "../recordModel";

const h = vi.hoisted(() => ({
  runs: [],
  get: vi.fn(),
  post: vi.fn(),
  notify: vi.fn(),
  role: "Owner",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post },
  endpoints: {
    develop: {
      datasetRuns: { list: (id) => `/model-hub/datasets/${id}/runs/` },
      kh: {
        recordRun: (e, r) => `/model-hub/experiments/v2/${e}/runs/${r}/record/`,
      },
    },
  },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: h.role }) }));
vi.mock("src/sections/common/ux/toast", () => ({ default: h.notify }));

const START = new Date(2026, 9, 23, 14, 5).getTime();
const scratch = {
  run_id: "r9a",
  label: "#9A",
  name: "Support reply v5",
  experiment_id: "e9",
  ...recordFieldsOf({ record: false }, START),
};
const quick = { run_id: "q1", label: "#2H", listed: false, kind: "quick_test" };
const kept = { run_id: "r1", label: "#1A", listed: true };

const Where = () => {
  const loc = useLocation();
  return <div data-testid="where">{`${loc.pathname}${loc.search}`}</div>;
};
const ui = () => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <MemoryRouter initialEntries={["/dashboard/develop/ds?tab=experiments"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <NotRecordedLink datasetId="ds" />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  </QueryClientProvider>
);

describe("NotRecordedLink", () => {
  beforeEach(() => {
    h.role = "Owner";
    h.runs = [scratch, quick, kept];
    h.get.mockReset();
    h.get.mockImplementation(() =>
      Promise.resolve({ data: { result: { results: h.runs } } }),
    );
    h.post.mockReset();
    h.post.mockImplementation(() => {
      h.runs = h.runs.map((r) =>
        r.run_id === "r9a"
          ? { ...r, listed: true, recorded: true, expires_at: null }
          : r,
      );
      return Promise.resolve({ data: { status: true } });
    });
    h.notify.mockReset();
  });

  it("reads R1 with quick tests (the quick tests link's key) and counts only Record-off runs", async () => {
    render(ui());
    expect(await screen.findByTestId("not-recorded-link")).toHaveTextContent(
      "1 not recorded · Show",
    );
    expect(h.get).toHaveBeenCalledWith("/model-hub/datasets/ds/runs/", {
      params: { limit: 500, include: "metrics,config,lineage,quick_tests" },
    });
  });

  it("lists `#9A · name` with no deletion time; Open goes to its results by link", async () => {
    render(ui());
    fireEvent.click(await screen.findByTestId("not-recorded-toggle"));
    const row = await screen.findByTestId("not-recorded-row");
    expect(row).toHaveTextContent(notRecordedRowText(scratch));
    expect(notRecordedRowText(scratch, START + 60000)).toBe(
      "#9A · Support reply v5",
    );
    // W7: the server deletes no run, so the open popover (in a portal) and
    // the footer say nothing about it.
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/deleted|24 hours/);
    fireEvent.click(within(row).getByTestId("not-recorded-open"));
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/dashboard/develop/ds/compare/data?runs=r9a",
    );
  });

  it("Record it posts the record path, toasts, refreshes R1 and the link goes", async () => {
    render(ui());
    fireEvent.click(await screen.findByTestId("not-recorded-toggle"));
    const row = await screen.findByTestId("not-recorded-row");
    fireEvent.click(within(row).getByTestId("not-recorded-record"));
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(
        "/model-hub/experiments/v2/e9/runs/r9a/record/",
      ),
    );
    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        "#9A recorded · now on the leaderboard",
        expect.objectContaining({ variant: "success" }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("not-recorded-link")).toBeNull(),
    );
    expect(h.get.mock.calls.length).toBeGreaterThan(1);
  });

  it("a viewer cannot record", async () => {
    h.role = "Viewer";
    render(ui());
    fireEvent.click(await screen.findByTestId("not-recorded-toggle"));
    expect(await screen.findByTestId("not-recorded-record")).toBeDisabled();
  });

  it("absent when nothing is unrecorded", async () => {
    h.runs = [quick, kept];
    render(ui());
    await waitFor(() => expect(h.get).toHaveBeenCalled());
    expect(screen.queryByTestId("not-recorded-link")).toBeNull();
  });
});
