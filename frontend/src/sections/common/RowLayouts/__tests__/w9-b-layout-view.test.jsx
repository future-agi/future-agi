import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import RowLayoutView from "../RowLayoutView";
import {
  defaultLayout,
  layoutColumns,
  moveField,
  patchField,
} from "../layoutModel";

const f = (id, name, role, value) => ({
  column: { id, name, role, data_type: "text", origin_type: "OTHERS" },
  value,
  original: value,
  edited: false,
});
const MODEL = {
  input: [
    f("c1", "customer_message", "input", "# Refund\nmy order is late"),
    f("c2", "intent", "input", "refund"),
  ],
  expected: [f("c3", "expected_reply", "expected", "We are sorry")],
  metadata: [],
  noRole: [f("c4", "ticket_id", null, "T-77")],
  outputs: [
    {
      column: {
        id: "o1",
        name: "answer",
        data_type: "text",
        origin_type: "run_prompt",
      },
      cell: { cell_value: "Here is your refund" },
      evals: [],
    },
  ],
  evals: [],
  human: [],
};
const COLUMNS = layoutColumns(MODEL);
const names = (el) =>
  [...el.querySelectorAll("[data-testid^='layout-field-']")].map((n) =>
    n.getAttribute("data-testid").replace("layout-field-", ""),
  );
const edit = () => ({
  canEdit: true,
  editingId: null,
  applyRef: { current: null },
  start: vi.fn(),
  cancel: vi.fn(),
  apply: vi.fn(),
  revert: vi.fn(),
});

describe("RowLayoutView", () => {
  it("renders fields in the spec's order and sides; hidden ones fold", () => {
    let spec = defaultLayout(COLUMNS);
    // order: customer_message, expected_reply, intent, answer; ticket hidden
    spec = moveField(spec, 2, 1);
    spec = patchField(spec, "c4", { hidden: true });
    spec = patchField(spec, "c2", { side: "left" });
    spec = patchField(spec, "o1", { side: "left" });
    spec = { ...spec, columns: 2 };
    render(
      <RowLayoutView
        spec={spec}
        columns={COLUMNS}
        model={MODEL}
        edit={edit()}
      />,
    );
    const left = screen.getByTestId("layout-side-left");
    const right = screen.getByTestId("layout-side-right");
    expect(names(left)).toEqual(["customer_message", "intent", "answer"]);
    expect(names(right)).toEqual(["expected_reply"]);
    const toggle = screen.getByTestId("layout-hidden-toggle");
    expect(toggle.textContent).toBe("Hidden fields (1)");
    expect(screen.queryByTestId("layout-hidden-list")).toBeNull();
    fireEvent.click(toggle);
    expect(names(screen.getByTestId("layout-hidden-list"))).toEqual([
      "ticket_id",
    ]);
  });

  it("one column puts every shown field on the left, in order", () => {
    let spec = defaultLayout(COLUMNS);
    spec = moveField(spec, 4, 0);
    render(
      <RowLayoutView
        spec={spec}
        columns={COLUMNS}
        model={MODEL}
        edit={edit()}
      />,
    );
    expect(screen.queryByTestId("layout-side-right")).toBeNull();
    expect(names(screen.getByTestId("layout-side-left"))).toEqual([
      "answer",
      "customer_message",
      "intent",
      "expected_reply",
      "ticket_id",
    ]);
  });

  it("shows each field in its view mode and uses the row's edit path", () => {
    let spec = defaultLayout(COLUMNS);
    spec = patchField(spec, "c1", { view: "text" });
    const e = edit();
    render(
      <RowLayoutView spec={spec} columns={COLUMNS} model={MODEL} edit={e} />,
    );
    const card = screen.getByTestId("layout-field-customer_message");
    // Text mode keeps the markdown source as typed.
    expect(card.textContent).toContain("# Refund");
    fireEvent.click(within(card).getByLabelText("Edit customer_message"));
    expect(e.start).toHaveBeenCalledWith("c1");
    // Generated outputs are read only.
    const out = screen.getByTestId("layout-field-answer");
    expect(within(out).queryByLabelText("Edit answer")).toBeNull();
    expect(out.textContent).toContain("Here is your refund");
  });

  it("a column added after the layout was saved shows under Hidden fields", () => {
    const spec = defaultLayout(COLUMNS.filter((c) => c.id !== "c4"));
    render(
      <RowLayoutView
        spec={spec}
        columns={COLUMNS}
        model={MODEL}
        edit={edit()}
      />,
    );
    expect(screen.getByTestId("layout-hidden-toggle").textContent).toBe(
      "Hidden fields (1)",
    );
  });

  it("says which saved fields are no longer columns here", () => {
    const spec = defaultLayout(COLUMNS);
    spec.fields.push({
      column_id: "gone",
      column_name: "refund_amount",
      view: "auto",
      side: "left",
      hidden: false,
    });
    render(
      <RowLayoutView
        spec={spec}
        columns={COLUMNS}
        model={MODEL}
        edit={edit()}
      />,
    );
    expect(screen.getByTestId("layout-missing").textContent).toBe(
      "Not a column here any more: refund_amount. The layout skips it.",
    );
    expect(names(screen.getByTestId("layout-side-left"))).not.toContain(
      "refund_amount",
    );
  });
});
