import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DateTimeRangePicker from "../DateTimeRangePicker";

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: ({ setDateFilter, setDateOption }) => (
    <button
      type="button"
      onClick={() => {
        setDateFilter(["2026-08-10 00:00:00", "2026-08-12 00:00:00"]);
        setDateOption("Custom");
      }}
    >
      Apply custom range
    </button>
  ),
}));

describe("DateTimeRangePicker option propagation", () => {
  it("publishes a preset range before switching the parent option", async () => {
    const events = [];
    const setParentDateFilter = vi.fn((range) =>
      events.push({ type: "range", range }),
    );
    const setDateOption = vi.fn((option) =>
      events.push({ type: "option", option }),
    );

    render(
      <DateTimeRangePicker
        dateOption="30D"
        setDateOption={setDateOption}
        setParentDateFilter={setParentDateFilter}
      />,
    );
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: "1 hr" }),
    ).not.toBeInTheDocument();
    events.length = 0;
    setParentDateFilter.mockClear();
    setDateOption.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Today" }));

    expect(events[0]?.type).toBe("range");
    expect(events[0]?.range).toHaveLength(2);
    expect(events[1]).toEqual({ type: "option", option: "Today" });
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalledTimes(1));
  });

  it("offers the required one-hour preset", async () => {
    const setParentDateFilter = vi.fn();
    const setDateOption = vi.fn();
    render(
      <DateTimeRangePicker
        includeOneHour
        dateOption="30D"
        setDateOption={setDateOption}
        setParentDateFilter={setParentDateFilter}
      />,
    );
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "1 hr" }));

    expect(setDateOption).toHaveBeenCalledWith("1 hr");
    expect(setParentDateFilter).toHaveBeenLastCalledWith([
      expect.any(String),
      expect.any(String),
    ]);
  });

  it("publishes a custom range before switching the parent option", async () => {
    const events = [];
    const setParentDateFilter = vi.fn((range) =>
      events.push({ type: "range", range }),
    );
    const setDateOption = vi.fn((option) =>
      events.push({ type: "option", option }),
    );

    render(
      <DateTimeRangePicker
        dateOption="30D"
        setDateOption={setDateOption}
        setParentDateFilter={setParentDateFilter}
      />,
    );
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalled());
    events.length = 0;
    setParentDateFilter.mockClear();
    setDateOption.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Apply custom range" }));

    expect(events).toEqual([
      {
        type: "range",
        range: ["2026-08-10 00:00:00", "2026-08-12 00:00:00"],
      },
      { type: "option", option: "Custom" },
    ]);
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalledTimes(1));
  });

  it("publishes a zoom range before switching the parent option", async () => {
    const events = [];
    const setParentDateFilter = vi.fn((range) =>
      events.push({ type: "range", range }),
    );
    const setDateOption = vi.fn((option) =>
      events.push({ type: "option", option }),
    );
    const { rerender } = render(
      <DateTimeRangePicker
        dateOption="30D"
        zoomRange={[null, null]}
        setDateOption={setDateOption}
        setParentDateFilter={setParentDateFilter}
      />,
    );
    await waitFor(() => expect(setParentDateFilter).toHaveBeenCalled());
    events.length = 0;
    setParentDateFilter.mockClear();
    setDateOption.mockClear();

    rerender(
      <DateTimeRangePicker
        dateOption="30D"
        zoomRange={["2026-08-11 00:00:00", "2026-08-12 00:00:00"]}
        setDateOption={setDateOption}
        setParentDateFilter={setParentDateFilter}
      />,
    );

    await waitFor(() =>
      expect(events).toEqual([
        {
          type: "range",
          range: ["2026-08-11 00:00:00", "2026-08-12 00:00:00"],
        },
        { type: "option", option: "Custom" },
      ]),
    );
    expect(setParentDateFilter).toHaveBeenCalledTimes(1);
  });
});
