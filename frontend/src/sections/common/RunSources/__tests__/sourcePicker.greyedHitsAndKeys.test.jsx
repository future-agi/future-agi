// The New experiment picker with greyed ways to run on screen (the held
// cards and tiles, and the sources the server refuses):
// - search Enter picks the first hit that can be picked, past greyed card
//   hits, and says so in plain words when nothing can be picked;
// - the number keys of the held cards pick nothing and pass through, so
//   other listeners still get them;
// - every greyed tile follows one tab order: focusable so its reason can be
//   read, aria-disabled, never picked.
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
import { availabilityOfReport } from "../runSourceAvailability";

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

const YES = { available: true, reason: null };
const HTTP_OFF = {
  available: false,
  reason:
    "HTTP endpoint experiments are unavailable on this server. Contact your administrator.",
};
// What GET /api/capabilities/ answers in `experiment_run_sources`.
const ready = (over = {}) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: {
        available: false,
        reason: "A2A experiments are not built in this release.",
      },
      mcp_tool: {
        available: false,
        reason: "MCP tool experiments are not built in this release.",
      },
      ...over,
    },
  });

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
const noneLine = () => screen.queryByTestId("source-search-none-pickable");

describe("step-1 search Enter skips greyed hits", () => {
  it.each([
    ["ur", "code"],
    ["es", "harness"],
  ])(
    "%s finds %s greyed and the HTTP endpoint; Enter adds the HTTP endpoint run",
    (query, heldId) => {
      const { onAddCard, onAddR4 } = mount();
      search(query);
      const held = within(
        screen.getByTestId(`search-card-${heldId}`),
      ).getByTestId(`source-card-${heldId}`);
      expect(held).toHaveAttribute("aria-disabled", "true");
      // The tile Enter picks is the one marked as first.
      const http = screen.getByTestId("catalog-tile-http");
      expect(http).not.toHaveAttribute("aria-disabled");
      expect(http).toHaveAttribute("data-first", "1");
      fireEvent.keyDown(searchBox(), { key: "Enter" });
      expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
      expect(onAddR4).not.toHaveBeenCalled();
      expect(noneLine()).toBeNull();
    },
  );

  it("says nothing can be picked when every hit is greyed, and adds nothing", () => {
    const callbacks = mount();
    search("sdk");
    expect(
      within(screen.getByTestId("search-card-code")).getByTestId(
        "source-card-code",
      ),
    ).toHaveAttribute("aria-disabled", "true");
    expect(noneLine()).toBeNull();
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expectNothingAdded(callbacks);
    const line = noneLine();
    expect(line).toBeVisible();
    expect(line).toHaveAttribute("role", "status");
    expect(line).toHaveTextContent(
      "None of these can be picked here. Try Prompt, Agent graph or HTTP endpoint.",
    );
    // A new query clears the line until its own Enter.
    search("sd");
    expect(noneLine()).toBeNull();
  });

  it("names only the ways left when the server refuses the HTTP endpoint", () => {
    harness.availability = ready({ http: HTTP_OFF });
    const callbacks = mount();
    search("ur");
    expect(screen.getByTestId("catalog-tile-http")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expectNothingAdded(callbacks);
    expect(noneLine()).toHaveTextContent(
      "None of these can be picked here. Try Prompt or Agent graph.",
    );
  });

  it("still picks the first offered card when one matches", () => {
    const { onAddCard } = mount();
    search("agent");
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith(
      "agent",
      expect.anything(),
    );
    expect(noneLine()).toBeNull();
  });
});

describe("number keys of the held cards", () => {
  it.each(["4", "5", "6"])("%s picks nothing and is not swallowed", (key) => {
    const callbacks = mount();
    const seen = [];
    const later = (e) => seen.push(e.defaultPrevented);
    document.addEventListener("keydown", later);
    const passed = fireEvent.keyDown(screen.getByRole("dialog"), { key });
    document.removeEventListener("keydown", later);
    expect(passed).toBe(true);
    expect(seen).toEqual([false]);
    expectNothingAdded(callbacks);
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "1",
    );
  });

  it("1 still adds a prompt run and is handled by the picker", () => {
    const { onAddCard } = mount();
    const passed = fireEvent.keyDown(screen.getByRole("dialog"), { key: "1" });
    expect(passed).toBe(false);
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith(
      "prompt",
      screen.getByTestId("source-card-prompt"),
    );
  });
});

describe("greyed tiles share one tab order", () => {
  it.each([
    ["a2a", "A2A agent card"],
    ["mcp_tool", "MCP server tool"],
    ["traces", "Traces I already have"],
    ["upload", "Upload results"],
  ])(
    "%s is focusable so its reason can be read, aria-disabled, and adds nothing",
    (id, name) => {
      harness.url = { src: "running" };
      const callbacks = mount();
      const tile = screen.getByTestId(`catalog-tile-${id}`);
      expect(tile).toHaveAttribute("tabindex", "0");
      expect(tile).toHaveAttribute("aria-disabled", "true");
      expect(tile).toHaveAccessibleName(name);
      act(() => tile.focus());
      expect(tile).toHaveFocus();
      fireEvent.click(tile);
      fireEvent.keyDown(tile, { key: "Enter" });
      fireEvent.keyDown(tile, { key: " " });
      expectNothingAdded(callbacks);
    },
  );

  it("keeps the HTTP endpoint tile in the same tab order, and pickable", () => {
    harness.url = { src: "running" };
    const { onAddCard } = mount();
    const tile = screen.getByTestId("catalog-tile-http");
    expect(tile).toHaveAttribute("tabindex", "0");
    expect(tile).not.toHaveAttribute("aria-disabled");
    fireEvent.keyDown(tile, { key: "Enter" });
    expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
  });
});
