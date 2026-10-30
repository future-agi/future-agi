import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { VIEWER_CANT_RUN } from "src/sections/projects/runPermission";
import configsFixture from "./fixtures/configs.json";
import runFixture from "./fixtures/run_sessions_failures.json";
import unitsFixture from "./fixtures/run_sessions_failures_units.json";

const mocks = vi.hoisted(() => ({ readQuery: vi.fn(), post: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: (...args) => mocks.post(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  readQuery: (...args) => mocks.readQuery(...args),
  endpoints: {
    project: {
      insightsConfigs: () => "/tracer/insights/configs/",
      insightsConfig: (id) => `/tracer/insights/configs/${id}/`,
      insightsDraft: () => "/tracer/insights/draft/",
      insightsPreview: () => "/tracer/insights/preview/",
      insightsRuns: () => "/tracer/insights/runs/",
      insightsRun: (id) => `/tracer/insights/runs/${id}/`,
      insightsRunUnits: (id) => `/tracer/insights/runs/${id}/units/`,
      insightsRunAsk: (id) => `/tracer/insights/runs/${id}/ask/`,
      insightsEstimate: () => "/tracer/insights/estimate/",
    },
  },
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));

import InsightsView from "../InsightsView";

const PROJECT = "1b9823aa-866c-400e-b60e-d99ae498d72a";
const sessionsConfig = configsFixture.result.configs[1];
const CONFIG_ID = sessionsConfig.id;
const RUN_ID = runFixture.result.id;
const ok = (result) => Promise.resolve({ data: { status: true, result } });

// Signed in with this workspace role, as the auth provider would hold it.
function renderAs(role, search) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const theme = createTheme({ palette: palette("light"), typography });
  return render(
    <AuthContext.Provider value={{ authenticated: true, user: {}, role }}>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter
            initialEntries={[`/dashboard/observe/${PROJECT}/insights${search}`]}
          >
            <Routes>
              <Route
                path="/dashboard/observe/:observeId/insights"
                element={<InsightsView />}
              />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </AuthContext.Provider>,
  );
}

beforeEach(() => {
  window.matchMedia = vi.fn(() => ({
    matches: true,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
  mocks.readQuery.mockImplementation((url) => {
    if (url === "/tracer/insights/configs/") return ok(configsFixture.result);
    if (url === `/tracer/insights/configs/${CONFIG_ID}/`)
      return ok({ ...sessionsConfig, runs: [sessionsConfig.latest_run] });
    if (url === `/tracer/insights/runs/${RUN_ID}/`)
      return ok(runFixture.result);
    if (url === `/tracer/insights/runs/${RUN_ID}/units/`)
      return ok({
        units: unitsFixture.result.units,
        total: unitsFixture.result.total,
      });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
  // A Viewer is still shown the counts and prices: the estimate is a read.
  mocks.post.mockImplementation((url, body) =>
    url === "/tracer/insights/estimate/" && !body?.action
      ? ok({
          population: 108,
          population_is_lower_bound: false,
          sample_size: 108,
        })
      : url === "/tracer/insights/estimate/"
        ? ok({ credits_estimate: null })
        : Promise.reject(new Error(`unexpected ${url}`)),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("a Viewer on Insights", () => {
  it("sees what a new report would read, and can't run, draft or preview it", async () => {
    renderAs("workspace_viewer", "?new=1&lens=failures");
    expect(
      (await screen.findAllByText(/Analysing all 108 sessions/)).length,
    ).toBeGreaterThan(0);
    await waitFor(() =>
      expect(screen.getByText(VIEWER_CANT_RUN)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Run report" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Draft with AI" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Preview on \d+ examples/ }),
    ).toBeDisabled();
  });

  it("reads a report without running, editing, renaming or asking it", async () => {
    renderAs("workspace_viewer", `?report=${CONFIG_ID}`);
    expect(
      await screen.findByRole("button", { name: "Run now" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Rename report" }),
    ).toBeDisabled();
    expect(
      await screen.findByRole("textbox", { name: "Ask about this report" }),
    ).toBeDisabled();
    expect(screen.getAllByText(VIEWER_CANT_RUN).length).toBeGreaterThan(0);
    // The trend's "Run it again" is words, not a link.
    expect(screen.getByText(/One run so far/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run it again" })).toBeNull();
  });

  // A stopped report open, and a failed one in the rail.
  function serveEnded() {
    const ended = (overrides) => ({
      ...runFixture.result,
      status: "failed",
      analysed_count: 4,
      skipped_count: 0,
      ...overrides,
    });
    const stopped = ended({ error: "Stopped by Ada.", error_code: "stopped" });
    const config = { ...sessionsConfig, latest_run: stopped, runs: [stopped] };
    const failed = {
      ...configsFixture.result.configs[0],
      latest_run: ended({ id: "failed-run", error: "", error_code: "" }),
    };
    mocks.readQuery.mockImplementation((url) => {
      if (url === "/tracer/insights/configs/")
        return ok({ configs: [config, failed] });
      if (url === `/tracer/insights/configs/${CONFIG_ID}/`) return ok(config);
      if (url === `/tracer/insights/runs/${RUN_ID}/`) return ok(stopped);
      return ok({ units: [], total: 0 });
    });
  }

  it("can't run a stopped report again or retry a failed one, on the page or in the rail", async () => {
    serveEnded();
    renderAs("workspace_viewer", `?report=${CONFIG_ID}`);
    expect(await screen.findByText("Stopped")).toBeInTheDocument();
    const again = screen.getAllByRole("button", { name: "Run again" });
    expect(again).toHaveLength(2);
    again.forEach((button) => expect(button).toBeDisabled());
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  });

  it.each([
    ["workspace_viewer", false],
    ["workspace_member", true],
  ])("lets a %s stop a run: %s", async (role, can) => {
    const running = { ...runFixture.result, status: "running" };
    const config = { ...sessionsConfig, latest_run: running, runs: [running] };
    mocks.readQuery.mockImplementation((url) => {
      if (url === "/tracer/insights/configs/") return ok({ configs: [config] });
      if (url === `/tracer/insights/configs/${CONFIG_ID}/`) return ok(config);
      if (url === `/tracer/insights/runs/${RUN_ID}/`) return ok(running);
      return ok({ units: [], total: 0 });
    });
    renderAs(role, `?report=${CONFIG_ID}`);
    const stop = await screen.findByRole("button", { name: "Stop" });
    if (can) expect(stop).toBeEnabled();
    else expect(stop).toBeDisabled();
  });

  it("leaves a Member Run again and Retry", async () => {
    serveEnded();
    renderAs("workspace_member", `?report=${CONFIG_ID}`);
    expect(await screen.findByText("Stopped")).toBeInTheDocument();
    screen
      .getAllByRole("button", { name: "Run again" })
      .forEach((button) => expect(button).toBeEnabled());
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("leaves a Member the report's actions", async () => {
    renderAs("workspace_member", `?report=${CONFIG_ID}`);
    expect(
      await screen.findByRole("button", { name: "Run now" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(screen.queryByText(VIEWER_CANT_RUN)).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Run it again" }),
    ).toBeEnabled();
  });
});
