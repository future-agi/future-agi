import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenarioTable from "../ScenarioTable";

const env = MOCK_WORLD;
const rows = generatedPool(env).slice(0, 3);

const renderTable = (props = {}) => {
  const onSelectionChange = vi.fn();
  render(
    <ScenarioTable
      rows={rows}
      onEdit={() => {}}
      onRemove={() => {}}
      selectedIds={[]}
      onSelectionChange={onSelectionChange}
      {...props}
    />,
  );
  // getAllByRole("checkbox")[0] is the header select-all; the rest are rows.
  return { onSelectionChange, boxes: screen.getAllByRole("checkbox") };
};

describe("ScenarioTable — bulk-action selection", () => {
  it("opens with nothing selected — checkboxes are a bulk affordance, not a run gate", () => {
    const { boxes } = renderTable();
    expect(boxes).toHaveLength(rows.length + 1);
    boxes.forEach((b) => expect(b).not.toBeChecked());
  });

  it("commits the toggled row id through onSelectionChange", () => {
    const { onSelectionChange, boxes } = renderTable();
    fireEvent.click(boxes[1]);
    expect(onSelectionChange).toHaveBeenCalledWith([rows[0].id]);
  });

  it("selects every id when the header checkbox is clicked from empty", () => {
    const { onSelectionChange, boxes } = renderTable();
    fireEvent.click(boxes[0]);
    expect(onSelectionChange).toHaveBeenCalledWith(rows.map((r) => r.id));
  });

  it("reflects a controlled selection: the row is checked and the header is indeterminate", () => {
    renderTable({ selectedIds: [rows[0].id] });
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[1]).toBeChecked();
    expect(boxes[0]).toHaveAttribute("data-indeterminate", "true");
  });
});

describe("ScenarioTable — pinned checkbox column", () => {
  it("keeps the header and row checkboxes pinned to the left edge while the table scrolls sideways", () => {
    const { boxes } = renderTable();
    boxes.forEach((b) => {
      const cell = b.closest("th, td");
      expect(getComputedStyle(cell).position).toBe("sticky");
      expect(getComputedStyle(cell).left).toBe("0px");
    });
  });
});

describe("ScenarioTable columns", () => {
  it("reads select, #, Scenario, Persona, Tags, Sub-goals, Brief, Passes when", () => {
    renderTable();
    const labels = screen
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent.trim())
      .filter(Boolean);
    expect(labels).toEqual([
      "#",
      "Scenario",
      "Persona",
      "Tags",
      "Sub-goals",
      "Brief",
      "Passes when",
    ]);
    expect(screen.queryByText("Situation")).toBeNull();
    expect(screen.queryByText("Ideal outcome")).toBeNull();
  });

  it("keeps the Scenario column pinned beside the number while the table scrolls sideways", () => {
    renderTable();
    const header = screen.getByRole("columnheader", { name: "Scenario" });
    expect(getComputedStyle(header).position).toBe("sticky");
    expect(getComputedStyle(header).left).toBe("92px");
    const cell = screen.getByText(rows[0].name || rows[0].title).closest("td");
    expect(getComputedStyle(cell).position).toBe("sticky");
    expect(getComputedStyle(cell).left).toBe("92px");
  });
});

describe("ScenarioTable — hide group", () => {
  it("calls onHideGroup with the section id when the group's eye is clicked", () => {
    const onHideGroup = vi.fn();
    const groups = [{ id: "g1", label: "Group one", rows }];
    render(
      <ScenarioTable
        rows={rows}
        groups={groups}
        onEdit={() => {}}
        onRemove={() => {}}
        onHideGroup={onHideGroup}
        selectedIds={[]}
        onSelectionChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Hide this group" }));
    expect(onHideGroup).toHaveBeenCalledWith("g1");
  });
});

describe("ScenarioTable — opening a row", () => {
  const served = [
    { ...rows[0], id: "a", number: 1 },
    { ...rows[1], id: "b", number: 2 },
    { ...rows[2], id: "c", number: 3 },
  ];

  it("opens a row on click, but not from its checkbox or its actions", () => {
    const onOpen = vi.fn();
    const { boxes } = renderTable({ rows: served, onOpen });
    fireEvent.click(screen.getByText(served[1].name || served[1].title));
    expect(onOpen).toHaveBeenCalledWith(served[1]);

    onOpen.mockClear();
    fireEvent.click(boxes[1]);
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Edit (?!tags$)/ })[0],
    );
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("opens a focused row with Enter", () => {
    const onOpen = vi.fn();
    renderTable({ rows: served, onOpen });
    const row = screen.getByRole("row", {
      name: `Open ${served[0].name || served[0].title}`,
    });
    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith(served[0]);
  });
});

