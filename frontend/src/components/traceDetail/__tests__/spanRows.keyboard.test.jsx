import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import TraceTreeV2 from "../TraceTreeV2";
import SpanTreeTimeline from "../SpanTreeTimeline";

// jsdom does not implement the browser's scrolling API.
const originalScroll = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "scrollIntoView",
);
beforeAll(() =>
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  }),
);
afterAll(() => {
  if (originalScroll)
    Object.defineProperty(Element.prototype, "scrollIntoView", originalScroll);
  else delete Element.prototype.scrollIntoView;
});

const START = "2026-03-29T10:00:00.000Z";
const spans = [
  {
    observation_span: {
      id: "root",
      name: "invoke_agent",
      observation_type: "agent",
      start_time: START,
      latency_ms: 1000,
      status: "OK",
    },
    children: [
      {
        observation_span: {
          id: "llm",
          name: "model request",
          observation_type: "llm",
          start_time: START,
          latency_ms: 400,
          status: "OK",
        },
        children: [],
      },
      {
        observation_span: {
          id: "tool",
          name: "search tool",
          observation_type: "tool",
          start_time: START,
          latency_ms: 200,
          status: "OK",
        },
        children: [],
      },
    ],
  },
];

const rowsOf = () => within(screen.getByRole("tree")).getAllByRole("treeitem");

// SW5-32: span rows in the Trace view and the Timeline view take the keyboard.
describe.each([
  ["Trace view", TraceTreeV2],
  ["Timeline view", SpanTreeTimeline],
])("%s span rows", (_, View) => {
  it("are tree items with one Tab stop on the selected span", () => {
    render(<View spans={spans} selectedSpanId="llm" onSelectSpan={vi.fn()} />);

    const rows = rowsOf();
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.tabIndex)).toEqual([-1, 0, -1]);
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    expect(rows[0]).toHaveAttribute("aria-selected", "false");
    expect(rows[0]).toHaveAttribute("aria-expanded", "true");
    expect(rows[1]).not.toHaveAttribute("aria-expanded");
  });

  it("move focus with the arrows and select with Enter or Space", () => {
    const onSelect = vi.fn();
    render(
      <View spans={spans} selectedSpanId="root" onSelectSpan={onSelect} />,
    );

    const rows = rowsOf();
    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: "ArrowDown" });
    expect(rows[1]).toHaveFocus();
    fireEvent.keyDown(rows[1], { key: "ArrowDown" });
    expect(rows[2]).toHaveFocus();
    fireEvent.keyDown(rows[2], { key: "ArrowDown" });
    expect(rows[2]).toHaveFocus();
    fireEvent.keyDown(rows[2], { key: "ArrowUp" });
    expect(rows[1]).toHaveFocus();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.keyDown(rows[1], { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith("llm");
    fireEvent.keyDown(rows[2], { key: " " });
    expect(onSelect).toHaveBeenLastCalledWith("tool");
  });

  it("expand and collapse with the right and left arrows; the chevron is labelled", () => {
    const onSelect = vi.fn();
    render(
      <View spans={spans} selectedSpanId="tool" onSelectSpan={onSelect} />,
    );

    const [root] = rowsOf();
    const chevron = screen.getByRole("button", { name: "Collapse" });
    // A key on the chevron stays with the chevron: it does not select.
    fireEvent.keyDown(chevron, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.keyDown(root, { key: "ArrowLeft" });
    expect(root).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
    // The selected span is hidden now, so Tab lands on the first row.
    expect(root.tabIndex).toBe(0);

    fireEvent.keyDown(root, { key: "ArrowRight" });
    expect(root).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("button", { name: "Collapse" }),
    ).toBeInTheDocument();
  });

  it("put the Tab stop on the first row when nothing is selected", () => {
    render(<View spans={spans} onSelectSpan={vi.fn()} />);

    expect(rowsOf().map((row) => row.tabIndex)).toEqual([0, -1, -1]);
  });
});
