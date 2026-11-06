import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomDateRangePicker from "../DatePicker";
import { observePresetDateFilter } from "src/sections/projects/timeWindowPresets";

function pickerProps(value) {
  return {
    open: true,
    onClose: vi.fn(),
    anchorEl: document.body,
    setDateFilter: vi.fn(),
    setDateOption: vi.fn(),
    value,
  };
}

function selectDay(date) {
  fireEvent.change(screen.getByLabelText("Start Date"), {
    target: { value: date },
  });
  fireEvent.change(screen.getByLabelText("End Date"), {
    target: { value: date },
  });
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
}

describe("date-only selections use half-open local calendar bounds", () => {
  // Run this suite in separate UTC, Asia/Kolkata and America/Los_Angeles
  // processes. The March/November cases cross both US DST changes.
  it.each([
    [2026, 10, 2, "2026-11-02"],
    [2026, 2, 8, "2026-03-08"],
    [2026, 10, 1, "2026-11-01"],
  ])(
    "includes the complete selected day %s-%s-%s",
    (year, month, day, text) => {
      const props = pickerProps();
      render(<CustomDateRangePicker {...props} />);
      selectDay(text);

      const expectedStart = new Date(year, month, day);
      const expectedEnd = new Date(year, month, day + 1);
      expect(props.setDateFilter).toHaveBeenCalledExactlyOnceWith([
        expectedStart.toISOString(),
        expectedEnd.toISOString(),
      ]);
      expect(props.setDateOption).toHaveBeenCalledWith("Custom");
      expect(props.onClose).toHaveBeenCalledOnce();
      if (
        Intl.DateTimeFormat().resolvedOptions().timeZone ===
        "America/Los_Angeles"
      ) {
        const hours = (expectedEnd - expectedStart) / 3_600_000;
        expect(hours).toBe(month === 2 ? 23 : day === 1 ? 25 : 24);
      }
    },
  );

  it("applies calendar clicks and does not expand the end again on reopen", () => {
    const props = pickerProps([
      new Date(2026, 10, 1).toISOString(),
      new Date(2026, 10, 4).toISOString(),
    ]);
    const { rerender } = render(<CustomDateRangePicker {...props} />);
    fireEvent.click(
      screen.getAllByRole("button", { name: "2", exact: true })[0],
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: "2", exact: true })[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    const applied = props.setDateFilter.mock.lastCall[0];
    expect(applied).toEqual([
      new Date(2026, 10, 2).toISOString(),
      new Date(2026, 10, 3).toISOString(),
    ]);

    rerender(<CustomDateRangePicker {...props} open={false} value={applied} />);
    rerender(<CustomDateRangePicker {...props} value={applied} />);
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-11-02");
    expect(screen.getByLabelText("End Date")).toHaveValue("2026-11-02");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter.mock.lastCall[0]).toEqual(applied);
  });

  it.each([
    ["ISO bookmark", ["2026-11-02T11:12:13.456Z", "2026-11-02T12:13:14.789Z"]],
    ["local exact timestamps", ["2026-11-02 11:12:13", "2026-11-02 12:13:14"]],
    ["preset", observePresetDateFilter("Today", new Date(2026, 10, 2, 12))],
  ])("preserves untouched %s bounds byte for byte", (_name, value) => {
    const props = pickerProps(value);
    render(<CustomDateRangePicker {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter).toHaveBeenCalledExactlyOnceWith(value);
  });

  it.each([
    ["2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z"],
    ["2026-11-03T00:00:00Z", "2026-11-02T00:00:00Z"],
    ["invalid", "invalid"],
  ])("does not silently expand invalid stored bounds %j", (start, end) => {
    const props = pickerProps([start, end]);
    render(<CustomDateRangePicker {...props} />);
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
    expect(props.setDateFilter).not.toHaveBeenCalled();
  });

  it("clearing an input disables Done instead of submitting the previous date", () => {
    const props = pickerProps(["2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"]);
    render(<CustomDateRangePicker {...props} />);
    fireEvent.change(screen.getByLabelText("End Date"), {
      target: { value: "" },
    });
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
    expect(props.setDateFilter).not.toHaveBeenCalled();
  });
});

describe("explicit inclusive-local compatibility", () => {
  it.each([
    ["2026-11-02 00:00:00", "2026-11-02 00:00:00"],
    ["2026-11-02 00:00:00", "2026-11-04 00:00:00"],
    ["2026-11-02 11:12:13", "2026-11-04 12:13:14"],
  ])("preserves untouched inclusive timestamps %s to %s", (start, end) => {
    const props = pickerProps([start, end]);
    render(
      <CustomDateRangePicker {...props} rangeConvention="inclusive-local" />,
    );
    expect(screen.getByLabelText("End Date")).toHaveValue(end.slice(0, 10));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.setDateFilter).toHaveBeenCalledExactlyOnceWith([start, end]);
  });

  it.each(["half-open", "inclusive-local"])(
    "rejects reversed and cleared %s ranges",
    (rangeConvention) => {
      const props = pickerProps(["2026-11-04 00:00:00", "2026-11-02 00:00:00"]);
      render(
        <CustomDateRangePicker {...props} rangeConvention={rangeConvention} />,
      );
      expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
      selectDay("2026-11-02");
      props.setDateFilter.mockClear();
      fireEvent.change(screen.getByLabelText("Start Date"), {
        target: { value: "" },
      });
      expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Done" }));
      expect(props.setDateFilter).not.toHaveBeenCalled();
    },
  );
});
