import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

import { GatewayGrid } from "../../../components/frame";
import { ruleColumns } from "../rule-columns";
import {
  CELL_PADDING_PX,
  CHIP_CHROME_PX,
  chipNeeds,
  columnOf,
  cutHeaders,
  gridBudget1440,
  minimumGridWidth,
  minimumWidth,
} from "../../../logs/__tests__/gridFit";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

// The widest value each column shows, with the cell's padding. Inter 400
// 12 px advance widths from the bundled font file, no kerning, rounded up
// plus 1 px: "Future AGI" 62, "Before LLM" 67; the action chip's label at
// Inter 400 11 px, "Block" 30 (the widest of Block, Warn, Mask and Log).
const CELL_NEEDS = {
  kind: 62 + CELL_PADDING_PX,
  stage: 67 + CELL_PADDING_PX,
  action: 30 + CHIP_CHROME_PX + CELL_PADDING_PX,
};

// The On column's controls while guardrails are on: the small Set up button
// ("Set up" 35 px at Inter 500 11 px, 10 px padding and a 1 px border each
// side) and the small switch (40 px, theme/overrides/components/switch.js).
const SET_UP_BUTTON_PX = 35 + 2 * 10 + 2;
const SMALL_SWITCH_PX = 40;

// The Rules grid as GatewayGrid mounts it, its default column def applied.
function mounted(enforced) {
  const columnDefs = ruleColumns({
    onToggle: () => {},
    onSetUp: () => {},
    busy: false,
    enforced,
  });
  render(<GatewayGrid rowData={[{ id: "rule" }]} columnDefs={columnDefs} />);
  return grid.props;
}

// B-01: at 1024 and 1280 px the Rules grid was wider than the page, so the
// On/Status switch sat past the right edge.
describe("Guardrails Rules grid width", () => {
  it("keeps the On/Status column pinned to the right edge, guardrails on or off", () => {
    [true, false].forEach((enforced) => {
      const status = columnOf(mounted(enforced), "status");
      expect(status.pinned, `enforced ${enforced}`).toBe("right");
    });
  });

  it("keeps every header whole, sorted or not, and fits at 1440 px, guardrails on or off", () => {
    [true, false].forEach((enforced) => {
      const props = mounted(enforced);
      expect(cutHeaders(props), `enforced ${enforced}`).toEqual([]);
      expect(minimumGridWidth(props)).toBeLessThanOrEqual(gridBudget1440());
    });
  });

  it("keeps each column's widest value whole", () => {
    const props = mounted(true);
    Object.entries(CELL_NEEDS).forEach(([id, needs]) => {
      expect(minimumWidth(columnOf(props, id)), id).toBeGreaterThanOrEqual(
        needs,
      );
    });
  });

  it("leaves the On column room for Set up and the switch, and Status room for Not enforced", () => {
    const on = minimumWidth(columnOf(mounted(true), "status"));
    expect(on).toBeGreaterThanOrEqual(SET_UP_BUTTON_PX + CELL_PADDING_PX);
    expect(on).toBeGreaterThanOrEqual(SMALL_SWITCH_PX + CELL_PADDING_PX);
    const status = minimumWidth(columnOf(mounted(false), "status"));
    expect(status).toBeGreaterThanOrEqual(chipNeeds("Not enforced"));
  });
});
