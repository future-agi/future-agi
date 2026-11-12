/* eslint-env node */
// Use --pool=forks: timezone changes must reach the worker's native Date.
// The offset assertions prevent a UTC-only run from masking this regression.
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { panelFilterToStore, storeFilterToPanel } from "../storeFilterAdapter";
import {
  buildFilterLeaves,
  fromPanelValue,
  toPanelValue,
} from "../complexFilterAdapter";

const columnLookup = {
  created_at: { panelType: "date", category: "dataset" },
};
const dateFilter = (value, operator) => ({
  field: "created_at",
  fieldType: "date",
  operator,
  value,
});
const definition = [
  {
    propertyName: "Created",
    propertyId: "created_at",
    filterType: { type: "date" },
  },
];

// A day picked without a time (date-only 'yyyy-MM-dd') must mean that day in
// the user's own time zone: the lower bound at local midnight and a range's
// upper bound at the end of the picked day, never UTC midnight.
describe.each([
  { timezone: "America/Los_Angeles", winterOffset: 480, summerOffset: 420 },
  { timezone: "UTC", winterOffset: 0, summerOffset: 0 },
])(
  "W10-FILTERS-U05 date-only cutoffs in $timezone",
  ({ timezone, winterOffset, summerOffset }) => {
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

    it.each(["between", "not_between"])(
      "W10-FILTERS-U05-store-range keeps a picked %s day range on local days",
      (operator) => {
        const stored = panelFilterToStore(
          dateFilter(["2026-11-01", "2026-11-05"], operator),
        );

        expect(
          stored.filterConfig.filterValue.map((date) => date.getTime()),
        ).toEqual([
          new Date(2026, 10, 1, 0, 0).getTime(),
          new Date(2026, 10, 5, 23, 59).getTime(),
        ]);
        expect(storeFilterToPanel(stored, columnLookup).value).toEqual([
          "2026-11-01T00:00",
          "2026-11-05T23:59",
        ]);
      },
    );

    it("W10-FILTERS-U05-store-single keeps a picked day at local midnight", () => {
      const stored = panelFilterToStore(
        dateFilter("2026-11-01", "greater_than"),
      );

      expect(stored.filterConfig.filterValue.getTime()).toBe(
        new Date(2026, 10, 1, 0, 0).getTime(),
      );
      expect(storeFilterToPanel(stored, columnLookup).value).toBe(
        "2026-11-01T00:00",
      );
    });

    it("W10-FILTERS-U05-store-control leaves picked date and time unchanged", () => {
      const entered = dateFilter(
        ["2026-11-01T09:30", "2026-11-05T17:00"],
        "between",
      );
      const stored = panelFilterToStore(entered);

      expect(storeFilterToPanel(stored, columnLookup)).toMatchObject(entered);
    });

    it("W10-FILTERS-U05-complex-range keeps a picked day range on local days", () => {
      const leaves = buildFilterLeaves(definition, []);
      const stored = fromPanelValue(
        [
          {
            field: "created_at",
            operator: "between",
            value: ["2026-11-01", "2026-11-05"],
          },
        ],
        leaves,
      );

      expect(
        stored[0].filter_config.filter_value.map((date) => date.getTime()),
      ).toEqual([
        new Date(2026, 10, 1, 0, 0).getTime(),
        new Date(2026, 10, 5, 23, 59).getTime(),
      ]);
      expect(toPanelValue(stored, leaves)[0].value).toEqual([
        "2026-11-01T00:00",
        "2026-11-05T23:59",
      ]);
    });

    it("W10-FILTERS-U05-complex-single keeps a picked day at local midnight", () => {
      const leaves = buildFilterLeaves(definition, []);
      const stored = fromPanelValue(
        [
          {
            field: "created_at",
            operator: "greater_than",
            value: "2026-11-01",
          },
        ],
        leaves,
      );

      expect(toPanelValue(stored, leaves)[0].value).toBe("2026-11-01T00:00");
    });
  },
);
