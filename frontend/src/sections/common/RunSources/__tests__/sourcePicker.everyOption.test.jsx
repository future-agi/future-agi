// The real New experiment picker (step 1, the Already
// running list, both searches) shows every promised way to run. What the
// server runs works as on cut 3 (Prompt, Agent graph, HTTP endpoint); every
// way this release does not build is shown greyed with "Not available in
// this release", and no click, Enter, Space, number key, search Enter or
// link adds one.
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SourcePicker from "../SourcePicker";
import { usePickerUi } from "../pickerUi";
import {
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "../runSourceAvailability";

const harness = vi.hoisted(() => ({
  url: {},
  setUrl: vi.fn(),
  availability: null,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("../useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => harness.availability,
}));
vi.mock("src/sections/common/ux/viewState", async (importOriginal) => ({
  ...(await importOriginal()),
  useViewUrlState: () => [harness.url, harness.setUrl],
}));
vi.mock("src/sections/common/ux/useShortcuts", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => {},
  openShortcutSheet: vi.fn(),
}));
// Single-export modules are replaced whole; partial mocks spread the original.
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../BundledIcon", () => ({ default: () => null }));
vi.mock("../SourceLogo", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
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
vi.mock("src/sections/common/ux/KeyHintTooltip", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ children }) => children,
}));
/* eslint-enable react/prop-types */
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("src/api/experiment/use-get-agents", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetListOfAgents: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("../graphModels-g2", async (importOriginal) => ({
  ...(await importOriginal()),
  useGraphModels: () => ({ data: {}, isLoading: false }),
  withGraphModels: (graphs) => graphs,
}));
vi.mock("../SourceConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="source-config">Continue</div>,
}));

const LINE = "Not available in this release";
const A2A_OFF = "A2A experiments are not built in this release.";
const MCP_OFF = "MCP tool experiments are not built in this release.";
const YES = { available: true, reason: null };
// What GET /api/capabilities/ answers in `experiment_run_sources` at cut 3.
const ready = () =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: { available: false, reason: A2A_OFF },
      mcp_tool: { available: false, reason: MCP_OFF },
    },
  });
const HELD_CARDS = [
  ["code", "Your code (SDK / CLI)"],
  ["harness", "Agent harness"],
  ["voice", "Voice agent"],
];
const HELD_TILES = [
  ["traces", "Traces I already have"],
  ["upload", "Upload results"],
];
const RUNNER = /futureagi run/i;

