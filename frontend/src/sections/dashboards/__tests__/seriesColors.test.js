import { describe, expect, it } from "vitest";
import {
  DASHBOARD_SERIES_COLORS,
  buildSeriesColorMap,
  getSeriesColor,
} from "../seriesColors";

describe("dashboard series colors", () => {
  it("handles object prototype property names safely", () => {
    const names = ["__proto__", "constructor", "toString", "hasOwnProperty"];
    const colorMap = buildSeriesColorMap(names);

    for (const name of names) {
      expect(getSeriesColor(colorMap, name)).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });

  it("does not consume additional palette slots for duplicate names", () => {
    const colorMap = buildSeriesColorMap(["same", "same", "different"]);

    expect(colorMap.size).toBe(2);
    expect(getSeriesColor(colorMap, "same")).not.toBe(
      getSeriesColor(colorMap, "different"),
    );
  });

  it("terminates and returns colors when series exceed the palette", () => {
    const names = Array.from({ length: 10_000 }, (_, index) => `series-${index}`);
    const colorMap = buildSeriesColorMap(names);

    expect(colorMap.size).toBe(names.length);
    expect(getSeriesColor(colorMap, names.at(-1))).toBeTruthy();
  });

  it("returns safely when an empty palette is supplied", () => {
    const colorMap = buildSeriesColorMap(["series"], []);

    expect(colorMap.size).toBe(0);
    expect(getSeriesColor(colorMap, "series", [])).toBeUndefined();
  });

  it("keeps assignments deterministic for the same ordered names", () => {
    const names = ["alpha", "beta", "gamma"];
    const first = buildSeriesColorMap(names);
    const second = buildSeriesColorMap(names);

    expect([...first.entries()]).toEqual([...second.entries()]);
    expect(new Set(first.values()).size).toBe(names.length);
    expect(DASHBOARD_SERIES_COLORS).toHaveLength(10);
  });
});
