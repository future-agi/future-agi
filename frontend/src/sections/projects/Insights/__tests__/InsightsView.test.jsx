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
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import configsFixture from "./fixtures/configs.json";
import runFixture from "./fixtures/run_sessions_failures.json";
import unitsFixture from "./fixtures/run_sessions_failures_units.json";
import enrichedRunFixture from "./fixtures/run_sessions_failures_enriched.json";
import enrichedUnitsFixture from "./fixtures/run_sessions_failures_units_enriched.json";
import askFixture from "./fixtures/ask_answer.json";
import omegaRunFixture from "./fixtures/run_sessions_failures_omega.json";

const mocks = vi.hoisted(() => ({
  readQuery: vi.fn(),
  post: vi.fn(),
  actionCredits: vi.fn(),
  patch: vi.fn(),
  collect: vi.fn(),
  enriched: false,
  omega: false,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: (...args) =>
      args[1]?.action ? mocks.actionCredits(...args) : mocks.post(...args),
    patch: (...args) => mocks.patch(...args),
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
      insightsRunStop: (id) => `/tracer/insights/runs/${id}/stop/`,
      insightsEstimate: () => "/tracer/insights/estimate/",
    },
  },
}));
// The views' filter panel: here it applies a cost filter, as a pick in it
// would.
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ onApply, onClose, currentFilters }) => (
    <div>
      <button
        type="button"
        onClick={() =>
          onApply([
            ...(currentFilters || []),
            {
              field: "total_cost",
              fieldCategory: "system",
              fieldType: "number",
              apiColType: "SYSTEM_METRIC",
              operator: "greater_than",
              value: "2",
            },
          ])
        }
      >
        Pick cost over 2
      </button>
      <button type="button" onClick={onClose}>
        Close panel
      </button>
    </div>
  ),
}));

vi.mock("src/sections/projects/observePopulation", async (importOriginal) => ({
  ...(await importOriginal()),
  collectPopulation: (...args) => mocks.collect(...args),
}));

import InsightsView from "../InsightsView";
import {
  presetWindow,
  viewSeed,
  windowItem,
} from "src/sections/projects/observeScope";

const PROJECT = "1b9823aa-866c-400e-b60e-d99ae498d72a";
const sessionsConfig = configsFixture.result.configs[1];
const CONFIG_ID = sessionsConfig.id;
const RUN_ID = runFixture.result.id;
const configDetail = { ...sessionsConfig, runs: [sessionsConfig.latest_run] };

const ok = (result) => Promise.resolve({ data: { status: true, result } });

function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <div data-testid="location">{location.search}</div>
      <button type="button" onClick={() => navigate(-1)}>
        Browser back
      </button>
    </>
  );
}

function UserPageProbe() {
  const location = useLocation();
  return (
    <div data-testid="user-page">{location.pathname + location.search}</div>
  );
}

function renderAt(search) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const theme = createTheme({ palette: palette("light"), typography });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter
          initialEntries={[`/dashboard/observe/${PROJECT}/insights${search}`]}
        >
          <Routes>
            <Route
              path="/dashboard/observe/:observeId/insights"
              element={
                <>
                  <InsightsView />
                  <LocationProbe />
                </>
              }
            />
            <Route
              path="/dashboard/settings/ee-licenses"
              element={<div>License page</div>}
            />
            <Route
              path="/dashboard/users/:userId"
              element={<UserPageProbe />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const population = Array.from({ length: 108 }, (_, i) => ({
  id: `s${i}`,
  time: "2026-10-10T00:00:00Z",
  user_id: `u${i % 39}`,
}));

// The rail starts open on screens of 1440px and wider.
function setViewport(width) {
  window.matchMedia = vi.fn((query) => {
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0);
    return {
      matches: width >= min,
      media: query,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    };
  });
}

// Node's own localStorage doesn't persist without --localstorage-file, so
// each test gets a fresh in-memory one.
function memoryStorage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
    clear: () => items.clear(),
  };
}

