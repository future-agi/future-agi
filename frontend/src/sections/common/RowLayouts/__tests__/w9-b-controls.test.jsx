import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  OP_TYPES,
  useChangeSetStore,
} from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import ControlButtons, { ButtonsEditor } from "../ControlButtons";
import {
  controlKinds,
  controlOp,
  controlsDisabledReason,
  defaultLayout,
} from "../layoutModel";

vi.mock("src/sections/common/ux/toast", () => ({
  notify: vi.fn(),
  default: vi.fn(),
}));
const { notify } = await import("src/sections/common/ux/toast");

const COLS = [
  { id: "c1", name: "customer_message", section: "input", editable: true },
  { id: "c3", name: "expected_reply", section: "expected", editable: true },
  { id: "o1", name: "answer", section: "outputs", editable: false },
];
const FIX = {
  id: "ctl_1",
  label: "Use the policy reply",
  kind: "set_field",
  column_id: "c3",
  value: "Refunds take 5-7 days.",
};
const LAYOUT = { id: "v1", name: "Refund review" };

describe("controlOp", () => {
  it("builds a pending update_cell with the layout as its source", () => {
    const r = controlOp(FIX, {
      rowId: "r12",
      rowLabel: "Row 12",
      columns: COLS,
      original: () => "old",
      layout: LAYOUT,
    });
    expect(r.op).toEqual({
      op: "update_cell",
      row_id: "r12",
      column_id: "c3",
      value: "Refunds take 5-7 days.",
      before: "old",
      source: { kind: "layout", layout_id: "v1", name: "Refund review" },
    });
    expect(r.toast).toBe(
      "Row 12 · expected_reply: Refunds take 5-7 days. · in pending changes",
    );
  });
  it("refuses a generated column", () => {
    const r = controlOp(
      { ...FIX, column_id: "o1" },
      { rowId: "r", rowLabel: "Row 1", columns: COLS, layout: LAYOUT },
    );
    expect(r.error).toBe("The column this button sets isn't in this dataset.");
  });
  it("set_status and add_tag ops follow W3 A's shapes", () => {
    const ctx = {
      rowId: "r12",
      rowLabel: "Row 12",
      columns: COLS,
      layout: LAYOUT,
      statusLabel: { needs_review: "Needs review" },
    };
    const s = controlOp(
      {
        id: "c",
        label: "x",
        kind: "set_status",
        status: "needs_review",
        reason: "Expected is wrong",
      },
      ctx,
    );
    expect(s.op).toMatchObject({
      op: "set_status",
      row_ids: ["r12"],
      status: "needs_review",
      reason: "Expected is wrong",
    });
    expect(s.toast).toBe(
      "Row 12 · Needs review: Expected is wrong · in pending changes",
    );
    const t = controlOp(
      { id: "c", label: "x", kind: "add_tag", tag: "wrong-expected" },
      ctx,
    );
    expect(t.op).toMatchObject({
      op: "set_tags",
      row_ids: ["r12"],
      add: ["wrong-expected"],
      remove: [],
    });
  });
  it("says why buttons can't run", () => {
    expect(controlsDisabledReason({ viewer: true })).toBe(
      "Viewers can't change rows",
    );
    expect(controlsDisabledReason({ asOfVersion: 5 })).toBe(
      "This is v5, read-only",
    );
    expect(controlsDisabledReason({ pendingDelete: true })).toBe(
      "This row is deleted in the pending version",
    );
    expect(controlsDisabledReason({})).toBeNull();
  });
});

describe("ControlButtons", () => {
  beforeEach(() => {
    useChangeSetStore.setState({ sets: {} });
    notify.mockClear();
  });
  const base = {
    controls: [FIX],
    columns: COLS,
    cells: { c3: { value: "old", original: "old" } },
    datasetId: "ds1",
    rowId: "r12",
    rowLabel: "Row 12",
    layout: LAYOUT,
  };
  it("adds one pending change and says so", () => {
    render(<ControlButtons {...base} />);
    fireEvent.click(screen.getByTestId("layout-btn-Use the policy reply"));
    const ops = useChangeSetStore.getState().getOps("ds1");
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({
      op: "update_cell",
      row_id: "r12",
      column_id: "c3",
    });
    expect(notify.mock.calls[0][0]).toBe(
      "Row 12 · expected_reply: Refunds take 5-7 days. · in pending changes",
    );
  });
  it("does nothing but say so when the field already reads that", () => {
    render(
      <ControlButtons
        {...base}
        cells={{ c3: { value: FIX.value, original: FIX.value } }}
      />,
    );
    fireEvent.click(screen.getByTestId("layout-btn-Use the policy reply"));
    expect(useChangeSetStore.getState().getOps("ds1")).toHaveLength(0);
    expect(notify.mock.calls[0][0]).toBe(
      "Row 12 · expected_reply already reads that",
    );
  });
  it("is disabled for viewers and on an older version, with the reason", () => {
    const { rerender } = render(<ControlButtons {...base} viewer />);
    expect(
      screen.getByTestId("layout-btn-Use the policy reply"),
    ).toBeDisabled();
    expect(screen.getByTestId("layout-buttons-reason").textContent).toBe(
      "Viewers can't change rows",
    );
    rerender(<ControlButtons {...base} asOfVersion={5} />);
    expect(screen.getByTestId("layout-buttons-reason").textContent).toBe(
      "This is v5, read-only",
    );
  });
  it("hides buttons whose kind this branch can't apply", () => {
    const kinds = controlKinds(OP_TYPES);
    const tag = { id: "ctl_2", label: "Tag it", kind: "add_tag", tag: "x" };
    render(<ControlButtons {...base} controls={[tag]} />);
    expect(screen.queryByTestId("layout-btn-Tag it") !== null).toBe(
      kinds.includes("add_tag"),
    );
  });
});

describe("ButtonsEditor", () => {
  it("adds a set-field button on an editable column", () => {
    const onDraft = vi.fn();
    render(
      <ButtonsEditor
        draft={defaultLayout(COLS)}
        columns={COLS}
        onDraft={onDraft}
      />,
    );
    fireEvent.click(screen.getByTestId("button-add"));
    expect(screen.getByTestId("button-add-confirm")).toBeDisabled();
    fireEvent.change(screen.getByTestId("button-label"), {
      target: { value: "Mark fixed" },
    });
    fireEvent.change(screen.getByTestId("button-value"), {
      target: { value: "done" },
    });
    fireEvent.click(screen.getByTestId("button-add-confirm"));
    const next = onDraft.mock.calls[0][0];
    expect(next.controls).toEqual([
      {
        id: "ctl_1",
        label: "Mark fixed",
        kind: "set_field",
        column_id: "c1",
        value: "done",
      },
    ]);
  });
});
