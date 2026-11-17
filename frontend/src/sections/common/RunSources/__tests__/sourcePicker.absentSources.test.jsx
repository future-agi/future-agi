import React from "react";
import { act, cleanup, render, screen } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SourcePicker from "../SourcePicker";
import { usePickerUi } from "../pickerUi";
import { availabilityOfReport } from "../runSourceAvailability";

const harness = vi.hoisted(() => ({
  url: {},
  setUrl: vi.fn(),
  cards: null,
  running: false,
  kinds: true,
  launch: true,
  availability: null,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) =>
    !harness.launch ||
    (id === "exp.new.runningSources" ? harness.running : harness.kinds),
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
vi.mock("src/components/iconify", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
vi.mock("src/sections/common/ux/KeyHintTooltip", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ children }) => children,
}));
/* eslint-enable react/prop-types */
// Capture callbacks to exercise stale/forged child selections. No source API runs.
vi.mock("../SourceCards", () => ({
  default: (props) => {
    harness.cards = props;
    return <div data-testid="step-one" />;
  },
  ConnectedNowStrip: () => null,
}));
vi.mock("../CatalogGrid", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="running-catalog" />,
}));
vi.mock("../SourceConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="source-config" />,
}));

// The server's report (GET /api/capabilities/ experiment_run_sources).
const YES = { available: true, reason: null };
const NO = { available: false, reason: "Not on this server." };
const report = (running) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: running ? YES : NO,
      a2a: running ? YES : NO,
      mcp_tool: running ? YES : NO,
    },
  });

beforeEach(() => {
  vi.useFakeTimers();
  Object.assign(harness, {
    url: {},
    cards: null,
    running: false,
    kinds: true,
    launch: true,
    // The server can run every source: only the launch gate is under test.
    availability: report(true),
  });
  harness.setUrl.mockClear();
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
    <SourcePicker datasetKind="rows" onAddCard={onAddCard} onAddR4={onAddR4} />,
  );
  act(() => vi.advanceTimersByTime(500));
  return { onAddCard, onAddR4 };
}

describe("SourcePicker enforces runningSources at every selection boundary", () => {
  it.each([
    "src=running",
    "source=running",
    "source=http",
    "entry=http",
    "entry=a2a",
    "entry=mcp_tool",
  ])("does not mount a catalog/config or add a form for %s", (query) => {
    harness.url = Object.fromEntries(
      new URLSearchParams(`new_experiment=1&${query}`),
    );
    const { onAddCard, onAddR4 } = mount();
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "1",
    );
    expect(screen.queryByTestId("running-catalog")).toBeNull();
    expect(screen.queryByTestId("source-config")).toBeNull();
    // onAddCard is the real boundary that appends HTTP/A2A/MCP config forms.
    expect(onAddCard).not.toHaveBeenCalled();
    expect(onAddR4).not.toHaveBeenCalled();
  });

  it("refuses stale child callbacks before either URL writes or form creation", () => {
    const { onAddCard, onAddR4 } = mount();
    act(() => {
      harness.cards.onPickCard("running");
      for (const id of ["http", "a2a", "mcp_tool"])
        harness.cards.onPickEntry(id);
    });
    expect(harness.setUrl).not.toHaveBeenCalled();
    expect(onAddCard).not.toHaveBeenCalled();
    expect(onAddR4).not.toHaveBeenCalled();
  });

  it("preserves prompt and qualified agent selection", () => {
    const { onAddCard } = mount();
    act(() => {
      harness.cards.onPickCard("prompt");
      harness.cards.onPickCard("agent");
    });
    expect(onAddCard.mock.calls.map(([kind]) => kind)).toEqual([
      "prompt",
      "agent",
    ]);
  });

  it("also contains running aliases with F01 ON and F02 OFF", () => {
    harness.kinds = false;
    harness.url = { new_experiment: "1", source: "http" };
    const { onAddCard } = mount();
    expect(screen.getByTestId("source-picker")).toHaveAttribute(
      "data-level",
      "1",
    );
    act(() => harness.cards.onPickCard("prompt"));
    expect(onAddCard.mock.calls.map(([kind]) => kind)).toEqual(["prompt"]);
  });

  it("refuses child callbacks for sources the server cannot run, with every launch surface shown", () => {
    harness.running = true;
    harness.availability = report(false);
    const { onAddCard, onAddR4 } = mount();
    act(() => {
      harness.cards.onPickCard("running");
      for (const id of ["http", "a2a", "mcp_tool"])
        harness.cards.onPickEntry(id);
    });
    expect(harness.setUrl).not.toHaveBeenCalled();
    expect(onAddCard).not.toHaveBeenCalled();
    expect(onAddR4).not.toHaveBeenCalled();
  });

  it("hands the same report to step 1", () => {
    mount();
    expect(harness.cards.availability).toBe(harness.availability);
  });

  it.each(["non-launch", "all-shown"])(
    "opens the %s endpoint link and adds its run where the server can run it",
    (mode) => {
      harness.launch = mode !== "non-launch";
      harness.running = mode === "all-shown";
      harness.url = { new_experiment: "1", entry: "http" };
      const { onAddCard } = mount();
      expect(screen.getByTestId("running-catalog")).toBeInTheDocument();
      expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
    },
  );

  it.each(["non-launch", "all-shown"])(
    "opens the %s endpoint link on its greyed list where the server cannot run it",
    (mode) => {
      harness.launch = mode !== "non-launch";
      harness.running = mode === "all-shown";
      harness.availability = report(false);
      harness.url = { new_experiment: "1", entry: "http" };
      const { onAddCard } = mount();
      // The list stays in view (its tiles greyed with the server's reason);
      // nothing is added.
      expect(screen.getByTestId("source-picker")).toHaveAttribute(
        "data-level",
        "2",
      );
      expect(screen.getByTestId("running-catalog")).toBeInTheDocument();
      expect(onAddCard).not.toHaveBeenCalled();
    },
  );
});
