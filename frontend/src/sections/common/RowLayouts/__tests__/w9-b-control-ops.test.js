// the interface contract implementation review M4: the button data (controlOps.js) and the kinds this
// branch can run (changeSetKinds.js), with the interface contract's change-set shapes.
import { describe, expect, it } from "vitest";
import {
  blankControl,
  cleanControl,
  controlOp,
  controlSummary,
  layoutOpId,
  runUndo,
  undoPlan,
  validateControls,
} from "../controlOps";
import { availableControlKinds, rowStatusLabels } from "../changeSetKinds";
import * as layoutModel from "../layoutModel";

// the interface contract's change-set (dataset integration 4d080ce8c1, changeSetStore.js).
const W3A = {
  OP_TYPES: [
    "update_cell",
    "add_row",
    "delete_rows",
    "set_splits",
    "set_row_metadata",
    "set_environment",
    "set_status",
    "set_tags",
  ],
  ROW_STATUS_LABEL: Object.freeze({
    draft: "Draft",
    needs_review: "Needs review",
    approved: "Approved",
    archived: "Archived",
  }),
};
const TODAY = {
  OP_TYPES: [
    "update_cell",
    "add_row",
    "delete_rows",
    "set_splits",
    "set_row_metadata",
    "set_environment",
  ],
};
const COLS = [
  { id: "c1", name: "customer_message", editable: true },
  { id: "o1", name: "answer", editable: false },
];
const ALL = ["set_field", "set_status", "add_tag"];

describe("changeSetKinds", () => {
  it("offers every kind with W3 A's change-set, and its status labels", () => {
    expect(availableControlKinds(W3A)).toEqual(ALL);
    expect(rowStatusLabels(W3A)).toBe(W3A.ROW_STATUS_LABEL);
  });
  it("without W3 A only Set a field", () => {
    expect(availableControlKinds(TODAY)).toEqual(["set_field"]);
    expect(rowStatusLabels(TODAY)).toBeNull();
  });
  it("a status button needs the labels its picker offers", () => {
    expect(availableControlKinds({ OP_TYPES: W3A.OP_TYPES })).toEqual([
      "set_field",
      "add_tag",
    ]);
    expect(
      availableControlKinds({ OP_TYPES: W3A.OP_TYPES, ROW_STATUS_LABEL: {} }),
    ).toEqual(["set_field", "add_tag"]);
  });
  it("reads this branch's change-set by default", () => {
    expect(availableControlKinds()).toContain("set_field");
  });
});

describe("validateControls (status and tag buttons)", () => {
  const S = W3A.ROW_STATUS_LABEL;
  const v = (c) =>
    validateControls([{ id: "c", label: "B", ...c }], COLS, ALL, S);
  it("needs a real row status, and a reason to archive", () => {
    expect(v({ kind: "set_status", status: "" })).toEqual([
      "Button “B”: pick the status it sets.",
    ]);
    expect(v({ kind: "set_status", status: "done" })).toEqual([
      "Button “B”: “done” isn't a row status.",
    ]);
    expect(v({ kind: "set_status", status: "archived", reason: " " })).toEqual([
      "Button “B”: archiving needs a reason.",
    ]);
    expect(
      v({ kind: "set_status", status: "archived", reason: "Duplicate" }),
    ).toEqual([]);
    expect(v({ kind: "set_status", status: "needs_review" })).toEqual([]);
  });
  it("needs one tag with no separator", () => {
    expect(v({ kind: "add_tag", tag: "" })).toEqual([
      "Button “B”: give the tag it adds.",
    ]);
    expect(v({ kind: "add_tag", tag: "a,b" })).toEqual([
      "Button “B”: a tag can't contain , ; or |.",
    ]);
    expect(v({ kind: "add_tag", tag: "wrong-expected" })).toEqual([]);
  });
  it("layoutModel passes the statuses through", () => {
    const spec = {
      ...layoutModel.defaultLayout(COLS),
      controls: [{ id: "c", label: "B", kind: "set_status", status: "done" }],
    };
    expect(
      layoutModel.validateLayout(spec, COLS, { kinds: ALL, statuses: S }),
    ).toContain("Button “B”: “done” isn't a row status.");
    // The same functions, one import path.
    expect(layoutModel.validateControls).toBe(validateControls);
    expect(layoutModel.controlOp).toBe(controlOp);
  });
});

