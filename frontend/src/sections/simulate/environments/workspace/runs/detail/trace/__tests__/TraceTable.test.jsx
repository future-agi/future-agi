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

// Scrolling a long run must keep the column names and the current group's
// scores on screen, or the eval columns become unlabeled numbers.
describe("TraceTable — sticky header and group rows", () => {
  const position = (el) => window.getComputedStyle(el).position;

  it("pins every column header cell", () => {
    render(table());
    const heads = document.querySelectorAll("thead th");
    expect(heads.length).toBeGreaterThan(0);
    heads.forEach((th) => expect(position(th)).toBe("sticky"));
  });

  it("pins every group row just under the header", () => {
    render(table({ activeCallId: "a1" }));
    ["A", "B"].forEach((label) => {
      const td = screen.getByText(label).closest("td");
      expect(position(td)).toBe("sticky");
      expect(window.getComputedStyle(td).top).toBe("44px");
    });
  });

  it("scrolls inside the table box, not the page", () => {
    render(table());
    const scroller = document.querySelector("table").parentElement;
    expect(window.getComputedStyle(scroller).getPropertyValue("overflow")).toBe(
      "auto",
    );
  });
});

describe("TraceTable — call status column", () => {
  const withStatus = (id, executionStatus) => ({ ...row(id), executionStatus });
  const statusGroups = [
    {
      label: "A",
      count: 3,
      rows: [
        withStatus("s1", "pending"),
        withStatus("s2", "ongoing"),
        withStatus("s3", "completed"),
      ],
      agg: {},
    },
  ];

  it("sits between Run details and Persona", () => {
    render(table());
    const heads = [...document.querySelectorAll("thead th")].map((th) =>
      th.textContent.trim(),
    );
    expect(heads.slice(0, 3)).toEqual(["Run details", "Status", "Persona"]);
  });

  it("shows each call's lifecycle status", () => {
    render(table({ groups: statusGroups, activeCallId: "s1" }));
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("summarises how many calls in the group have completed", () => {
    render(table({ groups: statusGroups }));
    expect(screen.getByText("1/3 completed")).toBeInTheDocument();
  });

  it("shows no completed count while only some of the group's calls are loaded", () => {
    render(table({ groups: [{ ...statusGroups[0], count: 25 }] }));
    expect(screen.queryByText(/completed$/)).toBeNull();
  });
});

describe("TraceTable — group row grid", () => {
  it("keeps the column dividers on the group row", () => {
    render(table());
    const cells = screen.getByText("A").closest("tr").querySelectorAll("td");
    expect(cells.length).toBeGreaterThan(1);
    [...cells].slice(1).forEach((td) => {
      expect(window.getComputedStyle(td).borderLeftStyle).toBe("solid");
    });
  });
});

describe("TraceTable — eval cells without a score", () => {
  const evals = [{ id: "e1", name: "Tone" }];
  const oneCall = (overrides) => [
    {
      label: "A",
      count: 1,
      rows: [{ ...row("x1"), executionStatus: "completed", ...overrides }],
      agg: {},
    },
  ];
  const renderCell = (overrides) =>
    render(
      <TraceTable
        groups={oneCall(overrides)}
        evals={evals}
        onOpen={vi.fn()}
        activeCallId="x1"
      />,
    );
  const evalCell = () =>
    document.querySelector('tr[aria-selected="true"]').lastElementChild;
  // The eval cell is the call row's last cell; other cells can show "-" too.
  const hoverText = async (text) => {
    const cell = document.querySelector('tr[aria-selected="true"]').lastElementChild;
    fireEvent.mouseOver(within(cell).getByText(text));
    return screen.findByRole("tooltip");
  };

  it("shows N/A for a finished call with no result", async () => {
    renderCell({ evalResults: [] });
    expect(await hoverText("N/A")).toHaveTextContent(
      "Not applicable to this scenario",
    );
  });

  it.each(["pending", "queued", "ongoing", "analyzing"])(
    "shows a loading skeleton while the call is %s and has no result",
    (executionStatus) => {
      renderCell({ evalResults: [], executionStatus });
      const cell = document.querySelector('tr[aria-selected="true"]').lastElementChild;
      expect(cell.querySelector(".MuiSkeleton-root")).not.toBeNull();
      expect(within(cell).queryByText("N/A")).toBeNull();
      expect(within(cell).queryByText("-")).toBeNull();
    },
  );

  it.each([
    ["cancelled", "Not evaluated: the call was cancelled"],
    ["failed", "Not evaluated: the call failed"],
  ])("says a %s call was never evaluated, instead of N/A", async (executionStatus, tip) => {
    renderCell({ evalResults: [], executionStatus });
    expect(within(evalCell()).queryByText("N/A")).toBeNull();
    expect(await hoverText("-")).toHaveTextContent(tip);
  });

  it("explains a failed eval with its reason", async () => {
    renderCell({
      evalResults: [{ id: "e1", score: null, status: "failed", reason: "Timed out" }],
    });
    expect(await hoverText("-")).toHaveTextContent("Evaluation failed: Timed out");
  });

  it("explains a skipped eval with its reason", async () => {
    renderCell({
      evalResults: [
        { id: "e1", score: null, status: "skipped", reason: "No transcript data available" },
      ],
    });
    expect(await hoverText("-")).toHaveTextContent(
      "Skipped: No transcript data available",
    );
  });

  it("shows a loading skeleton for an eval the backend marks pending", () => {
    renderCell({ evalResults: [{ id: "e1", score: null, status: "pending" }] });
    expect(evalCell().querySelector(".MuiSkeleton-root")).not.toBeNull();
  });

  it("explains an eval with status error like a failed one", async () => {
    renderCell({
      evalResults: [{ id: "e1", score: null, status: "error", reason: "Boom" }],
    });
    expect(await hoverText("-")).toHaveTextContent("Evaluation failed: Boom");
  });

  it("shows N/A for a completed result with neither a score nor a label", () => {
    renderCell({
      evalResults: [{ id: "e1", score: null, label: null, status: "completed" }],
    });
    expect(within(evalCell()).getByText("N/A")).toBeInTheDocument();
  });

  it("shows a label-only result as its label, with no tooltip", () => {
    renderCell({ evalResults: [{ id: "e1", score: null, label: "Yes" }] });
    const label = within(evalCell()).getByText("Yes");
    fireEvent.mouseOver(label);
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(within(evalCell()).queryByText("N/A")).toBeNull();
  });

  it("still shows the score of a stored result that carries no status", () => {
    renderCell({ evalResults: [{ id: "e1", score: 0.8, label: null }] });
    expect(within(evalCell()).getByText("80%")).toBeInTheDocument();
  });
});

describe("TraceTable — metric cells while the call runs", () => {
  const empty = { csat: null, turns: null, latencyMs: null, tokens: null };
  const renderCall = (executionStatus) =>
    render(
      <TraceTable
        groups={[
          {
            label: "A",
            count: 1,
            rows: [{ ...row("m1"), ...empty, executionStatus }],
            agg: {},
          },
        ]}
        evals={[]}
        onOpen={vi.fn()}
        activeCallId="m1"
      />,
    );
  // CSAT, Turns, Latency, Tokens are the four cells before the evals.
  const metricCells = () =>
    [...document.querySelector('tr[aria-selected="true"]').children].slice(-4);

  it("shows a skeleton in each empty metric cell while the call is live", () => {
    renderCall("ongoing");
    metricCells().forEach((cell) => {
      expect(cell.querySelector(".MuiSkeleton-root")).not.toBeNull();
      expect(cell).not.toHaveTextContent("-");
    });
  });

  it("shows a dash for a metric still missing once the call has finished", () => {
    renderCall("completed");
    metricCells().forEach((cell) => {
      expect(cell.querySelector(".MuiSkeleton-root")).toBeNull();
      expect(cell).toHaveTextContent("-");
    });
  });
});
