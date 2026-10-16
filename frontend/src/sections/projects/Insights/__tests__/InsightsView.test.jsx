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
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
  collect: vi.fn(),
  enriched: false,
  omega: false,
}));

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
    },
  },
}));

vi.mock("src/sections/projects/observePopulation", async (importOriginal) => ({
  ...(await importOriginal()),
  collectPopulation: (...args) => mocks.collect(...args),
}));

import InsightsView from "../InsightsView";

const PROJECT = "1b9823aa-866c-400e-b60e-d99ae498d72a";
const sessionsConfig = configsFixture.result.configs[1];
const CONFIG_ID = sessionsConfig.id;
const RUN_ID = runFixture.result.id;
const configDetail = { ...sessionsConfig, runs: [sessionsConfig.latest_run] };

const ok = (result) => Promise.resolve({ data: { status: true, result } });

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
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
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("InsightsView", () => {
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
    const more = await screen.findByRole("button", { name: "1 more example" });
    expect(more).toHaveTextContent("+1");
    expect(
      screen.queryByRole("button", { name: "Open example 20" }),
    ).toBeNull();
    fireEvent.mouseOver(more);
    expect(
      await screen.findByRole("button", { name: "Open example 20" }),
    ).toBeInTheDocument();
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
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Which users are hit hardest?",
        }),
      );
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

  it("guides a new report and starts it with the collected population", async () => {
    mocks.post.mockResolvedValue({
      data: { status: true, result: { run_id: RUN_ID, config_id: CONFIG_ID } },
    });
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
    fireEvent.click(screen.getByRole("button", { name: "Run report" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalled());
    const [url, body] = mocks.post.mock.calls[0];
    expect(url).toBe("/tracer/insights/runs/");
    expect(body.config).toMatchObject({
      unit: "sessions",
      lens: "failures",
      question: "Where is the agent failing?",
      sample_size: 300,
    });
    expect(body.scope.preset).toBe("7d");
    expect(body.scope.window).toHaveLength(2);
    expect(body.population).toHaveLength(108);
    expect(mocks.collect).toHaveBeenCalledWith(
      expect.objectContaining({ unit: "sessions", limit: 2000 }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `?report=${CONFIG_ID}&run=${RUN_ID}&tab=insights`,
      ),
    );
  });

  it("runs a handed-over population as a fixed scope", async () => {
    mocks.post.mockResolvedValue({
      data: { status: true, result: { run_id: RUN_ID, config_id: CONFIG_ID } },
    });
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
    fireEvent.click(screen.getByRole("button", { name: "Run report" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalled());
    const body = mocks.post.mock.calls[0][1];
    expect(body.scope).toEqual({
      label: "“No” answers · Was it resolved?",
      preset: "fixed",
    });
    expect(body.population).toHaveLength(12);
    expect(mocks.collect).not.toHaveBeenCalledWith(
      expect.objectContaining({ limit: 2000 }),
    );
  });
});
