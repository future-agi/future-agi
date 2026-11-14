import React, { useState } from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import DateTimeRangePicker from "../DateTimeRangePicker";
import ChartsDateTimeRangePicker from "../ChartsView/ChartsDateTimeRangePicker";
import UsersDetailDateTimePicker from "../UsersView/UserDetails/usersDetailDateTimePicker";

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => [null, vi.fn()],
}));
vi.mock("../UsersView/common", () => ({
  DateRangeButtonOptions: [{ title: "Custom" }, { title: "7D" }],
  detectTimePeriod: vi.fn(),
}));

function Harness({ kind, onRange }) {
  const [dateOption, setDateOption] = useState("7D");
  const [dateFilter, setDateFilter] = useState({
    dateOption: "7D",
    dateFilter: [
      new Date(2026, 10, 1).toISOString(),
      new Date(2026, 10, 4).toISOString(),
    ],
  });
  if (kind === "users") {
    return (
      <UsersDetailDateTimePicker
        dateFilter={dateFilter}
        setDateFilter={(update) =>
          setDateFilter((prev) => {
            const next = update(prev);
            onRange(next.dateFilter);
            return next;
          })
        }
        setDateInterval={() => {}}
      />
    );
  }
  const Picker =
    kind === "charts" ? ChartsDateTimeRangePicker : DateTimeRangePicker;
  return (
    <Picker
      dateOption={dateOption}
      setDateOption={setDateOption}
      setParentDateFilter={onRange}
    />
  );
}

Harness.propTypes = { kind: PropTypes.string, onRange: PropTypes.func };

describe("date-range consumer apply / close / reopen", () => {
  // Execute in UTC, Asia/Kolkata and America/Los_Angeles processes, not by
  // changing TZ inside jsdom. The dates exercise both DST transitions.
  it.each([
    ["charts", "2026-11-02", 10, 2, "02/11/2026"],
    ["users", "2026-03-08", 2, 8, "08/03/2026"],
    ["general", "2026-11-01", 10, 1, "01/11/2026"],
  ])(
    "%s preserves bounds and excludes the phantom end day",
    async (kind, day, month, date, label) => {
      const onRange = vi.fn();
      render(<Harness kind={kind} onRange={onRange} />);
      fireEvent.click(
        screen.getByRole("button", { name: "Custom", exact: true }),
      );
      // Clear the seeded range first; date entry otherwise deliberately sorts
      // reversed endpoints as the user types.
      for (const input of ["Start Date", "End Date"]) {
        fireEvent.change(screen.getByLabelText(input), {
          target: { value: "" },
        });
      }
      expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
      for (const input of ["Start Date", "End Date"]) {
        fireEvent.change(screen.getByLabelText(input), {
          target: { value: day },
        });
      }
      fireEvent.click(screen.getByRole("button", { name: "Done" }));
      const expected = [
        new Date(2026, month, date).toISOString(),
        new Date(2026, month, date + 1).toISOString(),
      ];
      await waitFor(() => expect(onRange).toHaveBeenLastCalledWith(expected));
      fireEvent.click(
        screen.getByRole("button", { name: `${label} - ${label}` }),
      );
      expect(screen.getByLabelText("Start Date")).toHaveValue(day);
      expect(screen.getByLabelText("End Date")).toHaveValue(day);
      expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Done" }));
      expect(onRange).toHaveBeenLastCalledWith(expected);
    },
  );
});
