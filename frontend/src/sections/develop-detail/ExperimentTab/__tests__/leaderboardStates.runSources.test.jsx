// The Experiments tab's doors into New experiment follow the server's
// run-source report (GET /api/capabilities/ experiment_run_sources): the
// empty tab's cards and keys, and the add action of a search no run matches.
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import { LeaderboardShell } from "../LeaderboardStates";

const harness = vi.hoisted(() => ({
  availability: null,
  openSource: vi.fn(),
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => harness.availability,
}));
vi.mock("../useOpenSource", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => harness.openSource,
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({
    kind: "rows",
    dataset: { kind: "rows", row_count: 3 },
  }),
}));
// Step 1's search reads prompts and graphs; none are needed here.
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("src/api/experiment/use-get-agents", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetListOfAgents: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock(
  "src/sections/common/RunSources/graphModels-g2",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useGraphModels: () => ({ data: {}, isLoading: false }),
    withGraphModels: (graphs) => graphs,
  }),
);
// Single-export modules are replaced whole; partial mocks spread the original.
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSources/BundledIcon", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/common/RunSources/SourceLogo",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock(
  "src/sections/common/Connect/ConnectionPill",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
/* eslint-disable react/prop-types */
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
/* eslint-enable react/prop-types */

// What GET /api/capabilities/ answers in `experiment_run_sources`.
const YES = { available: true, reason: null };
const NO = (reason) => ({ available: false, reason });
const AGENT_OFF =
  "Agent graph experiments are unavailable on this server. Contact your administrator.";
const ready = (sources = {}) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: NO("A2A experiments are not built in this release."),
      mcp_tool: NO("MCP tool experiments are not built in this release."),
      ...sources,
    },
  });
// Owner rule: nothing ships greyed with a promise.
const PROMISE = /A2A|MCP|coming|soon|not built|later|not available/i;

const RUN = { run_id: "run-1", label: "#1A", name: "Support reply" };
const viewOf = (overrides = {}) => ({
  query: { data: {}, isLoading: false, isError: false, refetch: vi.fn() },
  datasetId: "ds",
  allRuns: [],
  runs: [],
  version: "all",
  q: "",
  activeFilters: [],
  setQ: vi.fn(),
  setFilters: vi.fn(),
  setVersion: vi.fn(),
  ...overrides,
});
const shell = (view) => (
  <LeaderboardShell view={view} onClearFilters={vi.fn()} />
);

beforeEach(() => {
  harness.availability = ready();
  harness.openSource.mockReset();
});
afterEach(cleanup);

describe("the empty Experiments tab offers what the server runs", () => {
  it("shows Already running as the endpoint the server runs", () => {
    render(shell(viewOf()));
    expect(screen.getByTestId("experiments-empty")).toBeInTheDocument();
    const running = screen.getByTestId("source-card-running");
    expect(
      within(running).getByText("An HTTP endpoint you host."),
    ).toBeVisible();
    // The ways the server runs promise nothing; the held ways say only
    // "Not available in this release".
    for (const id of ["prompt", "agent", "running"])
      expect(screen.getByTestId(`source-card-${id}`), id).not.toHaveTextContent(
        PROMISE,
      );
    fireEvent.click(running);
    expect(harness.openSource).toHaveBeenCalledExactlyOnceWith({
      cardId: "running",
    });
  });

  it("lists Your code (SDK / CLI), Agent harness and Voice agent greyed; nothing opens them", () => {
    render(shell(viewOf()));
    for (const [id, title] of [
      ["code", "Your code (SDK / CLI)"],
      ["harness", "Agent harness"],
      ["voice", "Voice agent"],
    ]) {
      const card = screen.getByTestId(`source-card-${id}`);
      expect(within(card).getByText(title)).toBeVisible();
      expect(
        within(card).getByText("Not available in this release"),
      ).toBeVisible();
      expect(card).toHaveAttribute("aria-disabled", "true");
      expect(card).toHaveAttribute("tabindex", "-1");
      fireEvent.click(card);
      fireEvent.keyDown(card, { key: "Enter" });
    }
    // Keys 4 to 6 are the held cards: they open nothing.
    for (const key of ["4", "5", "6"]) fireEvent.keyDown(document, { key });
    expect(harness.openSource).not.toHaveBeenCalled();
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
  ])("shows the Already running card greyed while the report is %s", (_, a) => {
    harness.availability = a();
    render(shell(viewOf()));
    const running = screen.getByTestId("source-card-running");
    expect(running).toBeVisible();
    expect(running).toHaveAttribute("aria-disabled", "true");
    expect(
      within(running).getByText("Not available in this deployment"),
    ).toBeVisible();
    fireEvent.click(running);
    fireEvent.keyDown(document, { key: "3" });
    expect(harness.openSource).not.toHaveBeenCalled();
    // Prompt and Agent graph stay usable; create and run still check.
    fireEvent.keyDown(document, { key: "2" });
    expect(harness.openSource).toHaveBeenCalledExactlyOnceWith({
      cardId: "agent",
    });
  });

  it("gives a refused Agent graph no key, and keys the cards that run", () => {
    harness.availability = ready({ agent: NO(AGENT_OFF) });
    render(shell(viewOf()));
    const agent = screen.getByTestId("source-card-agent");
    expect(agent).toHaveAttribute("aria-disabled", "true");
    expect(within(agent).getByText(AGENT_OFF)).toBeVisible();
    fireEvent.keyDown(document, { key: "2" });
    fireEvent.click(agent);
    expect(harness.openSource).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "1" });
    expect(harness.openSource).toHaveBeenCalledExactlyOnceWith({
      cardId: "prompt",
    });
    fireEvent.keyDown(document, { key: "3" });
    expect(harness.openSource).toHaveBeenLastCalledWith({ cardId: "running" });
  });
});

describe("a search no run matches adds only a source the server runs", () => {
  const searched = (q) => viewOf({ allRuns: [RUN], runs: [], q });

  it.each([
    [
      "mcp",
      "MCP server tool",
      "MCP tool experiments are not built in this release.",
    ],
    ["a2a", "A2A agent card", "A2A experiments are not built in this release."],
  ])(
    "names %s with the server's reason and a disabled add",
    (q, name, reason) => {
      render(shell(searched(q)));
      expect(
        screen.getByText(`${name} has not run on this dataset.`),
      ).toBeInTheDocument();
      expect(screen.getByText(reason)).toBeInTheDocument();
      const add = screen.getByRole("button", { name: `Add an ${name} run` });
      expect(add).toBeDisabled();
      fireEvent.click(add);
      expect(harness.openSource).not.toHaveBeenCalled();
    },
  );

  it("offers the HTTP endpoint run the server runs", () => {
    render(shell(searched("http")));
    expect(
      screen.getByText("HTTP endpoint has not run on this dataset."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Add an HTTP endpoint run" }),
    );
    expect(harness.openSource).toHaveBeenCalledExactlyOnceWith({
      entryId: "http",
    });
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
    ["unavailable", () => ready({ http: NO("Not on this server.") })],
  ])("keeps the endpoint add, disabled, while the report is %s", (_, a) => {
    harness.availability = a();
    render(shell(searched("http")));
    expect(
      screen.getByText("HTTP endpoint has not run on this dataset."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add an HTTP endpoint run" }),
    ).toBeDisabled();
    expect(harness.openSource).not.toHaveBeenCalled();
  });
});
