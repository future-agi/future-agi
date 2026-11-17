// W3-2 B4: the Form view is listed only with a declared schema; its inputs
// follow the schema and a valid edit is committed as the cell's value.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import SchemaForm, { cellValueOf, emptyFor, formValueOf } from "../SchemaForm";
import {
  effectiveMode,
  modeAvailability,
} from "src/sections/common/Values/viewModes";
import { sniffValue } from "src/sections/common/Values/sniff";

const TIER = { type: "string", enum: ["free", "pro", "enterprise"] };

describe("W3-2 B4 · Form view", () => {
  it("is a mode only when the column has a declared schema", () => {
    expect("form" in modeAvailability(sniffValue("pro"))).toBe(false);
    expect(modeAvailability(sniffValue("pro"), { form: true }).form).toBeNull();
    expect(effectiveMode("form", sniffValue("pro"))).toBe("auto");
    expect(effectiveMode("form", sniffValue("pro"), { form: true })).toBe(
      "form",
    );
  });

  it("reads and writes the stored value in the schema's shape", () => {
    expect(formValueOf('{"a":1}', { type: "object" }, "json")).toEqual({
      a: 1,
    });
    expect(formValueOf("3", { type: "integer" }, "text")).toBe(3);
    expect(formValueOf("true", { type: "boolean" }, "text")).toBe(true);
    expect(cellValueOf({ a: 2 }, "json", '{"a":1}')).toBe('{"a":2}');
    expect(cellValueOf(4, "text", "3")).toBe("4");
    expect(cellValueOf("pro", "text", "free")).toBe("pro");
    expect(emptyFor(TIER)).toBe("free");
    expect(emptyFor({ type: "object" })).toEqual({});
  });

  it("an enum is a select; picking a value commits it", () => {
    const onCommit = vi.fn();
    render(
      <SchemaForm
        name="customer_tier"
        schema={TIER}
        value="free"
        dataType="text"
        editable
        onCommit={onCommit}
      />,
    );
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(within(screen.getByRole("listbox")).getByText("pro"));
    expect(onCommit).toHaveBeenCalledWith("pro");
  });

  it("an object is a group: switch, number and text inputs typed by the schema; invalid edits are not committed", () => {
    const schema = {
      type: "object",
      required: ["vip"],
      properties: {
        vip: { type: "boolean" },
        seats: { type: "integer" },
        note: { type: "string" },
      },
    };
    const onCommit = vi.fn();
    render(
      <SchemaForm
        name="account"
        schema={schema}
        value={JSON.stringify({ vip: false, seats: 3, note: "x" })}
        dataType="json"
        editable
        onCommit={onCommit}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "account.vip" }));
    expect(onCommit).toHaveBeenLastCalledWith(
      JSON.stringify({ vip: true, seats: 3, note: "x" }),
    );
    const seats = screen.getByRole("spinbutton", { name: "account.seats" });
    fireEvent.change(seats, { target: { value: "2.5" } });
    fireEvent.blur(seats);
    expect(screen.getByTestId("schema-form-issues")).toHaveTextContent(
      "account.seats must be a whole number",
    );
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("read-only without edit rights", () => {
    render(<SchemaForm name="customer_tier" schema={TIER} value="free" />);
    expect(screen.getByText("Read-only here")).toBeInTheDocument();
  });
});