describe("panel form", () => {
  it("blank forms per kind; the saved control keeps its kind's fields only", () => {
    expect(blankControl("set_field", COLS)).toEqual({
      kind: "set_field",
      column_id: "c1",
      value: "",
    });
    expect(blankControl("set_status")).toEqual({
      kind: "set_status",
      status: "",
      reason: "",
    });
    expect(blankControl("add_tag")).toEqual({ kind: "add_tag", tag: "" });
    expect(
      cleanControl("ctl_2", {
        label: " Archive dupe ",
        kind: "set_status",
        status: "archived",
        reason: " Duplicate ",
        column_id: "c1",
      }),
    ).toEqual({
      id: "ctl_2",
      label: "Archive dupe",
      kind: "set_status",
      status: "archived",
      reason: "Duplicate",
    });
    expect(
      cleanControl("ctl_3", {
        label: "Review",
        kind: "set_status",
        status: "needs_review",
        reason: "",
      }),
    ).toEqual({
      id: "ctl_3",
      label: "Review",
      kind: "set_status",
      status: "needs_review",
    });
    expect(
      cleanControl("ctl_4", { label: "Tag", kind: "add_tag", tag: " vip " }),
    ).toEqual({ id: "ctl_4", label: "Tag", kind: "add_tag", tag: "vip" });
  });
  it("summaries use the status label", () => {
    const S = W3A.ROW_STATUS_LABEL;
    expect(
      controlSummary(
        {
          label: "Archive dupe",
          kind: "set_status",
          status: "archived",
          reason: "Duplicate",
        },
        { statuses: S },
      ),
    ).toBe("Archive dupe · Set the row status Archived (Duplicate)");
    expect(controlSummary({ label: "Tag", kind: "add_tag", tag: "vip" })).toBe(
      "Tag · Add a tag vip",
    );
    expect(
      controlSummary(
        { label: "Fix", kind: "set_field", column_id: "c1", value: "x" },
        { colName: () => "customer_message" },
      ),
    ).toBe("Fix · Set a field customer_message = x");
  });
});

describe("undo takes back exactly one op", () => {
  const src = { kind: "layout", layout_id: "v1", name: "Refund review" };
  it("ids are new each time", () => {
    expect(layoutOpId()).not.toBe(layoutOpId());
  });
  it("a status op goes by id and the row's earlier pending status comes back", () => {
    const op = {
      id: "layout_op_9",
      op: "set_status",
      row_ids: ["r1"],
      status: "approved",
      source: src,
    };
    expect(undoPlan([], op)).toEqual({ revert: "layout_op_9", restore: null });
    const earlier = {
      id: "op_3",
      at: 5,
      op: "set_status",
      row_ids: ["r1", "r2"],
      status: "archived",
      reason: "Dupes",
    };
    expect(undoPlan([earlier], op)).toEqual({
      revert: "layout_op_9",
      restore: {
        op: "set_status",
        row_ids: ["r1"],
        status: "archived",
        reason: "Dupes",
      },
    });
  });
  it("a tag op goes by id; other pending changes on the row stay", () => {
    const op = {
      id: "layout_op_10",
      op: "set_tags",
      row_ids: ["r1"],
      add: ["vip"],
      remove: [],
    };
    const other = {
      id: "op_1",
      op: "update_cell",
      row_id: "r1",
      column_id: "c1",
      value: "x",
    };
    const plan = undoPlan([other], op);
    expect(plan).toEqual({ revert: "layout_op_10", restore: null });
    const calls = [];
    runUndo(
      {
        revert: (...a) => calls.push(["revert", ...a]),
        addOp: (o) => calls.push(["add", o]),
      },
      plan,
    );
    expect(calls).toEqual([["revert", "layout_op_10"]]);
  });
  it("a cell's earlier pending edit comes back instead of the cell reverting", () => {
    const earlier = {
      id: "op_1",
      at: 1,
      op: "update_cell",
      row_id: "r1",
      column_id: "c1",
      value: "typed",
      before: "orig",
    };
    const op = {
      id: "layout_op_11",
      op: "update_cell",
      row_id: "r1",
      column_id: "c1",
      value: "button",
      before: "orig",
    };
    expect(undoPlan([earlier], op)).toEqual({ revert: null, restore: earlier });
    expect(undoPlan([], op)).toEqual({ revert: "layout_op_11", restore: null });
  });
  it("no undo of its own on a pending new row (its add_row took the change)", () => {
    const add = { id: "op_2", op: "add_row", temp_id: "tmp_1", cells: {} };
    expect(
      undoPlan([add], {
        id: "x",
        op: "set_tags",
        row_ids: ["tmp_1"],
        add: ["a"],
        remove: [],
      }),
    ).toBeNull();
    runUndo(
      {
        revert: () => {
          throw new Error("no");
        },
      },
      null,
    );
  });
  it("controlOp says the status by its label", () => {
    const r = controlOp(
      {
        id: "c",
        label: "x",
        kind: "set_status",
        status: "archived",
        reason: "Duplicate",
      },
      {
        rowId: "r3",
        rowLabel: "Row 3",
        columns: COLS,
        layout: { id: "v1", name: "Refund review" },
        statusLabel: W3A.ROW_STATUS_LABEL,
      },
    );
    expect(r.toast).toBe("Row 3 · Archived: Duplicate · in pending changes");
  });
});
