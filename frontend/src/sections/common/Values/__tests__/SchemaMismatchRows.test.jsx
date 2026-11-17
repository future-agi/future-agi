import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SchemaMismatchRows from "../SchemaMismatchRows";

describe("sampled schema mismatches", () => {
  it("shows exactly the selected real sample values without a grid filter request", async () => {
    const close = vi.fn();
    render(
      <SchemaMismatchRows
        rows={[
          { row_id: "a", col: { cell_value: "first" } },
          { row_id: "b", col: { cell_value: "second" } },
        ]}
        columns={[{ id: "col", name: "question" }]}
        rowIds={["b"]}
        onClose={close}
      />,
    );
    const dialog = screen.getByRole("dialog", {
      name: "Sampled rows that do not match",
    });
    expect(within(dialog).queryByText("Row 1")).toBeNull();
    expect(within(dialog).getByText("Row 2")).toBeTruthy();
    expect(dialog.querySelector("pre").textContent).toBe(
      JSON.stringify({ question: "second" }, null, 2),
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(close).toHaveBeenCalledTimes(1);
  });
});
