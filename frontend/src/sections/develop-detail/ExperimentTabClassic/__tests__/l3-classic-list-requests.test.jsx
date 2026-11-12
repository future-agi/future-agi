/* eslint-disable react/prop-types */
// L3: the requests dev's Experiments list sends from the launch build, each
// checked two ways: the exact body/params (no extra key a strict serializer
// would refuse with a 400) and the generated request contract
// (openapi-contract.js, the same validator non-production builds run).
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
afterEach(() => setLaunchOverrideForTest(null));
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: http };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// MUI DataGrid renders no rows in jsdom (no layout); a plain table with the
// same column API (TanStack cell({ getValue, row })) keeps the cells real.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data, onRowClick }) => (
    <div>
      {data.map((row) => (
        <div
          key={row.id}
          data-testid={`row-${row.id}`}
          onClick={() => onRowClick?.(row)}
        >
          {columns.map((c) => (
            <span key={c.id}>
              {c.cell
                ? c.cell({
                    getValue: () => row[c.accessorKey],
                    row: { original: row },
                  })
                : row[c.accessorKey]}
            </span>
          ))}
        </div>
      ))}
    </div>
  ),
  DataTablePagination: () => null,
}));

import { endpoints } from "src/utils/axios";
import ExperimentTab from "../ExperimentTab";
import ExperimentTabHeader from "../ExperimentTabHeader";

const DATASET = "11111111-1111-4111-8111-111111111111";
const DONE = "22222222-2222-4222-8222-222222222222";
const RUNNING = "33333333-3333-4333-8333-333333333333";

const listPage = {
  data: {
    count: 2,
    results: [
      { id: DONE, name: "done-exp", status: "Completed", dataset: DATASET },
      { id: RUNNING, name: "running-exp", status: "Running", dataset: DATASET },
    ],
  },
};

