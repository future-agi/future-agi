// the interface contract implementation: status and tag buttons with the interface contract's change-set (its
// OP_TYPES and ROW_STATUS_LABEL, dataset integration 4d080ce8c1). The
// panel has their inputs, the row toast names the status by its label, and
// Undo takes back exactly the button's op (by id), not the whole row.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { defaultLayout } from "../layoutModel";

const W3A_LABELS = Object.freeze({
  draft: "Draft",
  needs_review: "Needs review",
  approved: "Approved",
  archived: "Archived",
});
vi.mock("../changeSetKinds", () => ({
  availableControlKinds: () => ["set_field", "set_status", "add_tag"],
  rowStatusLabels: () => W3A_LABELS,
}));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: vi.fn(),
  default: vi.fn(),
}));
// The change-set as the interface contract's would hold it: ops with ids, revert by id.
const fake = { ops: [], pendingStatus: {}, calls: [] };
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/changeSetStore",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useChangeSet: () => ({
      ops: fake.ops,
      pendingStatus: fake.pendingStatus,
      addOp: (op) => {
        fake.calls.push(["addOp", op]);
        fake.ops = [...fake.ops, op];
      },
      revert: (...a) => {
        fake.calls.push(["revert", ...a]);
        fake.ops = fake.ops.filter((o) => o.id !== a[0]);
      },
    }),
  }),
);
const { notify } = await import("src/sections/common/ux/toast");
const { default: ControlButtons } = await import("../ControlButtons");
const { default: ButtonsEditor } = await import("../ButtonsEditor");

const COLS = [
  { id: "c1", name: "customer_message", section: "input", editable: true },
  { id: "o1", name: "answer", section: "outputs", editable: false },
];
const LAYOUT = { id: "v1", name: "Refund review" };

describe("ButtonsEditor with W3 A's change-set", () => {
  it("adds a status button: status, reason (archiving needs one)", () => {
    const onDraft = vi.fn();
    render(
      <ButtonsEditor
        draft={defaultLayout(COLS)}
        columns={COLS}
        onDraft={onDraft}
      />,
    );
    fireEvent.click(screen.getByTestId("button-add"));
    fireEvent.change(screen.getByTestId("button-label"), {
      target: { value: "Archive dupe" },
    });
    fireEvent.change(screen.getByTestId("button-kind"), {
      target: { value: "set_status" },
    });
    expect(screen.queryByTestId("button-value")).toBeNull();
    fireEvent.change(screen.getByTestId("button-status"), {
      target: { value: "archived" },
    });
    expect(
      screen.getByText("Button “Archive dupe”: archiving needs a reason."),
    ).toBeTruthy();
    expect(screen.getByTestId("button-add-confirm")).toBeDisabled();
    fireEvent.change(screen.getByTestId("button-reason"), {
      target: { value: "Duplicate" },
    });
    expect(screen.getByTestId("button-add-confirm")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("button-add-confirm"));
    expect(onDraft.mock.calls[0][0].controls).toEqual([
      {
        id: "ctl_1",
        label: "Archive dupe",
        kind: "set_status",
        status: "archived",
        reason: "Duplicate",
      },
    ]);
  });
  it("adds a tag button; lists buttons by their labels", () => {
    const onDraft = vi.fn();
    const draft = {
      ...defaultLayout(COLS),
      controls: [
        {
          id: "ctl_1",
          label: "Review",
          kind: "set_status",
          status: "needs_review",
        },
      ],
    };
    render(<ButtonsEditor draft={draft} columns={COLS} onDraft={onDraft} />);
    expect(screen.getByTestId("panel-button-Review").textContent).toBe(
      "Review · Set the row status Needs review",
    );
    fireEvent.click(screen.getByTestId("button-add"));
    fireEvent.change(screen.getByTestId("button-label"), {
      target: { value: "Tag VIP" },
    });
    fireEvent.change(screen.getByTestId("button-kind"), {
      target: { value: "add_tag" },
    });
    fireEvent.change(screen.getByTestId("button-tag"), {
      target: { value: "vip;gold" },
    });
    expect(
      screen.getByText("Button “Tag VIP”: a tag can't contain , ; or |."),
    ).toBeTruthy();
    fireEvent.change(screen.getByTestId("button-tag"), {
      target: { value: " vip " },
    });
    fireEvent.click(screen.getByTestId("button-add-confirm"));
    expect(onDraft.mock.calls[0][0].controls[1]).toEqual({
      id: "ctl_2",
      label: "Tag VIP",
      kind: "add_tag",
      tag: "vip",
    });
  });
  it("status and tag buttons need no editable column", () => {
    render(
      <ButtonsEditor
        draft={defaultLayout([COLS[1]])}
        columns={[COLS[1]]}
        onDraft={vi.fn()}
      />,
    );
    expect(screen.getByTestId("button-add")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("button-add"));
    expect(screen.getByTestId("button-status")).toBeTruthy();
  });
});

