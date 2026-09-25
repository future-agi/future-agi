import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import TraceTable from "../TraceTable";

const row = (id) => ({
  id,
  scenario: `Scenario ${id}`,
  goal: `Goal ${id}`,
  status: "passed",
  critical: false,
  csat: 5,
  turns: 3,
  latencyMs: 100,
  tokens: null,
  durationMs: 1000,
  personaDetails: { name: "P", voice: null, age: null, traits: [] },
  evalResults: [],
});
const group = (label, ids) => ({
  label,
  count: ids.length,
  rows: ids.map(row),
  agg: {},
});
const PAGE1 = [group("A", ["a1", "a2"]), group("B", ["b1", "b2"])];
// Group-by-goal repeats labels across pages.
const PAGE2 = [group("A", ["a3", "a4"]), group("B", ["b3", "b4"])];

const table = (props) => (
  <TraceTable groups={PAGE1} evals={[]} onOpen={vi.fn()} {...props} />
);
const activeRow = () => document.querySelector('tr[aria-selected="true"]');

// The open call's row must end up on screen — groups start collapsed, and the
// row only mounts once its group expands, so the scroll has to wait for it.
describe("TraceTable — the open call's row scrolls into view", () => {
  let scrollIntoView;
  beforeEach(() => {
    scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
  });

  it("when a call is first opened in a group that starts collapsed", () => {
    render(table({ activeCallId: "a2" }));
    expect(activeRow()).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("when a step lands in another, collapsed group", () => {
    const { rerender } = render(table({ activeCallId: "a2" }));
    scrollIntoView.mockClear();

    rerender(table({ activeCallId: "b1" }));

    expect(activeRow()).toHaveTextContent("Scenario b1");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("when a step crosses a page into a group that is still collapsed", () => {
    const { rerender } = render(table({ activeCallId: "a2" }));
    scrollIntoView.mockClear();

    rerender(table({ groups: PAGE2, activeCallId: "b3" }));

    expect(activeRow()).toHaveTextContent("Scenario b3");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("but not again on a live refresh of the same call", () => {
    const { rerender } = render(table({ activeCallId: "a2" }));
    scrollIntoView.mockClear();

    for (let i = 0; i < 3; i += 1) {
      rerender(
        table({ groups: PAGE1.map((g) => ({ ...g })), activeCallId: "a2" }),
      );
    }

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("and a group the user collapses stays collapsed across refreshes", () => {
    const { rerender } = render(table({ activeCallId: "a2" }));

    fireEvent.click(screen.getAllByText("A")[0]);
    rerender(
      table({ groups: PAGE1.map((g) => ({ ...g })), activeCallId: "a2" }),
    );

    expect(activeRow()).toBeNull();
  });
});
