import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DateTimeRangePicker from "../DateTimeRangePicker";

// The calendar seeds itself from a `value` prop on the open transition
// (DatePicker.jsx:25-36), but this component never passed one, so picking
// Custom always opened a blank calendar even when a range was already set.
describe("DateTimeRangePicker custom calendar", () => {
  it("seeds the included calendar dates without changing the stored half-open range", () => {
    const setParentDateFilter = vi.fn();
    render(
      <DateTimeRangePicker
        dateOption="Custom"
        setDateOption={vi.fn()}
        setParentDateFilter={setParentDateFilter}
        dateFilter={["2026-08-01 00:00:00", "2026-08-05 00:00:00"]}
        isEdit
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "01/08/2026 - 04/08/2026" }),
    );
    expect(screen.getByDisplayValue("2026-08-01")).toBeInTheDocument();
    // The stored upper bound is exclusive; August 4 is the last included day.
    expect(screen.getByDisplayValue("2026-08-04")).toBeInTheDocument();
    setParentDateFilter.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(setParentDateFilter).toHaveBeenCalledWith([
      "2026-08-01 00:00:00",
      "2026-08-05 00:00:00",
    ]);
  });
});
