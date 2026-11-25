import { describe, expect, it } from "vitest";
import { createTheme, darken, lighten, rgbToHex } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { seriesColors } from "src/theme/data-colors";
import { buildChartSeriesColorMap, buildSeriesColorMap } from "../chartColors";

const rows = (labels, count = 2) =>
  labels.flatMap((breakdownName) =>
    Array.from({ length: count }, (_, metricIndex) => ({
      name: `metric ${metricIndex} / ${breakdownName}`,
      metricIndex,
      breakdownName,
    })),
  );

describe.each(["light", "dark"])("dashboard colors (%s)", (mode) => {
  const colors = seriesColors(createTheme({ palette: palette(mode) }));
  const resolve = (series, type = "line") =>
    buildChartSeriesColorMap(series, colors, mode, type);
  it("uses a shared breakdown hue and metric-specific shade", () => {
    // A and I collide modulo eight. The alphabetical walk assigns slots 1, 2.
    const map = resolve(rows(["I", "A"]));
    for (const [group, base] of [
      ["A", colors[1]],
      ["I", colors[2]],
    ]) {
      expect(map[`metric 0 / ${group}`]).toBe(base);
      expect(map[`metric 1 / ${group}`]).toBe(
        rgbToHex(mode === "light" ? darken(base, 0.32) : lighten(base, 0.35)),
      );
    }
  });
  it("retains the same colors after reordering colliding breakdowns", () => {
    const series = rows(["A", "I"]);
    expect(resolve(series.slice().reverse())).toEqual(resolve(series));
  });
  it("gives all five metrics distinct shades even for the grey slot", () => {
    const values = Object.values(resolve(rows(["G"], 5)));
    expect(values[0]).toBe(colors[7]);
    expect(new Set(values).size).toBe(5);
  });
  it("keeps total, absent and empty breakdowns on their original label colors", () => {
    const series = [
      { name: "A", breakdownName: "total" },
      { name: "I" },
      { name: "Q", breakdownName: "" },
      ...rows(["A"]),
    ];
    const map = resolve(series);
    expect([map.A, map.I, map.Q]).toEqual(colors.slice(1, 4));
    expect(map["metric 0 / A"]).toBe(colors[1]);
    expect(Object.hasOwn(map, "undefined")).toBe(false);
  });
  it("reuses theme hues beyond the palette capacity without losing a series", () => {
    const series = rows(Array.from("ABCDEFGHI"), 1);
    const values = Object.values(resolve(series));
    expect(values).toHaveLength(9);
    expect(values[8]).toBe(colors[1]);
    expect(new Set(values)).toEqual(new Set(colors));
  });
  it("leaves pie slice and undivided series colors in their original order", () => {
    const names = ["I", "A"];
    expect(Object.values(buildSeriesColorMap(names, colors))).toEqual(
      colors.slice(1, 3),
    );
    expect(
      Object.values(
        resolve(names.map((name) => ({ name, breakdownName: "total" }))),
      ),
    ).toEqual(colors.slice(1, 3));
    expect(resolve(rows(names))["metric 0 / A"]).toBe(colors[1]);
  });
});
