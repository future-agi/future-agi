import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

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

// An empty cell whose value is still coming loads; one that will never come
// shows "-". Default columns: details, persona, CSAT, turns, latency, tokens,
// then one per eval.
describe("TraceTable — cells whose value is still coming", () => {
  const EVALS = [
    { id: "e1", name: "Refund" },
    { id: "e2", name: "Tone" },
  ];
  const renderRow = (overrides) => {
    const call = { ...row("a1"), ...overrides };
    render(
      <TraceTable
        groups={[{ label: "A", count: 1, rows: [call], agg: {} }]}
        evals={EVALS}
        onOpen={vi.fn()}
        activeCallId="a1"
      />,
    );
    const [, , csat, turns, latency, tokens, e1, e2] =
      activeRow().querySelectorAll("td");
    return { csat, turns, latency, tokens, e1, e2 };
  };
  const loading = (cell) => cell.querySelector(".MuiSkeleton-root") !== null;

  it("loads an unscored eval while the call is scoring, next to a scored one", () => {
    const { e1, e2 } = renderRow({
      evalResults: [{ id: "e1", name: "Refund", score: 0.8, passed: true }],
      pending: { evals: true, csat: false, metrics: false },
    });
    expect(loading(e1)).toBe(false);
    expect(e1).toHaveTextContent("80%");
    expect(loading(e2)).toBe(true);
    expect(e2).not.toHaveTextContent("-");
  });

  it("loads CSAT while it is scoring", () => {
    const { csat } = renderRow({
      csat: null,
      pending: { evals: false, csat: true, metrics: false },
    });
    expect(loading(csat)).toBe(true);
  });

  it("loads turns and latency while the call runs, never tokens", () => {
    const { turns, latency, tokens } = renderRow({
      turns: null,
      latencyMs: null,
      tokens: null,
      pending: { evals: true, csat: true, metrics: true },
    });
    expect(loading(turns)).toBe(true);
    expect(loading(latency)).toBe(true);
    expect(loading(tokens)).toBe(false);
    expect(tokens).toHaveTextContent("-");
  });

  it("shows a present value even while it is marked pending", () => {
    const { csat, turns, latency } = renderRow({
      csat: 7.5,
      turns: 4,
      latencyMs: 250,
      pending: { evals: true, csat: true, metrics: true },
    });
    expect(loading(csat)).toBe(false);
    expect(csat).toHaveTextContent("7.5");
    expect(loading(turns)).toBe(false);
    expect(turns).toHaveTextContent("4");
    expect(loading(latency)).toBe(false);
    expect(latency).toHaveTextContent("250ms");
  });

  it.each([
    ["nothing is pending", { evals: false, csat: false, metrics: false }],
    ["the row has no pending flags", undefined],
  ])('shows "-" when %s', (_, pending) => {
    const cells = renderRow({
      csat: null,
      turns: null,
      latencyMs: null,
      tokens: null,
      pending,
    });
    Object.values(cells).forEach((cell) => {
      expect(loading(cell)).toBe(false);
      expect(cell).toHaveTextContent("-");
    });
  });
});

describe("TraceTable — cells whose scoring failed", () => {
  const EVALS = [
    { id: "e1", name: "Refund" },
    { id: "e2", name: "Tone" },
  ];
  const renderRow = (overrides) => {
    const call = { ...row("a1"), ...overrides };
    render(
      <TraceTable
        groups={[{ label: "A", count: 1, rows: [call], agg: {} }]}
        evals={EVALS}
        onOpen={vi.fn()}
        activeCallId="a1"
      />,
    );
    const [, , csat, , , , e1, e2] = activeRow().querySelectorAll("td");
    return { csat, e1, e2 };
  };

  it("shows Error on a failed eval, with its reason on hover", async () => {
    const { e1, e2 } = renderRow({
      evalResults: [
        {
          id: "e1",
          name: "Refund",
          score: null,
          errored: true,
          reason: "Judge timed out",
        },
        { id: "e2", name: "Tone", score: 0.8, passed: true },
      ],
      pending: { evals: true, csat: false, metrics: false },
    });
    expect(e1).toHaveTextContent("Error");
    expect(e1.querySelector(".MuiSkeleton-root")).toBeNull();
    expect(e2).toHaveTextContent("80%");
    fireEvent.mouseOver(within(e1).getByText("Error"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Judge timed out",
    );
  });

  it("shows Error on a failed CSAT, with its reason on hover", async () => {
    const { csat } = renderRow({
      csat: null,
      csatFailed: true,
      csatError: "CSAT scorer returned no result",
      pending: { evals: false, csat: false, metrics: false },
    });
    expect(csat).toHaveTextContent("Error");
    fireEvent.mouseOver(within(csat).getByText("Error"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "CSAT scorer returned no result",
    );
  });

  it("keeps a CSAT score even if an older attempt failed", () => {
    const { csat } = renderRow({ csat: 7.5, csatFailed: false });
    expect(csat).not.toHaveTextContent("Error");
  });
});
