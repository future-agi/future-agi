import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import BlankGridColumns from "../BlankGridColumns";

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
const roles = [
  { value: "input", label: "Input" },
  { value: "expected", label: "Expected" },
  { value: "metadata", label: "Metadata" },
];
function Editor() {
  const [columns, setColumns] = useState([{ name: "question", role: "input" }]);
  return (
    <>
      <BlankGridColumns
        columns={columns}
        setColumns={setColumns}
        roles={roles}
      />
      <output>{JSON.stringify(columns)}</output>
    </>
  );
}

describe("blank-grid schema editor", () => {
  it("retains the selected type and role when a column is renamed", () => {
    render(<Editor />);
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Column 1 type" }),
    );
    fireEvent.click(
      screen.getByRole(
        "option",
        /** @type {import("@testing-library/react").ByRoleOptions & { exact: true }} */ ({
          name: "Integer",
          exact: true,
        }),
      ),
    );
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Column 1 role" }),
    );
    fireEvent.click(
      screen.getByRole(
        "option",
        /** @type {import("@testing-library/react").ByRoleOptions & { exact: true }} */ ({
          name: "Expected",
          exact: true,
        }),
      ),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Column 1 name" }), {
      target: { value: "answer" },
    });
    expect(JSON.parse(screen.getByRole("status").textContent)).toEqual([
      { name: "answer", role: "expected", type: "integer" },
    ]);
  });

  it("reports duplicate and blank names without discarding columns", () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Column 2 name" }), {
      target: { value: " QUESTION " },
    });
    expect(screen.getAllByText("Use a unique, non-empty name")).toHaveLength(2);
    expect(JSON.parse(screen.getByRole("status").textContent)).toHaveLength(2);
    fireEvent.change(screen.getByRole("textbox", { name: "Column 2 name" }), {
      target: { value: "" },
    });
    expect(screen.getAllByText("Use a unique, non-empty name")).toHaveLength(1);
  });
});