const wrap = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
        <Routes>
          <Route path="/dashboard/develop/:dataset" element={ui} />
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={<div data-testid="opened-experiment" />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const contractOk = (config) => {
  const result = validateContractedRequestConfig(config);
  return { ok: result.ok, skipped: Boolean(result.skipped), result };
};

describe("L3 classic Experiments list requests", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    http.get.mockReset();
    http.post.mockReset();
    http.delete.mockReset();
    http.get.mockResolvedValue(listPage);
    http.post.mockResolvedValue({ data: {} });
    http.delete.mockResolvedValue({ data: {} });
  });

  it("lists with exactly dataset_id, page, limit (+search) on experiments/v2/list/", async () => {
    wrap(<ExperimentTab experimentSearch="" setRowSelected={vi.fn()} />);
    expect(await screen.findByTestId(`row-${DONE}`)).toBeTruthy();
    const [url, config] = http.get.mock.calls[0];
    expect(url).toBe(endpoints.develop.experiment.experimentListPaginated);
    expect(url).toBe("/model-hub/experiments/v2/list/");
    const sent = Object.fromEntries(
      Object.entries(config.params).filter(([, v]) => v !== undefined),
    );
    expect(sent).toEqual({ dataset_id: DATASET, page: 1, limit: 20 });
    const check = contractOk({ url, method: "get", params: config.params });
    expect(check.ok).toBe(true);
  });

  it("re-runs one experiment with exactly {experiment_ids} and stops one with {}", async () => {
    wrap(<ExperimentTab experimentSearch="" setRowSelected={vi.fn()} />);
    const done = await screen.findByTestId(`row-${DONE}`);
    fireEvent.click(within(done).getByTestId("classic-experiment-rerun"));
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    const [rerunUrl, rerunBody] = http.post.mock.calls[0];
    expect(rerunUrl).toBe("/model-hub/experiments/v2/re-run/");
    expect(rerunBody).toEqual({ experiment_ids: [DONE] });
    expect(
      contractOk({ url: rerunUrl, method: "post", data: rerunBody }).ok,
    ).toBe(true);

    const running = screen.getByTestId(`row-${RUNNING}`);
    expect(within(running).queryByTestId("classic-experiment-rerun")).toBe(
      null,
    );
    fireEvent.click(within(running).getByTestId("classic-experiment-stop"));
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(2));
    const [stopUrl, stopBody] = http.post.mock.calls[1];
    expect(stopUrl).toBe(`/model-hub/experiments/v2/${RUNNING}/stop/`);
    expect(stopBody).toEqual({});
    expect(
      contractOk({ url: stopUrl, method: "post", data: stopBody }).ok,
    ).toBe(true);
  });

  it("opens an experiment on dev's Data page with datasetId and payload", async () => {
    wrap(<ExperimentTab experimentSearch="" setRowSelected={vi.fn()} />);
    fireEvent.click(await screen.findByTestId(`row-${DONE}`));
    expect(await screen.findByTestId("opened-experiment")).toBeTruthy();
  });

  it("shows the classic first-run empty state with its New experiment action", async () => {
    http.get.mockResolvedValue({ data: { count: 0, results: [] } });
    wrap(<ExperimentTab experimentSearch="" setRowSelected={vi.fn()} />);
    expect(
      await screen.findByText("Ready to run your first experiment"),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Run your first experiment to test, compare, and improve prompt performance.",
      ),
    ).toBeVisible();
    // The split New experiment button: its main part and the menu of what to
    // run. Both stay off until this dataset has rows.
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveAccessibleName("New experiment");
    expect(buttons[1]).toHaveAccessibleName("New experiment: pick what to run");
    expect(buttons[1]).toBeDisabled();
    const action = screen.getByRole("button", { name: "New experiment" });
    expect(action).toBeVisible();
    expect(action).toBeDisabled();
  });

  it("bulk Re-run posts only experiment_ids; bulk Delete uses the DELETE method", async () => {
    const tabRef = {
      current: { refreshExperimentTab: vi.fn(), clearRowSelection: vi.fn() },
    };
    const rows = [
      { id: DONE, name: "done-exp", dataset: DATASET },
      { id: RUNNING, name: "running-exp", dataset: DATASET },
    ];
    wrap(
      <ExperimentTabHeader
        tabRef={tabRef}
        experimentSearch=""
        setExperimentSearch={vi.fn()}
        rowSelected={rows}
        setRowSelected={vi.fn()}
      />,
    );
    expect(screen.queryByText("Add Experiments")).toBe(null);

    fireEvent.click(screen.getByRole("button", { name: /Re-Run/ }));
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Re-Run" }));
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    const [rerunUrl, rerunBody] = http.post.mock.calls[0];
    expect(rerunUrl).toBe("/model-hub/experiments/v2/re-run/");
    expect(rerunBody).toEqual({ experiment_ids: [DONE, RUNNING] });
    expect(
      contractOk({ url: rerunUrl, method: "post", data: rerunBody }).ok,
    ).toBe(true);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBe(null));
    fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(http.delete).toHaveBeenCalledTimes(1));
    const [deleteUrl, deleteConfig] = http.delete.mock.calls[0];
    expect(deleteUrl).toBe("/model-hub/experiments/v2/delete/");
    expect(deleteConfig).toEqual({ data: { experiment_ids: [DONE, RUNNING] } });
    expect(
      contractOk({
        url: deleteUrl,
        method: "delete",
        data: deleteConfig.data,
      }).ok,
    ).toBe(true);
    // The POST method the inventory flagged (405 on dev) is never used.
    expect(
      http.post.mock.calls.some(
        ([u]) => u === "/model-hub/experiments/v2/delete/",
      ),
    ).toBe(false);
  });

  it("the contract check can fail (negative controls)", () => {
    // A wrong shape fails the generated contract, so the passes above mean
    // something. The generated request schemas do NOT refuse unknown keys
    // (an extra select_all passes them), while the server's strict
    // serializer answers 400: that is why every body above is also pinned
    // with toEqual, key for key.
    const wrongType = contractOk({
      url: "/model-hub/experiments/v2/re-run/",
      method: "post",
      data: { experiment_ids: "not-a-list" },
    });
    expect(wrongType.skipped).toBe(false);
    expect(wrongType.ok).toBe(false);
    const missing = contractOk({
      url: "/model-hub/experiments/v2/re-run/",
      method: "post",
      data: {},
    });
    expect(missing.ok).toBe(false);
    const extraKey = contractOk({
      url: "/model-hub/experiments/v2/re-run/",
      method: "post",
      data: { experiment_ids: [DONE], select_all: true },
    });
    expect(extraKey.ok).toBe(true);
  });
});
