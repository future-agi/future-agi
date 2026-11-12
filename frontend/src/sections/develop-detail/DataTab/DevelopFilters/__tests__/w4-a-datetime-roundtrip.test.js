/* eslint-env node */
// Use --pool=forks: timezone changes must reach the worker's native Date.
// The offset assertions prevent a UTC-only run from masking this regression.
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { panelFilterToStore, storeFilterToPanel } from "../DevelopFilterBox";
import { transformFilter, validateFilter } from "../common";

const columnLookup = {
  created_at: { panelType: "date", category: "dataset" },
  status: { panelType: "string", category: "dataset" },
};
const dateFilter = (value, operator = "greater_than") => ({
  field: "created_at",
  fieldType: "date",
  operator,
  value,
});
const requestValue = (stored) =>
  transformFilter(stored).filter_config.filter_value;

// These are the dataset exports on both releases. On the extracted-adapter
// release, they also exercise the shared adapter through its dataset wrapper.
describe.each([
  { timezone: "America/Los_Angeles", winterOffset: 480, summerOffset: 420 },
  { timezone: "UTC", winterOffset: 0, summerOffset: 0 },
])("W4-A-003 datetime round-trip in $timezone", ({
  timezone,
  winterOffset,
  summerOffset,
}) => {
  let previousTimezone;

  beforeAll(() => {
    previousTimezone = process.env.TZ;
    process.env.TZ = timezone;
  });

  afterAll(() => {
    if (previousTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimezone;
  });

  beforeEach(() => {
    expect(new Date(2026, 10, 9, 9).getTimezoneOffset()).toBe(winterOffset);
    expect(new Date(2026, 6, 9, 9).getTimezoneOffset()).toBe(summerOffset);
  });

  it("W4-A-003-scalar-reopen keeps the entered local cutoff", () => {
    const entered = dateFilter("2026-11-09T09:00");
    const stored = panelFilterToStore(entered);

    expect(validateFilter(stored)).toBe(true);
    expect(requestValue(stored)).toBe("2026-11-09 09:00:00");
    expect(storeFilterToPanel(stored, columnLookup)).toMatchObject(entered);
  });

  it("W4-A-003-scalar-reapply keeps the cutoff when another predicate changes", () => {
    const stored = [
      panelFilterToStore(dateFilter("2026-11-09T09:00")),
      panelFilterToStore({
        field: "status",
        fieldType: "string",
        operator: "equals",
        value: "open",
      }),
    ];
    const originalRequest = transformFilter(stored[0]);
    const reopened = stored.map((filter) =>
      storeFilterToPanel(filter, columnLookup),
    );
    const reapplied = reopened.map((filter) =>
      panelFilterToStore(
        filter.field === "status" ? { ...filter, value: "closed" } : filter,
      ),
    );

    expect(requestValue(reapplied[1])).toBe("closed");
    expect(transformFilter(reapplied[0])).toEqual(originalRequest);
    expect(requestValue(reapplied[0])).toBe("2026-11-09 09:00:00");
    expect(reapplied[0].filterConfig.filterValue.getTime()).toBe(
      stored[0].filterConfig.filterValue.getTime(),
    );
  });

  it.each(["between", "not_between"])(
    "W4-A-003-range-reopen preserves both %s endpoints",
    (operator) => {
      const entered = dateFilter(
        ["2026-11-09T09:00", "2026-11-09T12:00"],
        operator,
      );
      const stored = panelFilterToStore(entered);

      expect(validateFilter(stored)).toBe(true);
      expect(storeFilterToPanel(stored, columnLookup)).toMatchObject(entered);
    },
  );

  it.each(["between", "not_between"])(
    "W4-A-003-range-reapply preserves both %s request cutoffs",
    (operator) => {
      const stored = panelFilterToStore(
        dateFilter(["2026-11-09T09:00", "2026-11-09T12:00"], operator),
      );
      const reopened = storeFilterToPanel(stored, columnLookup);
      const reapplied = panelFilterToStore(reopened);

      expect(requestValue(stored)).toEqual([
        "2026-11-09 09:00:00",
        "2026-11-09 12:00:00",
      ]);
      expect(validateFilter(reapplied)).toBe(true);
      expect(transformFilter(reapplied)).toEqual(transformFilter(stored));
      expect(reapplied.filterConfig.filterValue.map((date) => date.getTime()))
        .toEqual(stored.filterConfig.filterValue.map((date) => date.getTime()));
    },
  );

  it.each(["2026-11-09T23:45", "2026-07-09T00:05"])(
    "W4-A-003-local-boundary preserves %s through repeated reapplication",
    (value) => {
      const original = panelFilterToStore(dateFilter(value));
      const reapplied = [0, 1, 2].reduce(
        (stored) => panelFilterToStore(storeFilterToPanel(stored, columnLookup)),
        original,
      );

      expect(storeFilterToPanel(reapplied, columnLookup).value).toBe(value);
      expect(transformFilter(reapplied)).toEqual(transformFilter(original));
    },
  );

  it.each([
    ["", ""],
    ["2026-11-09", "2026-11-09T00:00"],
    ["2026-11-09T09:00:00", "2026-11-09T09:00"],
  ])("W4-A-003-format-control leaves stored string %s unchanged", (value, expected) => {
    const stored = {
      columnId: "created_at",
      filterConfig: {
        filterType: "datetime",
        filterOp: "equals",
        filterValue: value,
      },
    };

    expect(storeFilterToPanel(stored, columnLookup).value).toBe(expected);
  });
});
