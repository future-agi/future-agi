// SIM-04: when the Run Simulation list read fails, the page says so and
// offers Try again. A failed read never reads as "No tests found", and
// Create a Simulation stays on the page whatever the list state. The
// first-run screen follows only a successful read that came back empty, and
// a list already on screen stays when a later refresh fails.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";

import RunTestsContent from "../RunTestsContent";

const http = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get, delete: http.delete },
  endpoints: {
    runTests: {
      list: "/simulate/run-tests/",
      detail: (id) => `/simulate/run-tests/${id}/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
// The real table and pager, imported directly. The barrel also loads
// VoiceCallsGrid and the call-log helpers, which read more of
// src/utils/axios than this list does.
vi.mock("src/components/data-table", async () => ({
  DataTable: (await vi.importActual("src/components/data-table/DataTable"))
    .default,
  DataTablePagination: (
    await vi.importActual("src/components/data-table/DataTablePagination")
  ).default,
}));

const ERROR_LINE = "Couldn't load your simulations.";
const FIRST_RUN_LINE =
  "Get started by creating your first test to evaluate your AI agents.";

// GET /simulate/run-tests/ failing with a 5xx (RunTestListView returns 500
// or 503 on failure).
const serverError = () => new Error("Request failed with status code 500");

// GET /simulate/run-tests/ as the component reads it: `select` takes `.data`.
const listPage = (results) => ({ data: { count: results.length, results } });

const SIMULATION = {
  id: "sim-1",
  name: "Checkout voice agent",
  agent_definition_detail: null,
  scenarios_detail: [],
  evals_detail: [],
  last_run_at: null,
};

const renderList = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onCreateClick = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <RunTestsContent onCreateClick={onCreateClick} />
    </QueryClientProvider>,
  );
  return { queryClient, onCreateClick };
};

// The list query's own status. Waiting on it lets a check run against the
// settled read without relying on the error line being on the page.
const listStatus = (queryClient) =>
  queryClient.getQueryCache().findAll({ queryKey: ["run-tests"] })[0]?.state
    .status;

describe("Run Simulation list when the read fails (SIM-04)", () => {
  beforeEach(() => {
    http.get.mockReset();
    http.delete.mockReset();
  });

  it("says the list failed and offers Try again, instead of 'No tests found'", async () => {
    http.get.mockRejectedValue(serverError());
    renderList();

    expect(await screen.findByText(ERROR_LINE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(screen.queryByText("No tests found")).toBeNull();
  });

  it("keeps Create a Simulation on the page when the list fails", async () => {
    http.get.mockRejectedValue(serverError());
    const { queryClient, onCreateClick } = renderList();
    await waitFor(() => expect(listStatus(queryClient)).toBe("error"));

    expect(
      await screen.findByRole("button", { name: "Create a Simulation" }),
    ).toBeEnabled();

    // The same button, on the error screen.
    await screen.findByText(ERROR_LINE);
    fireEvent.click(
      screen.getByRole("button", { name: "Create a Simulation" }),
    );
    expect(onCreateClick).toHaveBeenCalledTimes(1);
  });

  it("Try again reads the list again and shows it", async () => {
    http.get
      .mockRejectedValueOnce(serverError())
      .mockResolvedValueOnce(listPage([SIMULATION]));
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Checkout voice agent")).toBeInTheDocument();
    expect(screen.queryByText(ERROR_LINE)).toBeNull();
    expect(http.get).toHaveBeenCalledTimes(2);
  });

  it("keeps the first-run screen when the read succeeds with no simulations", async () => {
    http.get.mockResolvedValue(listPage([]));
    renderList();

    expect(await screen.findByText(FIRST_RUN_LINE)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create a Simulation" }),
    ).toBeEnabled();
    expect(screen.queryByText(ERROR_LINE)).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("keeps the list on screen when a later refresh fails", async () => {
    http.get.mockResolvedValueOnce(listPage([SIMULATION]));
    const { queryClient } = renderList();
    expect(await screen.findByText("Checkout voice agent")).toBeInTheDocument();

    http.get.mockRejectedValueOnce(serverError());
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["run-tests"] });
    });
    await waitFor(() => expect(listStatus(queryClient)).toBe("error"));

    expect(screen.getByText("Checkout voice agent")).toBeInTheDocument();
    expect(screen.queryByText(ERROR_LINE)).toBeNull();
    expect(http.get).toHaveBeenCalledTimes(2);
  });
});