beforeEach(() => {
  vi.useFakeTimers();
  harness.url = {};
  harness.availability = ready();
  harness.setUrl.mockReset();
  usePickerUi.setState({ deepEntry: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function mount() {
  const onAddCard = vi.fn();
  const onAddR4 = vi.fn();
  render(
    <div role="dialog" aria-label="New experiment" tabIndex={-1}>
      <SourcePicker
        datasetKind="rows"
        onAddCard={onAddCard}
        onAddR4={onAddR4}
      />
    </div>,
  );
  return { onAddCard, onAddR4 };
}

function expectNothingAdded({ onAddCard, onAddR4 }) {
  expect(onAddCard).not.toHaveBeenCalled();
  expect(onAddR4).not.toHaveBeenCalled();
  expect(harness.setUrl).not.toHaveBeenCalled();
  expect(screen.queryByTestId("source-config")).toBeNull();
}

const searchBox = () =>
  screen.getByRole("textbox", { name: "Search ways to run" });
const search = (value) => fireEvent.change(searchBox(), { target: { value } });

// The step-1 cards in render order (the `+N` counts carry no role).
const stepCards = () =>
  [
    ...screen
      .getByTestId("source-cards")
      .querySelectorAll('[role="button"][data-testid^="source-card-"]'),
  ].map((el) => el.getAttribute("data-testid").replace("source-card-", ""));

// A greyed card or tile: inert, named by its title and described by the
// release line. A card is out of the tab order; a tile stays in it, as every
// greyed tile does, so its reason can be read.
function expectHeld(el, name, { tile = false } = {}) {
  expect(el).toBeVisible();
  expect(within(el).getByText(name)).toBeVisible();
  expect(within(el).getByText(LINE)).toBeVisible();
  expect(el).toHaveAttribute("aria-disabled", "true");
  expect(el).toHaveAttribute("tabindex", tile ? "0" : "-1");
  expect(el).not.toHaveAttribute("aria-keyshortcuts");
  expect(el).toHaveAccessibleName(name);
  expect(el).toHaveAccessibleDescription(LINE);
}

describe("step 1 · What are you testing?", () => {
  it("shows six ways: Prompt, Agent graph, Already running, then the three held", () => {
    mount();
    expect(stepCards()).toEqual([
      "prompt",
      "agent",
      "running",
      "code",
      "harness",
      "voice",
    ]);
  });

  it.each(HELD_CARDS)(
    "shows %s greyed with the release line; click, Enter and Space add nothing",
    (id, title) => {
      const callbacks = mount();
      const card = screen.getByTestId(`source-card-${id}`);
      expectHeld(card, title);
      expect(card.querySelector(".sc-key")).toBeNull();
      expect(screen.queryByTestId("recommended-badge")).not.toBeNull();
      expect(within(card).queryByTestId("recommended-badge")).toBeNull();
      fireEvent.click(card);
      fireEvent.keyDown(card, { key: "Enter" });
      fireEvent.keyDown(card, { key: " " });
      expectNothingAdded(callbacks);
      expect(screen.getByTestId("source-picker")).toHaveAttribute(
        "data-level",
        "1",
      );
    },
  );

  it("number keys 4 to 6 (the held cards) open nothing", () => {
    const callbacks = mount();
    const dialog = screen.getByRole("dialog");
    ["4", "5", "6"].forEach((key) => fireEvent.keyDown(dialog, { key }));
    expectNothingAdded(callbacks);
  });

  it.each(["prompt", "agent"])("still adds a %s run from its card", (kind) => {
    const { onAddCard, onAddR4 } = mount();
    const card = screen.getByTestId(`source-card-${kind}`);
    expect(card).not.toHaveAttribute("aria-disabled");
    expect(within(card).queryByText(LINE)).toBeNull();
    fireEvent.click(card);
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith(kind, card);
    expect(onAddR4).not.toHaveBeenCalled();
  });

  it("still opens Already running, whose HTTP endpoint the server runs", () => {
    const { onAddCard } = mount();
    const card = screen.getByTestId("source-card-running");
    expect(card).not.toHaveAttribute("aria-disabled");
    expect(within(card).getByText("An HTTP endpoint you host.")).toBeVisible();
    fireEvent.click(card);
    expect(harness.setUrl).toHaveBeenLastCalledWith(
      expect.objectContaining({ src: "running" }),
    );
    expect(onAddCard).not.toHaveBeenCalled();
  });

  it("shows no runner command", () => {
    mount();
    expect(screen.getByTestId("source-picker")).not.toHaveTextContent(RUNNER);
  });
});

describe("Already running · the list behind the card", () => {
  const tiles = () =>
    [...document.querySelectorAll('[data-tile="1"]')].map((el) =>
      el.getAttribute("data-testid").replace("catalog-tile-", ""),
    );

  it("lists HTTP endpoint, A2A, MCP tool, Traces I already have and Upload results", () => {
    harness.url = { src: "running" };
    mount();
    expect(tiles()).toEqual(["http", "a2a", "mcp_tool", "traces", "upload"]);
    expect(screen.getByTestId("source-picker")).not.toHaveTextContent(RUNNER);
  });

  it.each(HELD_TILES)(
    "shows %s greyed with the release line; click, Enter and Space add nothing",
    (id, name) => {
      harness.url = { src: "running" };
      const callbacks = mount();
      const tile = screen.getByTestId(`catalog-tile-${id}`);
      expectHeld(tile, name, { tile: true });
      expect(tile).not.toHaveAttribute("data-first");
      fireEvent.click(tile);
      fireEvent.keyDown(tile, { key: "Enter" });
      fireEvent.keyDown(tile, { key: " " });
      expectNothingAdded(callbacks);
    },
  );

  it.each([
    ["a2a", "A2A agent card", A2A_OFF],
    ["mcp_tool", "MCP server tool", MCP_OFF],
  ])(
    "keeps %s greyed with the server's reason, as cut 3 does",
    (id, name, reason) => {
      harness.url = { src: "running" };
      mount();
      const tile = screen.getByTestId(`catalog-tile-${id}`);
      expect(tile).toHaveAttribute("aria-disabled", "true");
      expect(tile).toHaveAttribute("tabindex", "0");
      expect(within(tile).getByText(reason)).toBeVisible();
      expect(tile).toHaveAccessibleName(name);
      expect(tile).toHaveAccessibleDescription(reason);
    },
  );

  it("still adds the HTTP endpoint run", () => {
    harness.url = { src: "running" };
    const { onAddCard, onAddR4 } = mount();
    const tile = screen.getByTestId("catalog-tile-http");
    expect(tile).not.toHaveAttribute("aria-disabled");
    fireEvent.click(tile);
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
    expect(onAddR4).not.toHaveBeenCalled();
  });

  it.each(["traces", "upload"])(
    "the list's search finds %s, greyed; Enter adds nothing",
    (q) => {
      harness.url = { src: "running" };
      const callbacks = mount();
      const box = screen.getByRole("textbox", { name: "Search this list" });
      fireEvent.change(box, { target: { value: q } });
      expect(tiles()).toEqual([q]);
      expect(screen.getByTestId(`catalog-tile-${q}`)).toHaveAttribute(
        "aria-disabled",
        "true",
      );
      fireEvent.keyDown(box, { key: "Enter" });
      expectNothingAdded(callbacks);
    },
  );

  it("says 'Not available in this release' for A2A and MCP while the report loads", () => {
    harness.availability = LOADING_AVAILABILITY;
    harness.url = { src: "running" };
    mount();
    for (const id of ["a2a", "mcp_tool", "traces", "upload"])
      expect(
        within(screen.getByTestId(`catalog-tile-${id}`)).getByText(LINE),
        id,
      ).toBeVisible();
    // The endpoint is in this release: it waits for the server's answer.
    expect(
      within(screen.getByTestId("catalog-tile-http")).getByText(
        "Not available in this deployment",
      ),
    ).toBeVisible();
  });
});

describe("step-1 search · a held way is found and shown greyed, never added", () => {
  it.each([
    ["sdk", "code"],
    ["cli", "code"],
    ["python", "code"],
    ["typescript", "code"],
    ["your code", "code"],
    ["harness", "harness"],
    ["claude code", "harness"],
    ["voice", "voice"],
  ])("%s finds %s; Enter adds nothing", (query, id) => {
    const callbacks = mount();
    search(query);
    const hit = within(screen.getByTestId(`search-card-${id}`)).getByTestId(
      `source-card-${id}`,
    );
    expectHeld(hit, Object.fromEntries(HELD_CARDS)[id]);
    expect(screen.queryByTestId("source-no-match")).toBeNull();
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    fireEvent.click(hit);
    expectNothingAdded(callbacks);
    expect(screen.getByTestId("source-picker")).not.toHaveTextContent(RUNNER);
  });

  it.each(HELD_TILES)(
    "%s finds its tile, greyed; Enter adds nothing",
    (id, name) => {
      const callbacks = mount();
      search(id);
      const tile = screen.getByTestId(`catalog-tile-${id}`);
      expectHeld(tile, name, { tile: true });
      expect(tile).not.toHaveAttribute("data-first");
      expect(screen.queryByTestId("source-no-match")).toBeNull();
      fireEvent.keyDown(searchBox(), { key: "Enter" });
      expectNothingAdded(callbacks);
    },
  );

  it("agent lists Agent graph first, the held ones greyed after it; Enter adds the graph", () => {
    const { onAddCard } = mount();
    search("agent");
    const hits = [
      ...screen
        .getByTestId("source-search-results")
        .querySelectorAll('[data-testid^="search-card-"]'),
    ].map((el) => el.getAttribute("data-testid").replace("search-card-", ""));
    expect(hits).toEqual(["agent", "running", "harness", "voice"]);
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith(
      "agent",
      expect.anything(),
    );
  });

  it("counts and suggests only the ways that can be picked", () => {
    mount();
    expect(searchBox()).toHaveAttribute(
      "placeholder",
      "Search 3 ways to run, prompts, graphs, models",
    );
    search("zzqx");
    expect(screen.getByTestId("source-no-match")).toHaveTextContent(
      'No match for "zzqx". Try Prompt, Agent graph or HTTP endpoint.',
    );
    // The cards under the line still show all six, the held ones greyed.
    expect(stepCards()).toEqual([
      "prompt",
      "agent",
      "running",
      "code",
      "harness",
      "voice",
    ]);
  });
});

describe("no link opens a held way", () => {
  it.each([
    [{ src: "code" }],
    [{ src: "harness" }],
    [{ src: "voice" }],
    [{ new_experiment: "1", source: "code" }],
    [{ new_experiment: "1", source: "harness", entry: "claude_code" }],
    [{ new_experiment: "1", source: "code", entry: "langgraph" }],
    [{ new_experiment: "1", entry: "traces" }],
    [{ new_experiment: "1", entry: "upload" }],
  ])("%j stays on step 1 and adds nothing", (url) => {
    harness.url = url;
    const callbacks = mount();
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "1",
    );
    expectNothingAdded(callbacks);
    expect(usePickerUi.getState().deepEntry).toBeNull();
  });
});
