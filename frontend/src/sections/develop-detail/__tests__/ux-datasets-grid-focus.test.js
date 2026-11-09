import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// A non-literal URL argument avoids Vite/Vitest's browser asset URL rewrite.
const globalCssPath = "../../../global.css";
const css = readFileSync(new URL(globalCssPath, import.meta.url), "utf8");
const cellRules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(
  ([, selectors]) => /\.ag-cell(?::focus|-focus)/.test(selectors),
);

describe("D01 dataset grid focus", () => {
  it("does not suppress the cell outline with an important reset", () => {
    for (const [, , declarations] of cellRules) {
      expect(declarations).not.toMatch(/outline:\s*(none|0)\s*!important/);
    }
  });

  it("draws an inset semantic focus outline on the dataset grid's active cell", () => {
    const focusedCell = cellRules.find(([, selectors]) =>
      selectors
        .split(",")
        .some(
          (selector) => selector.trim() === ".dataset-table .ag-cell-focus",
        ),
    );
    expect(focusedCell).toBeDefined();
    expect(focusedCell?.[2]).toMatch(
      /outline:\s*2px solid var\(--ds-border-focus\)/,
    );
    expect(focusedCell?.[2]).toMatch(/outline-offset:\s*-2px/);
  });
});