describe("ScenarioTable actors", () => {
  it("names who else is in a scenario under its summary, as the row resolves them", () => {
    renderTable({
      rows: [
        {
          ...rows[0],
          callActors: [{ id: "friend", name: "Rowan", instruction: "" }],
        },
        rows[1],
      ],
    });
    expect(screen.getAllByText("With Rowan")).toHaveLength(1);
    expect(screen.getByText("With Rowan").closest("td")).toHaveTextContent(
      rows[0].name || rows[0].title,
    );
  });
});

describe("ScenarioTable source chip", () => {
  const built = {
    id: "s1",
    label: "v1",
    origin: "authoring",
    createdAt: "2026-11-20T10:00:00Z",
    createdBy: "Asha Rao",
  };

  it("marks each row Derived when the build wrote the only scenario version", () => {
    renderTable({ batch: { version: built, latest: true, only: true } });
    expect(screen.getAllByText("Derived")).toHaveLength(rows.length);
  });

  it("leaves the chip out when later versions changed the suite", () => {
    renderTable({ batch: { version: built, latest: true, only: false } });
    expect(screen.queryByText("Derived")).toBeNull();
  });

  it("leaves the chip out on an unsaved addition", () => {
    renderTable({
      rows: [{ ...rows[0], draftChange: "added" }, rows[1]],
      batch: { version: built, latest: true, only: true },
    });
    expect(screen.getAllByText("Derived")).toHaveLength(1);
  });
});

describe("ScenarioTable batch row", () => {
  const version = {
    id: "s1",
    label: "v1",
    origin: "authoring",
    createdAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    createdBy: "Asha Rao",
  };
  const groups = [{ id: "g1", label: "Group one", rows }];

  it("names who built the scenarios, the latest chip, the source, when, the version and how many can run", () => {
    renderTable({
      groups,
      total: rows.length,
      batch: { version, latest: true, only: true },
    });
    expect(screen.getByText("Asha Rao built the environment")).toBeVisible();
    expect(screen.getByText("Latest")).toBeVisible();
    expect(
      screen.getByText(
        `Written from the agent · 2 hours ago · scenarios v1 · all ${rows.length} can run`,
      ),
    ).toBeVisible();
    expect(screen.getByText("Group one")).toBeVisible();
  });

  it("counts the scenarios that cannot run, and drops the Latest chip on an older version", () => {
    renderTable({
      rows: [
        { ...rows[0], doorProblem: { code: "x", message: "y" } },
        ...rows.slice(1),
      ],
      total: rows.length,
      batch: { version: { ...version, createdBy: null }, latest: false },
    });
    expect(screen.getByText("System built the environment")).toBeVisible();
    expect(screen.queryByText("Latest")).toBeNull();
    expect(
      screen.getByText(`${rows.length - 1} of ${rows.length} can run`, {
        exact: false,
      }),
    ).toBeVisible();
  });

  it("hides and shows its rows", () => {
    renderTable({
      groups,
      total: rows.length,
      batch: { version, latest: true, only: true },
    });
    const name = rows[0].name || rows[0].title;
    fireEvent.click(
      screen.getByRole("button", { name: `Hide ${rows.length}` }),
    );
    expect(screen.queryByText(name)).toBeNull();
    expect(screen.queryByText("Group one")).toBeNull();
    const show = screen.getByRole("button", { name: `Show ${rows.length}` });
    expect(show).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(show);
    expect(screen.getByText(name)).toBeVisible();
  });
});

describe("scenario row buttons", () => {
  it("name the scenario they act on", async () => {
    const { SCENARIOS_COPY } = await import("../scenarios.constants");
    expect(SCENARIOS_COPY.editNamed("refund_after_cancel")).toBe(
      "Edit refund_after_cancel",
    );
    expect(SCENARIOS_COPY.removeNamed("refund_after_cancel")).toBe(
      "Remove refund_after_cancel from this environment",
    );
  });
});
