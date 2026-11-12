import { describe, expect, it } from "vitest";
import { resolveColumns } from "src/sections/common/ux/oneview/columnLayout";
import {
  DS_EVALS_COLUMNS,
  DS_EVALS_SURFACE,
  dsEvalsMinWidth,
} from "../dsEvalsLayout";

describe("dataset Summary evals default layout (one view, A1-37)", () => {
  const resolved = resolveColumns(DS_EVALS_COLUMNS, {});
  const visible = resolved.filter((c) => !c.hidden).map((c) => c.id);

  it("opens on Eval · Pass rate · Mean · Distribution · Scored · Errors · Failing rows", () => {
    expect(DS_EVALS_SURFACE).toBe("dsEvals");
    expect(visible).toEqual([
      "name",
      "passRate",
      "mean",
      "dist",
      "scored",
      "errors",
      "failing",
    ]);
  });

  it("keeps Version · Dir. · Judge vs human one click away (3 hidden)", () => {
    expect(resolved.filter((c) => c.hidden).map((c) => c.label)).toEqual([
      "Version",
      "Dir.",
      "Judge vs human",
    ]);
    expect(resolved.find((c) => c.id === "name").locked).toBe(true);
  });

  it("sizes the table to its visible columns (fits 1280 by default)", () => {
    expect(dsEvalsMinWidth(visible)).toBeLessThanOrEqual(900);
    const all = DS_EVALS_COLUMNS.map((c) => c.id);
    expect(dsEvalsMinWidth(all)).toBeGreaterThan(dsEvalsMinWidth(visible));
  });
});
