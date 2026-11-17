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
import CatalogTile from "../CatalogTile";
import { catalogEntry } from "../catalog";
import { usePickerUi } from "../pickerUi";
import {
  FAILED_AVAILABILITY,
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
const HTTP_OFF = {
  http: NO(
    "HTTP endpoint experiments are unavailable on this server. Contact your administrator.",
  ),
};
// Owner rule: nothing ships greyed with a promise.
const PROMISE = /A2A|MCP|coming|soon|not built|later|not available/i;

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

const picker = (onAddCard, onAddR4) => (
  <div role="dialog" aria-label="New experiment" tabIndex={-1}>
    <SourcePicker datasetKind="rows" onAddCard={onAddCard} onAddR4={onAddR4} />
  </div>
);

function mount() {
  const onAddCard = vi.fn();
  const onAddR4 = vi.fn();
  const view = render(picker(onAddCard, onAddR4));
  return {
    onAddCard,
    onAddR4,
    rerender: () => view.rerender(picker(onAddCard, onAddR4)),
  };
}

const search = (value) =>
  fireEvent.change(
    screen.getByRole("textbox", { name: "Search ways to run" }),
    {
      target: { value },
    },
  );

describe("New experiment run sources follow the server's report", () => {
  it("offers the HTTP endpoint and opens the list that greys A2A and MCP", () => {
    const { onAddCard, onAddR4 } = mount();
    const card = screen.getByTestId("source-card-running");
    expect(card).not.toHaveAttribute("aria-disabled");
    expect(card).toHaveAttribute("tabindex", "0");
    expect(within(card).getByText("An HTTP endpoint you host.")).toBeVisible();
    // The ways the server runs promise nothing; the held ways (Your code,
    // Agent harness, Voice agent) say only "Not available in this release".
    for (const id of ["prompt", "agent", "running"])
      expect(screen.getByTestId(`source-card-${id}`), id).not.toHaveTextContent(
        PROMISE,
      );
    for (const id of ["code", "harness", "voice"])
      expect(
        within(screen.getByTestId(`source-card-${id}`)).getByText(
          "Not available in this release",
        ),
        id,
      ).toBeVisible();
    // Refused sources sit behind the card: picking it opens the list, so
    // their greyed tiles stay in view, instead of adding the endpoint.
    fireEvent.click(card);
    expect(onAddCard).not.toHaveBeenCalled();
    expect(onAddR4).not.toHaveBeenCalled();
    expect(harness.setUrl).toHaveBeenLastCalledWith(
      expect.objectContaining({ src: "running" }),
    );
  });

  it("offers the HTTP endpoint from the keys and from search", () => {
    const { onAddCard } = mount();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "3" });
    expect(harness.setUrl).toHaveBeenLastCalledWith(
      expect.objectContaining({ src: "running" }),
    );
    expect(onAddCard).not.toHaveBeenCalled();
    search("http");
    const tile = screen.getByTestId("catalog-tile-http");
    expect(within(tile).getByText("HTTP endpoint")).toBeVisible();
    fireEvent.keyDown(
      screen.getByRole("textbox", { name: "Search ways to run" }),
      {
        key: "Enter",
      },
    );
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
  });

  it("opens every tile from a door's Already running link and adds nothing by itself", () => {
    // The kinds menu and the Experiments tab open the card by `src=running`.
    harness.url = { new_experiment: "1", src: "running" };
    const { onAddCard, onAddR4 } = mount();
    act(() => vi.advanceTimersByTime(500));
    expect(onAddCard).not.toHaveBeenCalled();
    expect(onAddR4).not.toHaveBeenCalled();
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "2",
    );
    expect(screen.getByTestId("catalog-running")).toBeInTheDocument();
    for (const id of ["http", "a2a", "mcp_tool"])
      expect(screen.getByTestId(`catalog-tile-${id}`), id).toBeVisible();
  });

  it("opens the list when the server runs more than one source", () => {
    harness.availability = ready({ mcp_tool: YES });
    harness.url = { new_experiment: "1", src: "running" };
    const { onAddCard } = mount();
    act(() => vi.advanceTimersByTime(500));
    expect(onAddCard).not.toHaveBeenCalled();
    expect(screen.getByTestId("catalog-running")).toBeInTheDocument();
  });

  it("lists every source behind Already running and greys the ones the server refuses", () => {
    harness.url = { src: "running" };
    const { onAddCard } = mount();
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "2",
    );
    const http = screen.getByTestId("catalog-tile-http");
    expect(http).toBeVisible();
    expect(http).not.toHaveAttribute("aria-disabled");
    for (const [id, reason] of [
      ["a2a", "A2A experiments are not built in this release."],
      ["mcp_tool", "MCP tool experiments are not built in this release."],
    ]) {
      const tile = screen.getByTestId(`catalog-tile-${id}`);
      expect(tile, id).toBeVisible();
      expect(tile).toHaveAttribute("aria-disabled", "true");
      // Keyboard-reachable, so its reason can be read; never pickable.
      expect(tile).toHaveAttribute("tabindex", "0");
      expect(within(tile).getByText(reason)).toBeVisible();
      fireEvent.click(tile);
      fireEvent.keyDown(tile, { key: "Enter" });
      fireEvent.keyDown(tile, { key: " " });
    }
    expect(onAddCard).not.toHaveBeenCalled();
    fireEvent.click(http);
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
    ["unavailable", () => ready(HTTP_OFF)],
  ])(
    "shows Already running and its tiles greyed while the report is %s",
    (_, availability) => {
      harness.availability = availability();
      const { onAddCard, onAddR4 } = mount();
      const card = screen.getByTestId("source-card-running");
      expect(card).toBeVisible();
      expect(card).toHaveAttribute("aria-disabled", "true");
      expect(
        within(card).getByText("Not available in this deployment"),
      ).toBeVisible();
      fireEvent.click(card);
      fireEvent.keyDown(screen.getByRole("dialog"), { key: "3" });
      for (const [query, id] of [
        ["http", "http"],
        ["a2a", "a2a"],
        ["mcp", "mcp_tool"],
      ]) {
        search(query);
        const tile = screen.getByTestId(`catalog-tile-${id}`);
        expect(tile, query).toBeVisible();
        expect(tile).toHaveAttribute("aria-disabled", "true");
        fireEvent.keyDown(
          screen.getByRole("textbox", { name: "Search ways to run" }),
          { key: "Enter" },
        );
      }
      expect(onAddCard).not.toHaveBeenCalled();
      expect(onAddR4).not.toHaveBeenCalled();
      expect(harness.setUrl).not.toHaveBeenCalled();
      expect(screen.queryByRole("alert")).toBeNull();
    },
  );

  it.each(["loading", "failed", "unavailable"])(
    "opens a link to the running catalog with every tile greyed while the report is %s",
    (state) => {
      harness.availability = {
        loading: LOADING_AVAILABILITY,
        failed: FAILED_AVAILABILITY,
        unavailable: ready(HTTP_OFF),
      }[state];
      harness.url = { src: "running" };
      const { onAddCard } = mount();
      expect(screen.getByTestId("source-picker")).toHaveAttribute(
        "data-level",
        "2",
      );
      for (const id of ["http", "a2a", "mcp_tool"]) {
        const tile = screen.getByTestId(`catalog-tile-${id}`);
        expect(tile, id).toBeVisible();
        expect(tile).toHaveAttribute("aria-disabled", "true");
        fireEvent.click(tile);
      }
      if (state !== "unavailable") {
        // The endpoint waits for the server's answer; A2A, MCP tool and the
        // held Traces I already have and Upload results are not in this
        // release.
        expect(
          within(screen.getByTestId("catalog-tile-http")).getByText(
            "Not available in this deployment",
          ),
        ).toBeVisible();
        expect(
          screen.getAllByText("Not available in this deployment"),
        ).toHaveLength(1);
        expect(
          screen.getAllByText("Not available in this release"),
        ).toHaveLength(4);
      }
      expect(onAddCard).not.toHaveBeenCalled();
    },
  );

  it.each(["a2a", "mcp_tool"])(
    "adds nothing from a deep link to %s, which the server cannot run",
    (entry) => {
      harness.url = { new_experiment: "1", entry, mode: "remote" };
      const { onAddCard, onAddR4 } = mount();
      act(() => vi.advanceTimersByTime(500));
      expect(onAddCard).not.toHaveBeenCalled();
      expect(onAddR4).not.toHaveBeenCalled();
      expect(usePickerUi.getState().deepEntry).toBeNull();
    },
  );

  it("adds the HTTP run from its deep link once the server can run it", () => {
    harness.availability = LOADING_AVAILABILITY;
    harness.url = { new_experiment: "1", entry: "http" };
    const { onAddCard, rerender } = mount();
    act(() => vi.advanceTimersByTime(500));
    expect(onAddCard).not.toHaveBeenCalled();
    harness.availability = ready();
    rerender();
    act(() => vi.advanceTimersByTime(500));
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
  });

  it("shows the server's reason on Agent graph and keeps it inert", () => {
    harness.availability = ready({ agent: NO(AGENT_OFF) });
    const { onAddCard } = mount();
    const agent = screen.getByTestId("source-card-agent");
    expect(agent).toHaveAttribute("aria-disabled", "true");
    expect(agent).toHaveAttribute("tabindex", "-1");
    expect(within(agent).getByText(AGENT_OFF)).toBeVisible();
    fireEvent.click(agent);
    fireEvent.keyDown(agent, { key: "Enter" });
    fireEvent.keyDown(agent, { key: " " });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "2" });
    expect(onAddCard).not.toHaveBeenCalled();
    const prompt = screen.getByTestId("source-card-prompt");
    fireEvent.click(prompt);
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("prompt", prompt);
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
  ])(
    "keeps Prompt and Agent graph selectable while the report is %s",
    (_, a) => {
      harness.availability = a();
      const { onAddCard } = mount();
      for (const kind of ["prompt", "agent"]) {
        const card = screen.getByTestId(`source-card-${kind}`);
        expect(card).not.toHaveAttribute("aria-disabled");
        fireEvent.click(card);
        expect(onAddCard).toHaveBeenLastCalledWith(kind, card);
      }
    },
  );

  it("keeps a tile inert, with the server's reason, for a source it refused", () => {
    const onPick = vi.fn();
    render(
      <CatalogTile
        entry={catalogEntry("http")}
        onPick={onPick}
        availability={ready(HTTP_OFF)}
      />,
    );
    const tile = screen.getByTestId("catalog-tile-http");
    expect(tile).toHaveAttribute("aria-disabled", "true");
    expect(tile).toHaveAttribute("tabindex", "0");
    expect(within(tile).getByText(HTTP_OFF.http.reason)).toBeVisible();
    expect(tile).not.toHaveTextContent(PROMISE);
    fireEvent.click(tile);
    fireEvent.keyDown(tile, { key: "Enter" });
    fireEvent.keyDown(tile, { key: " " });
    expect(onPick).not.toHaveBeenCalled();
  });

  it("picks a tile whose source the server runs", () => {
    const onPick = vi.fn();
    render(
      <CatalogTile
        entry={catalogEntry("http")}
        onPick={onPick}
        availability={ready()}
      />,
    );
    const tile = screen.getByTestId("catalog-tile-http");
    expect(tile).not.toHaveAttribute("aria-disabled");
    fireEvent.click(tile);
    expect(onPick).toHaveBeenCalledTimes(1);
  });
});