describe("ControlButtons with W3 A's change-set", () => {
  beforeEach(() => {
    fake.ops = [
      {
        id: "op_1",
        op: "update_cell",
        row_id: "r3",
        column_id: "c1",
        value: "typed",
      },
    ];
    fake.pendingStatus = {};
    fake.calls = [];
    notify.mockClear();
  });
  const base = {
    columns: COLS,
    cells: {},
    datasetId: "ds1",
    rowId: "r3",
    rowLabel: "Row 3",
    layout: LAYOUT,
  };
  const ARCHIVE = {
    id: "ctl_1",
    label: "Archive dupe",
    kind: "set_status",
    status: "archived",
    reason: "Duplicate",
  };
  const TAG = { id: "ctl_2", label: "Tag VIP", kind: "add_tag", tag: "vip" };
  it("a status button: one op, the label in the toast, Undo by its op id", () => {
    render(<ControlButtons {...base} controls={[ARCHIVE, TAG]} />);
    fireEvent.click(screen.getByTestId("layout-btn-Archive dupe"));
    const [kind, op] = fake.calls[0];
    expect(kind).toBe("addOp");
    expect(op).toMatchObject({
      op: "set_status",
      row_ids: ["r3"],
      status: "archived",
      reason: "Duplicate",
      source: { kind: "layout", layout_id: "v1", name: "Refund review" },
    });
    expect(op.id).toMatch(/^layout_op_/);
    const [text, opts] = notify.mock.calls[0];
    expect(text).toBe("Row 3 · Archived: Duplicate · in pending changes");
    opts.undo.onUndo();
    expect(fake.calls[1]).toEqual(["revert", op.id]);
    // The row's other pending change stays.
    expect(fake.ops.map((o) => o.id)).toEqual(["op_1"]);
  });
  it("a tag button: Undo reverts only the tag op", () => {
    render(<ControlButtons {...base} controls={[TAG]} />);
    fireEvent.click(screen.getByTestId("layout-btn-Tag VIP"));
    const op = fake.calls[0][1];
    expect(op).toMatchObject({
      op: "set_tags",
      row_ids: ["r3"],
      add: ["vip"],
      remove: [],
    });
    expect(notify.mock.calls[0][0]).toBe(
      "Row 3 · tag vip · in pending changes",
    );
    notify.mock.calls[0][1].undo.onUndo();
    expect(fake.calls[1]).toEqual(["revert", op.id]);
    expect(fake.ops.map((o) => o.id)).toEqual(["op_1"]);
  });
  it("a row already in that status (saved or pending) says so and adds nothing", () => {
    const REVIEW = {
      id: "ctl_3",
      label: "Review",
      kind: "set_status",
      status: "needs_review",
    };
    const { rerender } = render(
      <ControlButtons
        {...base}
        controls={[REVIEW]}
        rowStatus={{ status: "needs_review", reason: null }}
      />,
    );
    fireEvent.click(screen.getByTestId("layout-btn-Review"));
    expect(fake.calls).toHaveLength(0);
    expect(notify.mock.calls[0][0]).toBe("Row 3 is already Needs review");
    fake.pendingStatus = { r3: { status: "approved", reason: null } };
    rerender(
      <ControlButtons
        {...base}
        controls={[REVIEW]}
        rowStatus={{ status: "needs_review", reason: null }}
      />,
    );
    fireEvent.click(screen.getByTestId("layout-btn-Review"));
    expect(fake.calls[0][1]).toMatchObject({
      op: "set_status",
      status: "needs_review",
    });
  });
});
