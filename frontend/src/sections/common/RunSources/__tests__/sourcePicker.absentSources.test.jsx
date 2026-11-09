import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SourcePicker from "../SourcePicker";
import { usePickerUi } from "../pickerUi";

const harness = vi.hoisted(() => ({
  url: {},
  setUrl: vi.fn(),
  cards: null,
  running: false,
  kinds: true,
  launch: true,
}));
vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: (id) =>
    !harness.launch ||
    (id === "exp.new.runningSources" ? harness.running : harness.kinds),
}));
vi.mock("src/sections/common/ux/viewState", () => ({
  useViewUrlState: () => [harness.url, harness.setUrl],
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({
  default: () => {},
  openShortcutSheet: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/KeyHintTooltip", () => ({
  default: ({ children }) => children,
}));
// Capture callbacks to exercise stale/forged child selections. No source API runs.
vi.mock("../SourceCards", () => ({
  default: (props) => {
    harness.cards = props;
    return <div data-testid="step-one" />;
  },
  ConnectedNowStrip: () => null,
}));
vi.mock("../CatalogGrid", () => ({
  default: () => <div data-testid="running-catalog" />,
}));
vi.mock("../SourceConfig", () => ({
  default: () => <div data-testid="source-config" />,
}));

beforeEach(() => {
  vi.useFakeTimers();
  Object.assign(harness, {
    url: {},
    cards: null,
    running: false,
    kinds: true,
    launch: true,
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

  it.each(["non-launch", "all-shown"])(
    "preserves %s canonical entry behavior",
    (mode) => {
      harness.launch = mode !== "non-launch";
      harness.running = mode === "all-shown";
      harness.url = { new_experiment: "1", entry: "http" };
      const { onAddCard } = mount();
      expect(screen.getByTestId("running-catalog")).toBeInTheDocument();
      expect(onAddCard).toHaveBeenCalledExactlyOnceWith("http");
    },
  );
});
