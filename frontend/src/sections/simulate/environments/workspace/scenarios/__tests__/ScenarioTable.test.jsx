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
      env={env}
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

describe("ScenarioTable — hide group", () => {
  it("calls onHideGroup with the section id when the group's eye is clicked", () => {
    const onHideGroup = vi.fn();
    const groups = [{ id: "g1", label: "Group one", rows }];
    render(
      <ScenarioTable
        rows={rows}
        groups={groups}
        env={env}
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