beforeEach(() => {
  setViewport(1440);
  vi.stubGlobal("localStorage", memoryStorage());
  mocks.enriched = false;
  mocks.omega = false;
  mocks.actionCredits.mockImplementation(() => ok({ credits_estimate: null }));
  mocks.readQuery.mockImplementation((url, { params } = {}) => {
    if (url === "/tracer/insights/configs/") return ok(configsFixture.result);
    if (url === `/tracer/insights/configs/${CONFIG_ID}/`)
      return ok(configDetail);
    if (url === `/tracer/insights/runs/${RUN_ID}/`)
      return ok(
        mocks.omega
          ? { ...omegaRunFixture.result, id: RUN_ID }
          : mocks.enriched
            ? enrichedRunFixture.result
            : runFixture.result,
      );
    if (url === `/tracer/insights/runs/${RUN_ID}/units/`) {
      const source = mocks.enriched ? enrichedUnitsFixture : unitsFixture;
      const units = source.result.units;
      if (params?.unit_ids) {
        const cited = (source.result.cited || []).find(
          (u) => u.unit_id === params.unit_ids,
        );
        return ok({
          units: [cited || { ...units[0], unit_id: params.unit_ids }],
          total: 1,
        });
      }
      return ok({ units, total: source.result.total });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
  mocks.collect.mockImplementation(({ limit }) =>
    Promise.resolve({
      items: population.slice(0, limit),
      total: 108,
      totalIsLowerBound: false,
      truncated: false,
    }),
  );
  // The server counts a scope; a run is created with its config.
  mocks.post.mockImplementation((url) =>
    url === "/tracer/insights/estimate/"
      ? ok({
          population: 108,
          population_is_lower_bound: false,
          sample_size: 108,
        })
      : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
  );
});

const postsTo = (url) =>
  mocks.post.mock.calls.filter(([called]) => called === url);

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("InsightsView", () => {
  it.each(["completed", "failed"])(
    "renders a running report entering %s and stops polling",
    async (outcome) => {
      const base = mocks.readQuery.getMockImplementation();
      const runUrl = `/tracer/insights/runs/${RUN_ID}/`;
      let reads = 0;
      const failed = {
        ...runFixture.result,
        status: "failed",
        analysed_count: 0,
        skipped_count: 125,
        error: "This report stopped before it finished. Run it again.",
      };
      mocks.readQuery.mockImplementation((url, options) => {
        if (url !== runUrl) return base(url, options);
        reads += 1;
        return ok(
          reads === 1
            ? {
                ...runFixture.result,
                status: "running",
                stage: "reading",
                progress: { stage: "reading", done: 2, total: 125 },
              }
            : outcome === "failed"
              ? failed
              : runFixture.result,
        );
      });
      renderAt(`?report=${CONFIG_ID}`);
      expect(
        await screen.findByRole("list", { name: "Report stages" }),
      ).toBeInTheDocument();
      if (outcome === "failed") {
        expect(
          await screen.findByText(
            "0 analysed · 125 skipped",
            {},
            { timeout: 6000 },
          ),
        ).toBeInTheDocument();
        expect(screen.getByText(failed.error)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
      } else {
        expect(
          await screen.findByRole(
            "textbox",
            { name: "Ask about this report" },
            { timeout: 6000 },
          ),
        ).toBeInTheDocument();
      }
      expect(
        screen.queryByRole("list", { name: "Report stages" }),
      ).not.toBeInTheDocument();
      const settledReads = reads;
      await new Promise((resolve) => setTimeout(resolve, 2400));
      expect(reads).toBe(settledReads);
    },
    15000,
  );

  it("shows failed history outcomes, a safe reason, and a retry on the same handed-over rows", async () => {
    const rows = [{ id: "s1" }, { id: "s2" }];
    const store = new Map([
      [`insights-population:${CONFIG_ID}`, JSON.stringify(rows)],
    ]);
    vi.stubGlobal("sessionStorage", {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
    });
    const failed = {
      ...runFixture.result,
      status: "failed",
      analysed_count: 0,
      skipped_count: 125,
      error: "DB::Exception SELECT secret FROM internal",
      scope: { preset: "fixed", label: "Handed-over sessions" },
    };
    const config = {
      ...configDetail,
      scope: failed.scope,
      latest_run: failed,
      runs: [failed],
    };
    mocks.readQuery.mockImplementation((url) => {
      if (url === "/tracer/insights/configs/") return ok({ configs: [config] });
      if (url === `/tracer/insights/configs/${CONFIG_ID}/`) return ok(config);
      if (url === `/tracer/insights/runs/${RUN_ID}/`) return ok(failed);
      return ok({ units: [], total: 0 });
    });
    renderAt(`?report=${CONFIG_ID}`);
    expect(await screen.findAllByText("0 analysed · 125 skipped")).toHaveLength(
      2,
    );
    expect(screen.queryByText(/DB::Exception/)).not.toBeInTheDocument();
    expect(
      screen.getAllByText(
        "The report could not be completed. Try again in a moment.",
      ),
    ).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" })[0]);
    const dialog = await screen.findByRole("dialog");
    const rerun = within(dialog).getByRole("button", { name: "Run again" });
    await waitFor(() => expect(rerun).toBeEnabled());
    fireEvent.click(rerun);
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    expect(postsTo("/tracer/insights/runs/")[0][1].population).toEqual(rows);
  });

  describe("a run that retrying can't help", () => {
    const LICENCE =
      "Insights couldn't use Future AGI's managed models: this license couldn't be activated with the managed service. Check it in Settings → License, or set INSIGHTS_FAST_MODEL and INSIGHTS_THINKING_MODEL to models your own gateway serves.";
    const GATEWAY =
      "The model gateway doesn't serve turing_flash, so trying again won't help: an administrator needs to check the Insights model settings.";

    function serveFailed(error, errorCode) {
      const failed = {
        ...runFixture.result,
        status: "failed",
        analysed_count: 0,
        skipped_count: 0,
        error,
        error_code: errorCode,
      };
      const config = { ...configDetail, latest_run: failed, runs: [failed] };
      mocks.readQuery.mockImplementation((url) => {
        if (url === "/tracer/insights/configs/")
          return ok({ configs: [config] });
        if (url === `/tracer/insights/configs/${CONFIG_ID}/`) return ok(config);
        if (url === `/tracer/insights/runs/${RUN_ID}/`) return ok(failed);
        return ok({ units: [], total: 0 });
      });
    }

    it("names the licence and leads to the License settings instead of Retry", async () => {
      serveFailed(LICENCE, "license");
      renderAt(`?report=${CONFIG_ID}`);
      // On the page and in the rail, whole: not "Try again shortly".
      expect(await screen.findAllByText(LICENCE)).toHaveLength(2);
      expect(screen.queryByText(/Try again/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "License settings" }));
      expect(await screen.findByText("License page")).toBeInTheDocument();
    });

    it("offers nothing to click when only the model settings can fix it", async () => {
      serveFailed(GATEWAY, "model_setup");
      renderAt(`?report=${CONFIG_ID}`);
      expect(await screen.findAllByText(GATEWAY)).toHaveLength(2);
      expect(screen.getAllByText("Failed before analysing")).toHaveLength(2);
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(
        screen.queryByRole("button", { name: "License settings" }),
      ).toBeNull();
    });

    it("reads a stopped run as stopped, by whom, with Run again", async () => {
      serveFailed("Stopped by Ada.", "stopped");
      renderAt(`?report=${CONFIG_ID}`);
      expect(await screen.findAllByText("Stopped by Ada.")).toHaveLength(2);
      expect(screen.getByText("Stopped")).toBeInTheDocument();
      expect(screen.getByText("Last run stopped")).toBeInTheDocument();
      expect(screen.queryByText("Failed before analysing")).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      fireEvent.click(screen.getAllByRole("button", { name: "Run again" })[0]);
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("counts reads as they come back, and after Stop gives the page and the rail the same counts at once", async () => {
      // 18 sessions: two calls of 8 had come back when Ada pressed Stop;
      // 11 of those 16 showed a failure, 4 had none and 1 couldn't be read.
      // The banner accounts for all 16 the progress counted.
      const run = (overrides) => ({
        ...runFixture.result,
        sample_size: 18,
        ...overrides,
      });
      const running = run({
        status: "running",
        stage: "summarising",
        progress: { stage: "summarising", done: 16, total: 18 },
      });
      const stopped = run({
        status: "failed",
        error: "Stopped by Ada.",
        error_code: "stopped",
        analysed_count: 11,
        excluded_count: 4,
        failed_count: 1,
        skipped_count: 0,
        progress: { ...running.progress, error_code: "stopped" },
      });
      let current = running;
      const base = mocks.readQuery.getMockImplementation();
      mocks.readQuery.mockImplementation((url, options) => {
        if (url === "/tracer/insights/configs/")
          return ok({
            configs: configsFixture.result.configs.map((config) =>
              config.id === CONFIG_ID
                ? { ...config, latest_run: current }
                : config,
            ),
          });
        if (url === `/tracer/insights/configs/${CONFIG_ID}/`)
          return ok({ ...configDetail, latest_run: current, runs: [current] });
        if (url === `/tracer/insights/runs/${RUN_ID}/`) return ok(current);
        return base(url, options);
      });
      mocks.post.mockImplementation((url) => {
        if (url !== `/tracer/insights/runs/${RUN_ID}/stop/`)
          return Promise.reject(new Error(`unexpected ${url}`));
        current = stopped;
        return ok({
          id: RUN_ID,
          status: "failed",
          error: "Stopped by Ada.",
          error_code: "stopped",
        });
      });
      renderAt(`?report=${CONFIG_ID}`);
      expect(
        await screen.findByText("Summarising each item: 16 of 18 sessions"),
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      fireEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", {
          name: "Stop run",
        }),
      );
      await waitFor(
        () =>
          expect(
            screen.getAllByText(
              "11 analysed (4 had no failure) · 1 couldn't be read · 0 skipped",
            ),
          ).toHaveLength(2),
        { timeout: 5000 },
      );
      expect(screen.getByText("Stopped")).toBeInTheDocument();
      expect(screen.getByText("Last run stopped")).toBeInTheDocument();
      expect(screen.queryByText("Stopping…")).toBeNull();
      expect(screen.queryByText(/0 analysed/)).toBeNull();
    });
  });

  it.each(
    ["new", "rerun", "same rows"].flatMap((mode) =>
      ["HTTP error", "unavailable check"].map((failure) => [mode, failure]),
    ),
  )(
    "holds a %s report on %s and offers a cost retry",
    async (mode, failure) => {
      let unavailable = true;
      mocks.post.mockImplementation((url) =>
        url === "/tracer/insights/estimate/"
          ? unavailable
            ? failure === "HTTP error"
              ? Promise.reject({ statusCode: 503 })
              : ok({
                  population: 108,
                  sample_size: 108,
                  credits_estimate: 5,
                  credit_limited: true,
                  credit_check_unavailable: true,
                  credit_message:
                    "Credits can't be checked right now, so this wasn't started. Please retry in a moment.",
                })
            : ok({ population: 108, sample_size: 108, credits_estimate: 5 })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      if (mode === "same rows") {
        const rows = [{ id: "s1" }];
        vi.stubGlobal("sessionStorage", {
          getItem: (key) =>
            key === `insights-population:${CONFIG_ID}`
              ? JSON.stringify(rows)
              : null,
        });
        const base = mocks.readQuery.getMockImplementation();
        mocks.readQuery.mockImplementation((url, options) =>
          url === `/tracer/insights/configs/${CONFIG_ID}/`
            ? ok({
                ...configDetail,
                scope: { preset: "fixed", label: "Handed over" },
              })
            : base(url, options),
        );
      }
      renderAt(mode === "new" ? "?new=1&lens=usage" : `?report=${CONFIG_ID}`);
      if (mode !== "new")
        fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
      const retry = await screen.findByRole("button", {
        name: "Retry cost check",
      });
      const run = screen.getByRole("button", {
        name: mode === "new" ? "Run report" : "Run again",
      });
      expect(run).toBeDisabled();
      expect(
        screen.queryByRole("link", { name: "Open billing" }),
      ).not.toBeInTheDocument();
      unavailable = false;
      fireEvent.click(retry);
      await screen.findAllByText(/about 5 credits/);
      await waitFor(() => expect(run).toBeEnabled());
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(0);
    },
  );

  it("shows Draft and Preview prices and waits for their credit checks", async () => {
    const prices = [];
    mocks.actionCredits.mockImplementation(
      (url, body) =>
        new Promise((resolve) => prices.push({ action: body.action, resolve })),
    );
    renderAt("?new=1&lens=usage");
    const draft = await screen.findByRole("button", { name: "Draft with AI" });
    const preview = screen.getByRole("button", {
      name: "Preview on 3 examples",
    });
    expect(draft).toBeDisabled();
    expect(preview).toBeDisabled();
    await waitFor(() => expect(prices).toHaveLength(2));
    prices.forEach(({ action, resolve }) =>
      resolve({
        data: {
          status: true,
          result: {
            credits_estimate: action === "draft" ? 2 : 3,
            credit_limited: action === "preview",
            credit_message: "No credits left for a preview.",
          },
        },
      }),
    );
    expect(
      await screen.findByText(/Draft: about 2 credits per action/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Preview: about 3 credits per action/),
    ).toBeInTheDocument();
    await waitFor(() => expect(draft).toBeEnabled());
    expect(preview).toBeDisabled();
    expect(
      screen.getByText(/No credits left for a preview\./),
    ).toBeInTheDocument();
  });

  it("prices suggested, typed, follow-up and retry questions before they can be asked", async () => {
    const store = new Map();
    vi.stubGlobal("sessionStorage", {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
    });
    let price;
    mocks.actionCredits.mockImplementation(
      () =>
        new Promise((resolve) => {
          price = resolve;
        }),
    );
    const base = mocks.post.getMockImplementation();
    mocks.post.mockImplementation((url, body) =>
      url.endsWith("/ask/")
        ? Promise.resolve({ data: askFixture })
        : base(url, body),
    );
    renderAt(`?report=${CONFIG_ID}`);
    const suggestion = await screen.findByRole("button", {
      name: "Which users are hit hardest?",
    });
    expect(suggestion).toBeDisabled();
    const input = screen.getByRole("textbox", {
      name: "Ask about this report",
    });
    fireEvent.change(input, { target: { value: "Why?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(postsTo(`/tracer/insights/runs/${RUN_ID}/ask/`)).toHaveLength(0);
    price({ data: { status: true, result: { credits_estimate: 4 } } });
    expect(
      await screen.findByText(
        /Each question, including suggestions and retries: about 4 credits/,
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(suggestion).toBeEnabled());
    fireEvent.click(suggestion);
    const followUp = await screen.findByRole("button", {
      name: "Which of these users came back later?",
    });
    // The completed action rechecks the remaining balance; follow-ups wait too.
    expect(followUp).toBeDisabled();
    price({
      data: {
        status: true,
        result: {
          credits_estimate: 4,
          credit_limited: true,
          credit_message: "No credits left for a question.",
        },
      },
    });
    expect(
      await screen.findByText(/No credits left for a question\./),
    ).toBeInTheDocument();
    expect(followUp).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it.each([
    ["draft", "Draft with AI", "?new=1&lens=usage"],
    ["preview", "Preview on 3 examples", "?new=1&lens=usage"],
    ["ask", "Which users are hit hardest?", `?report=${CONFIG_ID}`],
  ])(
    "retries an unavailable %s credit check without sending the paid action",
    async (action, buttonName, search) => {
      let unavailable = true;
      mocks.actionCredits.mockImplementation((url, body) =>
        ok({
          credits_estimate: 4,
          credit_limited: body.action === action && unavailable,
          credit_check_unavailable: body.action === action && unavailable,
          credit_message:
            "Credits can't be checked right now, so this wasn't started. Please retry in a moment.",
        }),
      );
      renderAt(search);
      const retry = await screen.findByRole("button", {
        name: "Retry cost check",
      });
      const button = screen.getByRole("button", { name: buttonName });
      expect(button).toBeDisabled();
      expect(
        screen.queryByRole("link", { name: "Open billing" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(/Please retry in a moment\./),
      ).toBeInTheDocument();
      fireEvent.click(button);
      expect(postsTo("/tracer/insights/draft/")).toHaveLength(0);
      expect(postsTo("/tracer/insights/preview/")).toHaveLength(0);
      expect(postsTo(`/tracer/insights/runs/${RUN_ID}/ask/`)).toHaveLength(0);
      unavailable = false;
      fireEvent.click(retry);
      await waitFor(() => expect(button).toBeEnabled());
      expect(
        screen.queryByRole("button", { name: "Retry cost check" }),
      ).not.toBeInTheDocument();
      expect(
        mocks.actionCredits.mock.calls.filter(
          ([, body]) => body.action === action,
        ),
      ).toHaveLength(2);
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(0);
    },
  );

  it("checks the displayed cost again before retrying a failed question", async () => {
    vi.stubGlobal("sessionStorage", memoryStorage());
    const prices = [];
    mocks.actionCredits.mockImplementation(
      () => new Promise((resolve) => prices.push(resolve)),
    );
    mocks.post.mockRejectedValue({ statusCode: 503 });
    renderAt(`?report=${CONFIG_ID}`);
    const question = await screen.findByRole("button", {
      name: "Which users are hit hardest?",
    });
    prices[0]({ data: { status: true, result: { credits_estimate: 4 } } });
    await waitFor(() => expect(question).toBeEnabled());
    fireEvent.click(question);
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(retry).toBeDisabled();
    await waitFor(() => expect(prices).toHaveLength(2));
    prices[1]({ data: { status: true, result: { credits_estimate: 5 } } });
    await screen.findByText(
      /Each question, including suggestions and retries: about 5 credits/,
    );
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);
    await waitFor(() =>
      expect(postsTo(`/tracer/insights/runs/${RUN_ID}/ask/`)).toHaveLength(2),
    );
    expect(postsTo(`/tracer/insights/runs/${RUN_ID}/ask/`)[1][1].question).toBe(
      "Which users are hit hardest?",
    );
  });

  it("lists saved reports with their distribution, and the templates", async () => {
    renderAt("");
    expect(
      await screen.findByText("sessions/failures test"),
    ).toBeInTheDocument();
    expect(screen.getByText("users/personas test")).toBeInTheDocument();
    expect(
      screen.getAllByText("Shoppers, Account Holders, and Escalators").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Start from a template")).toBeInTheDocument();
    expect(screen.getByText("Who are our users?")).toBeInTheDocument();
  });

  it("starts with the reports rail closed on a narrow screen", async () => {
    setViewport(1280);
    renderAt(`?report=${CONFIG_ID}`);
    expect(
      await screen.findByRole("heading", {
        name: "Where does the agent fall short?",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("users/personas test")).not.toBeInTheDocument();
  });

  it("keeps the viewer's choice to open the rail on a narrow screen", async () => {
    setViewport(1280);
    globalThis.localStorage.setItem("insights-rail-collapsed", "0");
    renderAt("");
    expect(
      await screen.findByText("sessions/failures test"),
    ).toBeInTheDocument();
  });

  it("opens the most recent report when none is chosen", async () => {
    renderAt("");
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        // Insights keeps the Observe page's tab, so the tab bar stays on it.
        `?report=${configsFixture.result.configs[0].id}&tab=insights`,
      ),
    );
  });

  it("opens the next report, not the deleted one, while the list refetches", async () => {
    const [newest, next] = configsFixture.result.configs;
    let deleted = false;
    const axios = (await import("src/utils/axios")).default;
    axios.delete.mockImplementation(async () => {
      deleted = true;
      return { data: { status: true, result: newest } };
    });
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) => {
      // The refetch after the delete is still in flight.
      if (deleted && url === "/tracer/insights/configs/")
        return new Promise(() => {});
      if (deleted && url === `/tracer/insights/configs/${newest.id}/`)
        return Promise.reject({ statusCode: 404, message: "Not found" });
      return base(url, options);
    });
    renderAt("");
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(newest.id),
    );
    fireEvent.click(
      screen.getByRole("button", { name: `Actions for ${newest.name}` }),
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(next.id),
    );
    expect(
      screen.queryByText(/This report could not be loaded/),
    ).not.toBeInTheDocument();
  });

  // The report's own menu deletes too; the list the auto-open reads must
  // lose the report there as well.
  it.each([
    [
      "opens the next report",
      [sessionsConfig, configsFixture.result.configs[0]],
    ],
    ["shows the empty state for the only report", [sessionsConfig]],
  ])("%s after a delete from the report's menu", async (_, listed) => {
    let deleted = false;
    const axios = (await import("src/utils/axios")).default;
    axios.delete.mockImplementation(async () => {
      deleted = true;
      return { data: { status: true, result: sessionsConfig } };
    });
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) => {
      if (url === "/tracer/insights/configs/")
        return deleted
          ? new Promise(() => {}) // the refetch is still in flight
          : ok({ ...configsFixture.result, configs: listed });
      if (deleted && url === `/tracer/insights/configs/${CONFIG_ID}/`)
        return Promise.reject({ statusCode: 404, message: "Not found" });
      return base(url, options);
    });
    renderAt("");
    // The list, the auto-open, then the report: slow on a loaded runner.
    fireEvent.click(
      await screen.findByRole(
        "button",
        { name: "More report actions" },
        { timeout: 5000 },
      ),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Delete report" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    if (listed.length > 1)
      await waitFor(() =>
        expect(screen.getByTestId("location").textContent).toContain(
          listed[1].id,
        ),
      );
    else
      expect(
        await screen.findByText(
          "What do you want to learn about your traffic?",
        ),
      ).toBeInTheDocument();
    expect(screen.getByTestId("location").textContent).not.toContain(CONFIG_ID);
    expect(
      screen.queryByText(/This report could not be loaded/),
    ).not.toBeInTheDocument();
  });

  it("shows why the reports didn't load in the page, with the rail closed", async () => {
    setViewport(1280);
    const base = mocks.readQuery.getMockImplementation();
    let failing = true;
    mocks.readQuery.mockImplementation((url, options) =>
      failing && url === "/tracer/insights/configs/"
        ? Promise.reject({ statusCode: 500, message: "boom" })
        : base(url, options),
    );
    renderAt("");
    const main = await screen.findByRole("main");
    expect(await within(main).findByRole("alert")).toBeInTheDocument();
    failing = false;
    fireEvent.click(within(main).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(
        configsFixture.result.configs[0].id,
      ),
    );
  });

  it("renders a legacy run: key points as findings, meta line, categories, highlights", async () => {
    renderAt(`?report=${CONFIG_ID}`);
    expect(
      await screen.findByRole("heading", {
        name: "Where does the agent fall short?",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "108 sessions sampled of 108 · 72 analysed (34 had no failure) · 39 users · Oct 9–15",
      ),
    ).toBeInTheDocument();
    // The first sentence of a key point is its headline.
    expect(
      screen.getByText(
        /^Unresponsive support is the most damaging failure: 26 conversations/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/5 findings checked against the report's statistics/),
    ).toBeInTheDocument();
    // Citations are chips, not "#3" in the prose.
    expect(
      screen.getAllByRole("button", { name: "Open example 3" }).length,
    ).toBeGreaterThan(0);
    // Categories table with a notable difference in plain words.
    const table = screen.getByRole("table");
    expect(
      within(table).getByText("Unverified Claims and Fabricated Facts"),
    ).toBeInTheDocument();
    expect(
      within(table).getAllByText("Sentiment 4.0 vs 2.0").length,
    ).toBeGreaterThan(0);
    expect(
      within(table).getByText("All sessions analysed: 72"),
    ).toBeInTheDocument();
    // No previous run: no Change column and no change cards.
    expect(within(table).queryByText("Change")).toBeNull();
    expect(screen.queryByText("What changed since the last run")).toBeNull();
    // Highlights with their reason.
    expect(
      screen.getByRole("button", { name: "Hide highlighted examples" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Refund of \$129 issued outside the 30-day window/),
    ).toBeInTheDocument();
    // The trend falls back to a single bar for one run.
    expect(screen.getByText(/One run so far/)).toBeInTheDocument();
  });

  it("renders the engine's findings, changes and highlight quotes", async () => {
    mocks.enriched = true;
    renderAt(`?report=${CONFIG_ID}`);
    expect(
      await screen.findByText(
        "Unresolved 100% across 26 conversations (36%): the agent loops instead of escalating",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Add a handoff after two failed lookups or one explicit request for a human.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "High severity" })).toHaveLength(
      2,
    );
    expect(
      screen.getByText(
        /5 findings checked against the report's statistics and the cited examples · 1 dropped/,
      ),
    ).toBeInTheDocument();
    // What changed: a new category and a significant move.
    expect(
      screen.getByText("What changed since the last run"),
    ).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("5% → 14%")).toBeInTheDocument();
    // Highlight cards use the title and quote.
    expect(
      screen.getByText(
        "Asked for a human four times and was never offered one",
      ),
    ).toBeInTheDocument();
    // The Change column shows the delta in points.
    const table = screen.getByRole("table");
    expect(within(table).getByText("Change")).toBeInTheDocument();
    expect(within(table).getAllByText("+6 pts").length).toBeGreaterThan(0);
  });

  it("renders a real Omega run: verifier footer, quotes and root causes", async () => {
    mocks.omega = true;
    renderAt(`?report=${CONFIG_ID}`);
    expect(
      await screen.findByText(
        "Unresolved 100% in 10 conversations (14%): the agent re-asks for an order number given in the first message",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/6 findings checked by an independent verifier/),
    ).toBeInTheDocument();
    // Highlight cards carry the engine's quote.
    expect(
      screen.getAllByText("“I literally just gave it to you”").length,
    ).toBeGreaterThan(0);
    // The root cause of a subcategory shows in its explorer.
    const table = screen.getByRole("table");
    fireEvent.click(
      within(table).getByRole("button", {
        name: /Show subcategories of Agent disregards/,
      }),
    );
    fireEvent.click(
      within(table).getByText("Agent re-asks for order number already given"),
    );
    expect(await screen.findByText("Root cause")).toBeInTheDocument();
    expect(screen.getByText("Fix")).toBeInTheDocument();
  });

  it("folds a finding's fifth and later citations into +N", async () => {
    mocks.omega = true;
    renderAt(`?report=${CONFIG_ID}`);
    // Finding 5 of the Omega run cites #16–#20.
    const more = await screen.findByRole("button", {
      name: "Show 1 more example",
    });
    expect(more).toHaveTextContent("+1");
    expect(
      screen.queryByRole("button", { name: "Open example 20" }),
    ).toBeNull();
    // Clicked, it gives way to the folded ref in the same line.
    const line = more.closest("p");
    fireEvent.click(more, { detail: 1 });
    expect(
      within(line).getByRole("button", { name: "Open example 20" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /more example/ })).toBeNull();
  });

  it("previews a citation on hover with its title and quote", async () => {
    mocks.enriched = true;
    renderAt(`?report=${CONFIG_ID}`);
    const [chip] = await screen.findAllByRole("button", {
      name: "Open example 11",
    });
    fireEvent.mouseEnter(chip);
    const card = await waitFor(() => {
      const found = screen
        .getAllByRole("tooltip")
        .find((el) => el.textContent.includes("Click to open the example"));
      expect(found).toBeTruthy();
      return found;
    });
    expect(
      within(card).getByText(
        "Asked for a human four times and was never offered one",
      ),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(/no\. a human\. agent\. representative/),
    ).toBeInTheDocument();
  });

  it("expands a category into its subcategories", async () => {
    renderAt(`?report=${CONFIG_ID}`);
    const toggle = await screen.findByRole("button", {
      name: "Show subcategories of Unresponsive Support and No Escalation",
    });
    const table = screen.getByRole("table");
    expect(
      within(table).queryByText("Ignored Explicit Requests for Human Agent"),
    ).toBeNull();
    fireEvent.click(toggle);
    expect(
      within(table).getByText("Ignored Explicit Requests for Human Agent"),
    ).toBeInTheDocument();
  });

  it("switches columns from the Columns menu and keeps the choice", async () => {
    const store = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
    });
    try {
      renderAt(`?report=${CONFIG_ID}`);
      fireEvent.click(await screen.findByRole("button", { name: "Columns" }));
      fireEvent.click(
        screen.getByRole("menuitemcheckbox", { name: /Unresolved %/ }),
      );
      const table = screen.getByRole("table", { hidden: true });
      expect(within(table).getByText("Unresolved %")).toBeInTheDocument();
      expect(JSON.parse(store.get(`insights-columns:${CONFIG_ID}`))).toEqual({
        on: ["unresolved"],
        off: [],
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("opens the cited example from a citation chip", async () => {
    renderAt(`?report=${CONFIG_ID}`);
    const [chip] = await screen.findAllByRole("button", {
      name: "Open example 11",
    });
    fireEvent.click(chip);
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(
        "example=abf42ede-1742-5a77-acf0-6023fe8c2d61",
      ),
    );
    expect(mocks.readQuery).toHaveBeenCalledWith(
      `/tracer/insights/runs/${RUN_ID}/units/`,
      expect.objectContaining({
        params: expect.objectContaining({
          unit_ids: "abf42ede-1742-5a77-acf0-6023fe8c2d61",
          include_content: true,
        }),
      }),
    );
    expect(
      await screen.findByRole("button", { name: "Open session's traces" }),
    ).toBeInTheDocument();
  });

  it("opens an example's user on the days the report read", async () => {
    // The Sessions list there opens on Today unless told otherwise.
    const window = ["2026-10-08T00:00:00+00:00", "2026-10-15T00:00:00+00:00"];
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) =>
      url === `/tracer/insights/runs/${RUN_ID}/`
        ? ok({ ...runFixture.result, scope: { label: "Custom", window } })
        : base(url, options),
    );
    renderAt(
      `?report=${CONFIG_ID}&example=abf42ede-1742-5a77-acf0-6023fe8c2d61`,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open user" }));
    const url = new URL(
      (await screen.findByTestId("user-page")).textContent,
      "https://app.test",
    );
    expect(url.pathname).toBe("/dashboard/users/cust_omar_702");
    expect(url.searchParams.get("userTab")).toBe("sessions");
    const dates = JSON.parse(url.searchParams.get("sessionDateFilter"));
    expect(dates.dateOption).toBe("Custom");
    expect(dates.dateFilter.map((value) => new Date(value).getTime())).toEqual(
      window.map((value) => new Date(value).getTime()),
    );
  });

  it("opens a users report's user from its category on the days it read", async () => {
    mocks.enriched = true;
    const window = ["2026-10-08T00:00:00+00:00", "2026-10-15T00:00:00+00:00"];
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) =>
      url === `/tracer/insights/runs/${RUN_ID}/`
        ? ok({
            ...enrichedRunFixture.result,
            unit: "users",
            scope: { label: "Custom", window },
          })
        : base(url, options),
    );
    renderAt(`?report=${CONFIG_ID}&category=a55e9078defc`);
    fireEvent.click(await screen.findByRole("button", { name: "Users" }));
    const table = await screen.findByRole("table");
    fireEvent.click(within(table).getByText("cust_omar_702").closest("tr"));
    const url = new URL(
      (await screen.findByTestId("user-page")).textContent,
      "https://app.test",
    );
    expect(url.pathname).toBe("/dashboard/users/cust_omar_702");
    // The Sessions list there opens on Today unless told otherwise.
    expect(url.searchParams.get("userTab")).toBe("sessions");
    const dates = JSON.parse(url.searchParams.get("sessionDateFilter"));
    expect(dates.dateOption).toBe("Custom");
    expect(dates.dateFilter.map((value) => new Date(value).getTime())).toEqual(
      window.map((value) => new Date(value).getTime()),
    );
  });

  it("marks the evidence inside the example's content", async () => {
    mocks.enriched = true;
    const unitId = "5b2ca31b-3453-52f3-a646-a8801b377c16";
    renderAt(`?report=${CONFIG_ID}&example=${unitId}`);
    const marks = await waitFor(() => {
      const found = document.querySelectorAll("mark");
      expect(found.length).toBe(3);
      return found;
    });
    expect([...marks].map((m) => m.textContent)).toEqual([
      "Could you share your order number?",
      "I just gave it to you. HP-72336.",
      "Is there anything else I can help you with?",
    ]);
    expect(marks[1]).toHaveAttribute(
      "title",
      "The customer had given it in the first message",
    );
  });

  it("says once that a deleted project's reports are gone, with no retries", async () => {
    const missing = (message) =>
      Promise.reject({
        status: false,
        code: "not_found",
        message,
        result: message,
        statusCode: 404,
      });
    mocks.readQuery.mockImplementation((url) =>
      url === "/tracer/insights/configs/"
        ? missing("Project not found")
        : missing("Report not found"),
    );
    renderAt(`?report=${CONFIG_ID}`);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "This project can't be found. It may have been deleted, and its reports with it.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(/not found/i)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(
      within(alert).getByRole("link", { name: "Open projects" }),
    ).toHaveAttribute("href", "/dashboard/observe");
  });

  it("names the days a custom range read in the report header", async () => {
    const base = mocks.readQuery.getMockImplementation();
    const year = new Date().getFullYear();
    const scope = {
      label: "Custom range · 1 filter",
      preset: "Custom",
      window: [
        new Date(year, 9, 22).toISOString(),
        new Date(year, 9, 23, 23, 59, 59, 999).toISOString(),
      ],
    };
    mocks.readQuery.mockImplementation((url, options) =>
      url === `/tracer/insights/runs/${RUN_ID}/`
        ? ok({ ...runFixture.result, scope })
        : base(url, options),
    );
    renderAt(`?report=${CONFIG_ID}&run=${RUN_ID}`);
    const shown = await screen.findAllByText("Oct 22–23 · 1 filter");
    expect(shown.some((label) => label.closest(".MuiChip-root"))).toBe(true);
    expect(screen.queryByText(/Custom range/)).toBeNull();
  });

  it("falls back to the unit alone when its content cannot be read", async () => {
    const unitId = "5b2ca31b-3453-52f3-a646-a8801b377c16";
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options = {}) =>
      options.params?.include_content
        ? Promise.reject(new Error("contract"))
        : base(url, options),
    );
    renderAt(`?report=${CONFIG_ID}&example=${unitId}`);
    expect(
      await screen.findByRole(
        "button",
        { name: "Open session's traces" },
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("First message")).toBeInTheDocument();
  });

  it("opens the category explorer with the tree, root cause and summaries", async () => {
    mocks.enriched = true;
    renderAt(`?report=${CONFIG_ID}`);
    const table = await screen.findByRole("table");
    fireEvent.click(
      within(table).getByText("Unresponsive Support and No Escalation"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(
        "category=3ffe0720fc9b",
      ),
    );
    expect(
      await screen.findByRole("button", { name: "Measure with Jev" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tree")).toBeInTheDocument();
    expect(screen.getByText("Root cause")).toBeInTheDocument();
    expect(
      screen.getByText(/The order lookup fails silently during outages/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/Coastline Rain Shell's material/),
    ).toBeInTheDocument();
    // Back returns to the report.
    fireEvent.click(
      screen.getAllByRole("button", { name: "Back to report" })[0],
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).not.toContain(
        "category=",
      ),
    );
    // The browser's Back goes to the category again, not the same report.
    fireEvent.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(
        "category=3ffe0720fc9b",
      ),
    );
  });

  it("asks about the report and cites the answer", async () => {
    mocks.enriched = true;
    const store = new Map();
    vi.stubGlobal("sessionStorage", {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
    });
    const answers = [];
    mocks.post.mockImplementation((url) =>
      url === `/tracer/insights/runs/${RUN_ID}/ask/`
        ? new Promise((resolve) => answers.push(resolve))
        : Promise.reject(new Error(`unexpected ${url}`)),
    );
    try {
      renderAt(`?report=${CONFIG_ID}`);
      const question = await screen.findByRole("button", {
        name: "Which users are hit hardest?",
      });
      await waitFor(() => expect(question).toBeEnabled());
      fireEvent.click(question);
      // While it answers: what it reads, and the composer stays usable.
      expect(
        await screen.findByText("Reading 72 conversations…"),
      ).toBeInTheDocument();
      const input = screen.getByRole("textbox", {
        name: "Ask about this report",
      });
      fireEvent.change(input, { target: { value: "Who came back?" } });
      expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Ask" }));
      await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
      expect(screen.getAllByText("Reading 72 conversations…")).toHaveLength(2);
      answers.forEach((resolve) => resolve({ data: askFixture }));
      expect(
        await screen.findAllByText(
          /Customers who ask for a human are hit hardest/,
        ),
      ).toHaveLength(2);
      const [url, body] = mocks.post.mock.calls[0];
      expect(url).toBe(`/tracer/insights/runs/${RUN_ID}/ask/`);
      expect(body).toEqual({
        question: "Which users are hit hardest?",
        history: [],
      });
      // The cited unit keeps its report number.
      expect(
        (await screen.findAllByRole("button", { name: "Open example 11" }))
          .length,
      ).toBeGreaterThan(1);
      expect(
        screen.getAllByRole("button", {
          name: "Which of these users came back later?",
        }).length,
      ).toBe(2);
      expect(store.get(`insights-ask:${RUN_ID}`)).toContain(
        "Which users are hit hardest?",
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  describe("a question still being answered", () => {
    let store;
    beforeEach(() => {
      mocks.enriched = true;
      store = new Map();
      vi.stubGlobal("sessionStorage", {
        getItem: (key) => store.get(key) ?? null,
        setItem: (key, value) => store.set(key, value),
        removeItem: (key) => store.delete(key),
      });
    });

    it("keeps going while a category is open, and its answer is there after", async () => {
      let answer;
      mocks.post.mockImplementation((url) =>
        url === `/tracer/insights/runs/${RUN_ID}/ask/`
          ? new Promise((resolve) => {
              answer = resolve;
            })
          : Promise.reject(new Error(`unexpected ${url}`)),
      );
      renderAt(`?report=${CONFIG_ID}`);
      const question = await screen.findByRole("button", {
        name: "Which users are hit hardest?",
      });
      await waitFor(() => expect(question).toBeEnabled());
      fireEvent.click(question);
      await screen.findByText("Reading 72 conversations…");
      const table = await screen.findByRole("table");
      fireEvent.click(
        within(table).getByText("Unresponsive Support and No Escalation"),
      );
      await screen.findByRole("button", { name: "Measure with Jev" });
      // The panel is gone; the question isn't aborted, and lands meanwhile.
      expect(
        screen.queryByRole("textbox", { name: "Ask about this report" }),
      ).toBeNull();
      answer({ data: askFixture });
      await waitFor(() =>
        expect(store.get(`insights-ask:${RUN_ID}`)).toContain(
          "Customers who ask for a human are hit hardest",
        ),
      );
      fireEvent.click(
        screen.getAllByRole("button", { name: "Back to report" })[0],
      );
      fireEvent.click(await screen.findByRole("button", { name: "1 answer" }));
      expect(
        (
          await screen.findAllByText(
            /Customers who ask for a human are hit hardest/,
          )
        ).length,
      ).toBeGreaterThan(0);
      expect(mocks.post).toHaveBeenCalledTimes(1);
    });

    it("reads as interrupted after a reload, with Retry", async () => {
      // Left busy a moment ago by a page since reloaded: nothing answers it.
      store.set(
        `insights-ask:${RUN_ID}`,
        JSON.stringify([
          {
            id: "t1",
            question: "Who came back?",
            status: "busy",
            startedAt: Date.now() - 5000,
          },
        ]),
      );
      renderAt(`?report=${CONFIG_ID}`);
      fireEvent.click(
        await screen.findByRole(
          "button",
          { name: "1 answer" },
          { timeout: 5000 },
        ),
      );
      expect(
        await screen.findByText(/This question was interrupted/),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    });
  });

  it("guides a new report and starts it on the scope the server reads", async () => {
    renderAt("?new=1&lens=failures");
    expect(
      await screen.findByRole("radio", { name: /Failures/ }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText(/Question/)).toHaveValue(
      "Where is the agent failing?",
    );
    expect(
      (await screen.findAllByText(/Analysing all 108 sessions/)).length,
    ).toBeGreaterThan(0);
    // Counted on the server with the scope's window.
    const [[, counted]] = postsTo("/tracer/insights/estimate/");
    expect(counted).toEqual({
      project_id: PROJECT,
      unit: "sessions",
      scope: { filters: [windowItem(presetWindow("7D"))] },
      sample_size: 300,
    });
    fireEvent.click(screen.getByRole("button", { name: "Run report" }));
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    const [[, body]] = postsTo("/tracer/insights/runs/");
    expect(body.config).toMatchObject({
      unit: "sessions",
      lens: "failures",
      question: "Where is the agent failing?",
      sample_size: 300,
    });
    expect(body.scope).toEqual({
      filters: [windowItem(presetWindow("7D"))],
      label: "Past 7D",
      preset: "7D",
      window: presetWindow("7D"),
    });
    // The server reads the rows: none collected in the browser, no cap.
    expect(body).not.toHaveProperty("population");
    expect(mocks.collect).not.toHaveBeenCalledWith(
      expect.objectContaining({ limit: 2000 }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `?report=${CONFIG_ID}&run=${RUN_ID}&tab=insights`,
      ),
    );
  });

  it("runs a handed-over population as a fixed scope", async () => {
    const key = "seedkey1";
    sessionStorage.setItem(
      `observe-handoff:${key}`,
      JSON.stringify({
        unit: "sessions",
        population: population.slice(0, 12),
        question: "Why did these sessions get “No”?",
        label: "“No” answers · Was it resolved?",
        source: "Instant eval: Was it resolved?",
      }),
    );
    renderAt(`?new=1&seed=${key}`);
    expect(
      await screen.findByRole("radio", { name: /Your question/ }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("12 sessions")).toBeInTheDocument();
    // Run waits for the rows' cost.
    const run = screen.getByRole("button", { name: "Run report" });
    await waitFor(() => expect(run).toBeEnabled());
    fireEvent.click(run);
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    // The rows are priced by their number, never counted from a scope.
    const bodies = postsTo("/tracer/insights/estimate/").map(
      ([, sent]) => sent,
    );
    expect(bodies.length).toBeGreaterThan(0);
    bodies.forEach((sent) => {
      expect(sent).not.toHaveProperty("scope");
      expect(sent.population_size).toBe(12);
    });
    const [[, body]] = postsTo("/tracer/insights/runs/");
    expect(body.scope).toEqual({
      label: "“No” answers · Was it resolved?",
      preset: "fixed",
    });
    expect(body.population).toHaveLength(12);
    expect(mocks.collect).not.toHaveBeenCalledWith(
      expect.objectContaining({ limit: 2000 }),
    );
  });

  it("opens a Users hand-over on the view's date, filters and search", async () => {
    // Users checked one by one are counted a few seconds at a time.
    const counts = [
      { population: 4209, population_is_lower_bound: true },
      { population: 12450, population_is_lower_bound: false },
    ];
    mocks.post.mockImplementation((url) =>
      url === "/tracer/insights/estimate/"
        ? ok({
            ...(counts.shift() || counts.at(-1) || {}),
            sample_ids: ["u1", "u2", "u3"],
          })
        : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
    );
    const COST = {
      column_id: "total_cost",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 1,
        col_type: "SYSTEM_METRIC",
      },
    };
    const key = "seedusers";
    sessionStorage.setItem(
      `observe-handoff:${key}`,
      JSON.stringify(
        viewSeed({
          unit: "users",
          filters: [windowItem(presetWindow("30D")), COST],
          dateFilter: { dateOption: "30D" },
          search: "ada",
        }),
      ),
    );
    renderAt(`?new=1&seed=${key}`);
    expect(
      await screen.findByRole("button", { name: "Past 30D" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Total Cost ($)")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search users" })).toHaveValue(
      "ada",
    );
    // More than a browser read could collect: the server counts them all,
    // climbing until it is exact.
    expect(await screen.findByText("4,209+")).toBeInTheDocument();
    expect(
      await screen.findByText("12,450", {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    // The draft and preview read the server's picks, not a list page.
    expect(mocks.collect).not.toHaveBeenCalledWith(
      expect.objectContaining({ unit: "users" }),
    );
    const window = presetWindow("30D");
    expect(postsTo("/tracer/insights/estimate/").at(-1)[1]).toEqual({
      project_id: PROJECT,
      unit: "users",
      scope: { filters: [COST, windowItem(window)], search: "ada" },
      sample_size: 300,
    });

    fireEvent.click(screen.getByRole("button", { name: "Run report" }));
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    const [[, body]] = postsTo("/tracer/insights/runs/");
    expect(body.config.unit).toBe("users");
    expect(body.scope).toEqual({
      filters: [COST, windowItem(window)],
      label: "Past 30D · 1 filter · “ada”",
      preset: "30D",
      window,
      search: "ada",
    });
    expect(body).not.toHaveProperty("population");
  });

  // Drives the date drawer, two pickers and the filter bar end to end: well
  // past the default timeout on a busy runner.
  it("edits a saved report's date and filters for its next run", async () => {
    mocks.patch.mockImplementation((url, body) =>
      ok({ ...sessionsConfig, ...body }),
    );
    renderAt(`?report=${CONFIG_ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("presentation");
    // A report saved with "All" reads all time until a date is chosen.
    fireEvent.click(within(drawer).getByRole("button", { name: "All time" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Past 30D" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "Filter" }));
    fireEvent.click(screen.getByRole("button", { name: "Pick cost over 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalled());
    const [url, body] = mocks.patch.mock.calls[0];
    expect(url).toBe(`/tracer/insights/configs/${CONFIG_ID}/`);
    expect(body.scope).toMatchObject({
      label: "Past 30D · 1 filter",
      preset: "30D",
      window: presetWindow("30D"),
    });
    expect(body.scope.filters).toEqual([
      expect.objectContaining({ column_id: "total_cost" }),
      windowItem(presetWindow("30D")),
    ]);
  }, 60000);

  it("asks for a name instead of saving a blank one", async () => {
    renderAt(`?report=${CONFIG_ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("presentation");
    fireEvent.change(within(drawer).getByLabelText("Name"), {
      target: { value: "   " },
    });
    expect(within(drawer).getByText("Add a name")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("runs a saved preset report again over a window ending now", async () => {
    const OLD = ["2026-09-01T00:00:00.000Z", "2026-09-08T00:00:00.000Z"];
    const saved = {
      ...configDetail,
      scope: {
        filters: [windowItem(OLD)],
        label: "Past 7D",
        preset: "7D",
        window: OLD,
      },
    };
    mocks.readQuery.mockImplementation((url) => {
      if (url === "/tracer/insights/configs/")
        return ok({
          ...configsFixture.result,
          configs: configsFixture.result.configs.map((config) =>
            config.id === CONFIG_ID
              ? { ...config, scope: saved.scope }
              : config,
          ),
        });
      if (url === `/tracer/insights/configs/${CONFIG_ID}/`) return ok(saved);
      if (url === `/tracer/insights/runs/${RUN_ID}/`)
        return ok(runFixture.result);
      if (url === `/tracer/insights/runs/${RUN_ID}/units/`)
        return ok({ units: [], total: 0 });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderAt(`?report=${CONFIG_ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Past 7D" }),
    ).toBeInTheDocument();
    // Beside the estimate the row mustn't squeeze the field into "Sample siz".
    const sampleField = within(dialog)
      .getByRole("combobox", { name: "Sample size" })
      .closest(".MuiTextField-root");
    expect(getComputedStyle(sampleField).flexShrink).toBe("0");
    const week = presetWindow("7D");
    await waitFor(() =>
      expect(postsTo("/tracer/insights/estimate/").at(-1)?.[1].scope).toEqual({
        filters: [windowItem(week)],
      }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Run again" }));
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    const [[, body]] = postsTo("/tracer/insights/runs/");
    expect(body).toMatchObject({
      config_id: CONFIG_ID,
      scope: { preset: "7D", window: week, filters: [windowItem(week)] },
    });
    expect(body).not.toHaveProperty("population");
  });

  it("runs a handed-over report again on the same rows", async () => {
    const FIXED = { preset: "fixed", label: "3 sessions from Instant eval" };
    const rows = [
      { id: "s1", time: "2026-10-10T00:00:00Z", user_id: "u1" },
      { id: "s2", time: "2026-10-10T01:00:00Z", user_id: "u2" },
      { id: "s3", time: "2026-10-10T02:00:00Z", user_id: "" },
    ];
    const store = new Map([
      [`insights-population:${CONFIG_ID}`, JSON.stringify(rows)],
    ]);
    vi.stubGlobal("sessionStorage", {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
      removeItem: (key) => store.delete(key),
    });
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) =>
      url === `/tracer/insights/configs/${CONFIG_ID}/`
        ? ok({ ...configDetail, scope: FIXED })
        : base(url, options),
    );
    renderAt(`?report=${CONFIG_ID}`);
    fireEvent.click(
      await screen.findByRole("button", { name: "Run now" }, { timeout: 5000 }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByLabelText("The same 3 sessions as last run"),
    ).toBeChecked();
    // Run again waits for the rows' cost.
    const runAgain = within(dialog).getByRole("button", { name: "Run again" });
    await waitFor(() => expect(runAgain).toBeEnabled());
    fireEvent.click(runAgain);
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    const [[, body]] = postsTo("/tracer/insights/runs/");
    expect(body).toMatchObject({ config_id: CONFIG_ID, scope: FIXED });
    expect(body.population.map((row) => row.id)).toEqual(["s1", "s2", "s3"]);
    // The rows are named, not counted from filters: only priced.
    const bodies = postsTo("/tracer/insights/estimate/").map(
      ([, sent]) => sent,
    );
    expect(bodies.length).toBeGreaterThan(0);
    bodies.forEach((sent) => {
      expect(sent).not.toHaveProperty("scope");
      expect(sent.population_size).toBe(3);
    });
  });

  it("rediscovers categories only with a run the server starts", async () => {
    let refuse = true;
    mocks.post.mockImplementation((url) => {
      if (url === "/tracer/insights/estimate/")
        return ok({
          population: 108,
          population_is_lower_bound: false,
          sample_size: 108,
        });
      if (url === "/tracer/insights/runs/" && refuse)
        return Promise.reject({
          statusCode: 429,
          message: "Too many reports this hour.",
        });
      return ok({ run_id: RUN_ID, config_id: CONFIG_ID });
    });
    renderAt(`?report=${CONFIG_ID}`);
    fireEvent.click(
      await screen.findByRole("button", { name: "Run now" }, { timeout: 5000 }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Rediscover categories"));
    const runAgain = within(dialog).getByRole("button", { name: "Run again" });
    await waitFor(() => expect(runAgain).toBeEnabled());
    fireEvent.click(runAgain);
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(1),
    );
    // The reset rides with the refused run: nothing erased the categories.
    expect(postsTo("/tracer/insights/runs/")[0][1]).toMatchObject({
      config_id: CONFIG_ID,
      reset_taxonomy: true,
    });
    expect(mocks.patch).not.toHaveBeenCalled();
    refuse = false;
    fireEvent.click(runAgain);
    await waitFor(() =>
      expect(postsTo("/tracer/insights/runs/")).toHaveLength(2),
    );
    expect(postsTo("/tracer/insights/runs/")[1][1].reset_taxonomy).toBe(true);
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("measures a category over the rows the report read", async () => {
    mocks.enriched = true;
    sessionStorage.clear();
    const OLD = ["2026-09-01T00:00:00.000Z", "2026-09-08T00:00:00.000Z"];
    const STATUS = {
      column_id: "status",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "ERROR",
      },
    };
    const base = mocks.readQuery.getMockImplementation();
    mocks.readQuery.mockImplementation((url, options) =>
      url === `/tracer/insights/runs/${RUN_ID}/`
        ? ok({
            ...enrichedRunFixture.result,
            scope: {
              filters: [STATUS, windowItem(OLD)],
              label: "Past 7D · 1 filter",
              preset: "7D",
              window: OLD,
            },
          })
        : base(url, options),
    );
    renderAt(`?report=${CONFIG_ID}`);
    const table = await screen.findByRole("table");
    fireEvent.click(
      within(table).getByText("Unresponsive Support and No Escalation"),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Measure with Jev" }),
    );
    const key = Object.keys(sessionStorage).find((k) =>
      k.startsWith("observe-handoff:"),
    );
    const seed = JSON.parse(sessionStorage.getItem(key));
    const week = presetWindow("7D");
    expect(seed).toMatchObject({
      unit: "sessions",
      filters: [STATUS, windowItem(week)],
      dateOption: "7D",
      window: week,
      label: "Past 7D · 1 filter",
      source: "the report “sessions/failures test”",
    });
    expect(seed.questions[0].question).toMatch(
      /^Is this an example of “Unresponsive Support and No Escalation”\?/,
    );
  });

  // Three or fewer themes make each its own top with a same-named sub.
  describe("a sub named like its only top", () => {
    const UNITS_URL = `/tracer/insights/runs/${RUN_ID}/units/`;
    const stats = runFixture.result.result.categories[0].stats;
    const category = (id, name, parentId, children) => ({
      id,
      name,
      level: parentId ? 2 : 1,
      parent_id: parentId,
      description: `${name.trim()} sessions`,
      status: "active",
      notable: [],
      stats: { ...stats, count: 5, share: 0.5 },
      ...(parentId
        ? { has_children: false }
        : { children, has_children: true }),
    });
    const moved = (id, name) => ({
      category_id: id,
      name,
      level: id.startsWith("s-") ? 2 : 1,
      share: 0.5,
      previous_share: 0.2,
      delta: 0.3,
      count: 5,
      significant: true,
    });
    const unitIn = (categoryId, subId) => ({
      ...unitsFixture.result.units[0],
      unit_id: `u-${subId}`,
      category_id: categoryId,
      sub_category_id: subId,
    });
    const unitCalls = () =>
      mocks.readQuery.mock.calls
        .filter(
          ([url, options]) => url === UNITS_URL && !options?.params?.unit_ids,
        )
        .map(([, options]) => options.params);

    beforeEach(() => {
      const base = mocks.readQuery.getMockImplementation();
      mocks.readQuery.mockImplementation((url, options = {}) => {
        if (url === `/tracer/insights/runs/${RUN_ID}/`)
          return ok({
            ...runFixture.result,
            result: {
              ...runFixture.result.result,
              categories: [
                category("t-refunds", "Refunds", null, ["s-refunds"]),
                category("t-billing", "Billing", null, ["s-card"]),
                category("s-refunds", " refunds ", "t-refunds"),
                category("s-card", "Card declined", "t-billing"),
              ],
              changes: {
                categories: [
                  moved("t-refunds", "Refunds"),
                  moved("s-refunds", " refunds "),
                ],
                new_category_ids: [],
                metrics: [],
              },
            },
          });
        if (url === UNITS_URL) {
          const id = options.params?.unit_ids;
          const unit =
            id === "u-s-card"
              ? unitIn("t-billing", "s-card")
              : unitIn("t-refunds", "s-refunds");
          return ok({ units: [unit], total: 1 });
        }
        return base(url, options);
      });
    });

    it("lists it once in the table and the changes", async () => {
      renderAt(`?report=${CONFIG_ID}`);
      const table = await screen.findByRole("table");
      expect(within(table).getAllByText(/^refunds$/i)).toHaveLength(1);
      expect(
        screen.queryByRole("button", { name: "Show subcategories of Refunds" }),
      ).toBeNull();
      const changes = screen.getByRole("region", {
        name: "What changed since the last run",
      });
      expect(within(changes).getAllByText(/^refunds$/i)).toHaveLength(1);
    });

    it("names an example's category once and opens the same units", async () => {
      renderAt(`?report=${CONFIG_ID}&example=u-s-refunds`);
      const [chip, ...more] = await screen.findAllByRole(
        "button",
        { name: /^refunds$/i },
        { timeout: 4000 },
      );
      expect(more).toHaveLength(0);
      fireEvent.click(chip);
      await waitFor(() =>
        expect(screen.getByTestId("location").textContent).toContain(
          "category=t-refunds",
        ),
      );
      await waitFor(() =>
        expect(unitCalls()).toContainEqual(
          expect.objectContaining({ category_id: "t-refunds" }),
        ),
      );
      expect(unitCalls().some((params) => params.sub_category_id)).toBe(false);
    });

    it("opens the sub's id as the one category", async () => {
      renderAt(`?report=${CONFIG_ID}&category=s-refunds`);
      expect(
        await screen.findByRole("button", { name: "Measure with Jev" }),
      ).toBeInTheDocument();
      const tree = screen.getByRole("tree");
      expect(
        within(tree).getByRole("treeitem", { name: /Refunds/ }),
      ).toHaveAttribute("aria-selected", "true");
      expect(within(tree).getAllByRole("treeitem")).toHaveLength(2);
      expect(screen.queryByText(/\/ Refunds/)).toBeNull();
      await waitFor(() =>
        expect(unitCalls()).toContainEqual(
          expect.objectContaining({ category_id: "t-refunds" }),
        ),
      );
      expect(unitCalls().some((params) => params.sub_category_id)).toBe(false);
    });

    it("keeps a differently named sub under its top", async () => {
      renderAt(`?report=${CONFIG_ID}&example=u-s-card`);
      expect(
        await screen.findByRole(
          "button",
          { name: "Card declined" },
          { timeout: 4000 },
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Billing" }),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Card declined" }));
      await waitFor(() =>
        expect(unitCalls()).toContainEqual(
          expect.objectContaining({ sub_category_id: "s-card" }),
        ),
      );
      expect(await screen.findByText(/\/ Billing$/)).toBeInTheDocument();
    });
  });

  describe("credits", () => {
    // A refusal as the app's axios hands it over: the error envelope with
    // its status.
    const refused = (statusCode = 403) => ({
      status: false,
      code: "insights_credits",
      message:
        "This report needs about 12 credits; you have 2 left this month.",
      statusCode,
      result: {
        message:
          "This report needs about 12 credits; you have 2 left this month.",
        error_code: statusCode === 402 ? "PAYMENT_REQUIRED" : "FREE_TIER_LIMIT",
        credits_estimate: 12,
        credits_left: 2,
        sample_size: 50,
      },
    });
    const estimate = (sampleSize, extra = {}) =>
      ok({
        population: 108,
        population_is_lower_bound: false,
        sample_size: Math.min(sampleSize || 300, 108),
        credits_estimate: 3.6,
        credit_limited: false,
        credits_left: 500,
        credit_max_sample: null,
        credit_message: null,
        ...extra,
      });
    const estimates = () => postsTo("/tracer/insights/estimate/");

    it("says what a report costs", async () => {
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size)
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt("?new=1&lens=failures");
      expect(
        (
          await screen.findAllByText(
            /Analysing all 108 sessions · about \d+ minutes · about 3\.6 credits/,
          )
        ).length,
      ).toBeGreaterThan(0);
      expect(estimates().at(-1)[1].sample_size).toBe(300);
      expect(screen.getByRole("button", { name: "Run report" })).toBeEnabled();
    });

    it("says nothing of credits where reports aren't billed", async () => {
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size, {
              credits_estimate: null,
              credits_left: null,
            })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt("?new=1&lens=failures");
      const [line] = await screen.findAllByText(
        /Analysing all 108 sessions · about \d+ minutes/,
      );
      expect(line.textContent).not.toMatch(/credit/);
      expect(screen.queryByText(/credit/i)).toBeNull();
      expect(screen.getByRole("button", { name: "Run report" })).toBeEnabled();
    });

    it("holds a report while billing can't check credits, without pricing it at 0", async () => {
      const unavailable =
        "Credits can't be checked right now, so this can't start. Try again in a moment.";
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size, {
              // A billing config that doesn't load prices nothing.
              credits_estimate: 0,
              credit_limited: true,
              credit_check_unavailable: true,
              credits_left: null,
              credit_message: unavailable,
            })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt("?new=1&lens=failures");
      expect(await screen.findByRole("alert")).toHaveTextContent(unavailable);
      expect(
        screen.getByRole("button", { name: "Retry cost check" }),
      ).toBeEnabled();
      expect(screen.queryByRole("link", { name: "Open billing" })).toBeNull();
      const [line] = await screen.findAllByText(
        /Analysing all 108 sessions · about \d+ minutes/,
      );
      expect(line.textContent).not.toMatch(/about 0 credits/);
      expect(screen.queryByText(/about 0 credits/)).toBeNull();
      expect(screen.getByRole("button", { name: "Run report" })).toBeDisabled();
    });

    it("holds a report the credits don't cover, and offers a sample that fits", async () => {
      mocks.post.mockImplementation((url, body) =>
        url !== "/tracer/insights/estimate/"
          ? ok({ run_id: RUN_ID, config_id: CONFIG_ID })
          : body.sample_size === 40
            ? estimate(40, { credits_estimate: 1.2 })
            : estimate(body.sample_size, {
                credits_estimate: 3.6,
                credit_limited: true,
                credits_left: 1.3,
                credit_max_sample: 40,
                credit_message:
                  "This report needs about 3.6 credits; you have 1.3 left this month.",
              }),
      );
      renderAt("?new=1&lens=failures");
      const why = await screen.findByRole("status");
      expect(why).toHaveTextContent(
        "This report needs about 3.6 credits; you have 1.3 left this month.",
      );
      expect(screen.getByRole("button", { name: "Run report" })).toBeDisabled();
      fireEvent.click(
        within(why).getByRole("button", {
          name: "Analyse a sample of 40 instead",
        }),
      );
      await waitFor(() => expect(estimates().at(-1)[1].sample_size).toBe(40));
      await screen.findAllByText(/about 1\.2 credits/, {}, { timeout: 4000 });
      expect(screen.getByRole("button", { name: "Run report" })).toBeEnabled();
    });

    it("offers billing beside the sample that fits a budget limit", async () => {
      mocks.post.mockImplementation((url, body) =>
        url !== "/tracer/insights/estimate/"
          ? ok({ run_id: RUN_ID, config_id: CONFIG_ID })
          : estimate(body.sample_size, {
              credits_estimate: 3.6,
              credit_limited: true,
              credits_left: 1.3,
              credit_max_sample: 40,
              credit_message:
                "This report needs about 3.6 credits; your budget limit allows 1.3 more this month. A sample of up to 40 sessions fits.",
            }),
      );
      renderAt("?new=1&lens=failures");
      const why = await screen.findByRole("status");
      expect(why).toHaveTextContent("your budget limit allows 1.3 more");
      expect(
        within(why).getByRole("button", {
          name: "Analyse a sample of 40 instead",
        }),
      ).toBeEnabled();
      expect(
        within(why).getByRole("link", { name: "Open billing" }),
      ).toHaveAttribute("href", "/dashboard/settings/billing");
    });

    it("holds Run while a new sample size is costed", async () => {
      let release;
      mocks.post.mockImplementation((url, body) => {
        if (url !== "/tracer/insights/estimate/")
          return ok({ run_id: RUN_ID, config_id: CONFIG_ID });
        if (body.sample_size !== 300)
          return new Promise((resolve) => {
            release = () =>
              resolve(estimate(body.sample_size, { credits_estimate: 12 }));
          });
        return estimate(300);
      });
      renderAt("?new=1&lens=failures");
      await screen.findAllByText(/about 3\.6 credits/);
      expect(screen.getByRole("button", { name: "Run report" })).toBeEnabled();
      fireEvent.mouseDown(
        screen.getByRole("combobox", { name: "Sample size" }),
      );
      const options = screen.getAllByRole("option");
      fireEvent.click(options[options.length - 1]);
      await waitFor(() =>
        expect(estimates().at(-1)[1].sample_size).not.toBe(300),
      );
      // The previous cost is still on screen: Run waits for the new one.
      expect(screen.getByRole("button", { name: "Run report" })).toBeDisabled();
      release();
      await screen.findAllByText(/about 12 credits/, {}, { timeout: 4000 });
      expect(screen.getByRole("button", { name: "Run report" })).toBeEnabled();
    });

    it("leads to billing when no sample fits", async () => {
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size, {
              credit_limited: true,
              credits_left: 0,
              credit_max_sample: 0,
              credit_message: "You have no credits left this month.",
            })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt("?new=1&lens=failures");
      const why = await screen.findByRole("status");
      expect(why).toHaveTextContent("You have no credits left this month.");
      expect(
        within(why).getByRole("link", { name: "Open billing" }),
      ).toHaveAttribute("href", "/dashboard/settings/billing");
      expect(screen.getByRole("button", { name: "Run report" })).toBeDisabled();
    });

    it("says in full why a paused budget holds the report, with billing", async () => {
      const paused =
        "AI features are paused by your budget limit. Raise or remove it in billing to run this.";
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size, {
              credit_limited: true,
              credit_max_sample: null,
              credit_message: paused,
            })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt("?new=1&lens=failures");
      const run = await screen.findByRole("button", { name: "Run report" });
      await waitFor(() => expect(run).toBeDisabled());
      // The footer beside Run report: the whole sentence, not cut off, and
      // the way to billing.
      const footer = run.closest(".MuiStack-root");
      const why = within(footer).getByText(/AI features are paused/);
      expect(why).toHaveTextContent(paused);
      expect(why).not.toHaveClass("MuiTypography-noWrap");
      expect(
        within(why).getByRole("link", { name: "Open billing" }),
      ).toHaveAttribute("href", "/dashboard/settings/billing");
    });

    it.each([
      ["Run report", 403],
      ["Draft with AI", 402],
      [/Preview on 3 examples/, 403],
    ])(
      "says inline why %s was refused for credits, with billing",
      async (name, statusCode) => {
        mocks.post.mockImplementation((url, body) =>
          url === "/tracer/insights/estimate/"
            ? estimate(body.sample_size)
            : Promise.reject(refused(statusCode)),
        );
        renderAt("?new=1&lens=failures");
        await screen.findAllByText(/about 3\.6 credits/);
        const button = await screen.findByRole("button", { name });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);
        const message = await screen.findByText(
          /This report needs about 12 credits; you have 2 left this month\./,
        );
        const where = message.closest("p, div, [role='alert']");
        expect(
          within(where).getByRole("link", { name: "Open billing" }),
        ).toHaveAttribute("href", "/dashboard/settings/billing");
      },
    );

    it("says inline why a question about the report was refused", async () => {
      mocks.enriched = true;
      mocks.post.mockImplementation(() => Promise.reject(refused(402)));
      renderAt(`?report=${CONFIG_ID}`);
      const question = await screen.findByRole("button", {
        name: "Which users are hit hardest?",
      });
      await waitFor(() => expect(question).toBeEnabled());
      fireEvent.click(question);
      const message = await screen.findByText(
        /This report needs about 12 credits; you have 2 left this month\./,
      );
      expect(
        within(message).getByRole("link", { name: "Open billing" }),
      ).toBeInTheDocument();
    });

    it("holds a run again the credits don't cover", async () => {
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? estimate(body.sample_size, {
              credit_limited: true,
              credits_left: 1.3,
              credit_max_sample: 40,
              credit_message:
                "This report needs about 3.6 credits; you have 1.3 left this month.",
            })
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt(`?report=${CONFIG_ID}`);
      fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
      const dialog = await screen.findByRole("dialog");
      expect(
        await within(dialog).findByText(
          "This report needs about 3.6 credits; you have 1.3 left this month.",
          { exact: false },
        ),
      ).toBeInTheDocument();
      expect(
        within(dialog).getByRole("button", { name: "Run again" }),
      ).toBeDisabled();
      expect(
        within(dialog).getByRole("button", {
          name: "Analyse a sample of 40 instead",
        }),
      ).toBeInTheDocument();
    });

    // Rows handed over by id (Find patterns from Instant eval, or a run
    // again on the same rows) are priced by their number: the server has
    // no scope to count, and answers with no window.
    const priced = (body, extra = {}) =>
      ok({
        population: body.population_size,
        population_is_lower_bound: false,
        sample_size: Math.min(body.sample_size || 300, body.population_size),
        window: null,
        sample_ids: [],
        credits_estimate: 3.6,
        credit_limited: false,
        credits_left: 500,
        credit_max_sample: null,
        credit_message: null,
        ...extra,
      });
    const LIMITED = {
      credit_limited: true,
      credits_left: 1.3,
      credit_max_sample: 40,
      credit_message:
        "This report needs about 3.6 credits; you have 1.3 left this month.",
    };
    const handOver = () => {
      const key = "seedcost1";
      sessionStorage.setItem(
        `observe-handoff:${key}`,
        JSON.stringify({
          unit: "sessions",
          population: population.slice(0, 12),
          label: "“No” answers · Was it resolved?",
          source: "Instant eval: Was it resolved?",
        }),
      );
      return `?new=1&seed=${key}`;
    };

    it.each([
      ["says what a handed-over population costs", {}],
      ["holds a handed-over population the credits don't cover", LIMITED],
    ])("%s", async (_, extra) => {
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? priced(body, extra)
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt(handOver());
      expect(await screen.findByText("12 sessions")).toBeInTheDocument();
      await waitFor(() => expect(estimates()).toHaveLength(1));
      expect(estimates()[0][1]).toEqual({
        project_id: PROJECT,
        unit: "sessions",
        population_size: 12,
        sample_size: 300,
      });
      const run = screen.getByRole("button", { name: "Run report" });
      if (extra.credit_limited) {
        expect(
          (await screen.findAllByText(LIMITED.credit_message, { exact: false }))
            .length,
        ).toBeGreaterThan(0);
        expect(run).toBeDisabled();
      } else {
        expect(
          (await screen.findAllByText(/about 3\.6 credits/)).length,
        ).toBeGreaterThan(0);
        expect(run).toBeEnabled();
      }
    });

    it.each([
      ["says what a run again on the same rows costs", {}],
      ["holds a run again on the same rows the credits don't cover", LIMITED],
    ])("%s", async (_, extra) => {
      const FIXED = { preset: "fixed", label: "3 sessions from Instant eval" };
      const rows = ["s1", "s2", "s3"].map((id) => ({
        id,
        time: "2026-10-10T00:00:00Z",
        user_id: "",
      }));
      sessionStorage.setItem(
        `insights-population:${CONFIG_ID}`,
        JSON.stringify(rows),
      );
      const base = mocks.readQuery.getMockImplementation();
      mocks.readQuery.mockImplementation((url, options) =>
        url === `/tracer/insights/configs/${CONFIG_ID}/`
          ? ok({ ...configDetail, scope: FIXED })
          : base(url, options),
      );
      mocks.post.mockImplementation((url, body) =>
        url === "/tracer/insights/estimate/"
          ? priced(body, extra)
          : ok({ run_id: RUN_ID, config_id: CONFIG_ID }),
      );
      renderAt(`?report=${CONFIG_ID}`);
      fireEvent.click(
        await screen.findByRole(
          "button",
          { name: "Run now" },
          { timeout: 5000 },
        ),
      );
      const dialog = await screen.findByRole("dialog");
      await waitFor(() => expect(estimates()).toHaveLength(1));
      expect(estimates()[0][1]).toEqual({
        project_id: PROJECT,
        unit: "sessions",
        population_size: 3,
        sample_size: configDetail.sample_size || 300,
      });
      const run = within(dialog).getByRole("button", { name: "Run again" });
      if (extra.credit_limited) {
        expect(
          await within(dialog).findByText(LIMITED.credit_message, {
            exact: false,
          }),
        ).toBeInTheDocument();
        expect(run).toBeDisabled();
      } else {
        expect(
          await within(dialog).findByText(/about 3\.6 credits/),
        ).toBeInTheDocument();
        expect(run).toBeEnabled();
      }
    });
  });

  describe("a fault on the server's side", () => {
    const failures = {
      503: {
        status: false,
        statusCode: 503,
        message: "Database temporarily unavailable",
      },
      500: { status: false, statusCode: 500, message: "Server Error" },
      none: { message: "Network Error", transportCode: "ERR_NETWORK" },
    };

    it.each([
      ["Run report", 503, "Couldn't start right now. Try again in a moment."],
      ["Run report", 500, "Couldn't start right now. Try again in a moment."],
      [
        "Run report",
        "none",
        "Couldn't start right now. Try again in a moment.",
      ],
      [
        "Draft with AI",
        503,
        "Couldn't draft right now. Try again in a moment.",
      ],
      [
        /Preview on 3 examples/,
        "none",
        "Couldn't preview right now. Try again in a moment.",
      ],
    ])("%s (%s) asks to try again", async (name, failure, words) => {
      mocks.post.mockImplementation((url) =>
        url === "/tracer/insights/estimate/"
          ? ok({ population: 108, population_is_lower_bound: false })
          : Promise.reject(failures[failure]),
      );
      renderAt("?new=1&lens=failures");
      await screen.findAllByText(/Analysing all 108 sessions/);
      const button = await screen.findByRole("button", { name });
      await waitFor(() => expect(button).toBeEnabled());
      fireEvent.click(button);
      expect(await screen.findByText(words)).toBeInTheDocument();
      // The report as set up, ready to try again.
      expect(screen.getByLabelText(/Question/)).toHaveValue(
        "Where is the agent failing?",
      );
      await waitFor(() => expect(button).toBeEnabled());
    });
  });
});
