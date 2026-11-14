import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomDateRangePicker from "../DatePicker";

// Today is 9 Nov 2026 (local). The left calendar opens on November 2026 and
// the right one on December 2026. Days outside a calendar's month are not
// buttons, so index 0 is the November day and index 1 the December day.
const NOW = new Date(2026, 10, 9, 15, 30);

function pickerProps() {
  return {
    open: true,
    onClose: vi.fn(),
    anchorEl: document.body,
    setDateFilter: vi.fn(),
    setDateOption: vi.fn(),
  };
}

function dayButtons(day) {
  return screen.getAllByRole("button", { name: String(day), exact: true });
}

function typeDate(label, value) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("custom date range picker does not allow days after today", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("disables calendar days after today and keeps today selectable", () => {
    const props = pickerProps();
    render(<CustomDateRangePicker {...props} />);

    expect(dayButtons(2)[0]).toBeEnabled();
    expect(dayButtons(9)[0]).toBeEnabled();
    expect(dayButtons(10)[0]).toBeDisabled();
    expect(dayButtons(30)[0]).toBeDisabled();
    expect(dayButtons(1)[1]).toBeDisabled();
    expect(dayButtons(15)[1]).toBeDisabled();

    fireEvent.click(dayButtons(2)[0]);
    fireEvent.click(dayButtons(10)[0]);
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-11-02");
    expect(screen.getByLabelText("End Date")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();

    fireEvent.click(dayButtons(9)[0]);
    expect(screen.getByLabelText("End Date")).toHaveValue("2026-11-09");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter).toHaveBeenCalledExactlyOnceWith([
      new Date(2026, 10, 2).toISOString(),
      new Date(2026, 10, 10).toISOString(),
    ]);
  });

  it("rejects a typed start or end date after today", () => {
    const props = pickerProps();
    render(<CustomDateRangePicker {...props} />);

    typeDate("Start Date", "2026-12-01");
    expect(screen.getByLabelText("Start Date")).toHaveValue("");

    typeDate("Start Date", "2026-11-02");
    typeDate("End Date", "2026-11-10");
    expect(screen.getByLabelText("End Date")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter).not.toHaveBeenCalled();

    typeDate("End Date", "2026-11-09");
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter).toHaveBeenCalledExactlyOnceWith([
      new Date(2026, 10, 2).toISOString(),
      new Date(2026, 10, 10).toISOString(),
    ]);
  });
});
