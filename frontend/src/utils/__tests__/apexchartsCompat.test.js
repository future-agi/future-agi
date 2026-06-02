import { describe, expect, it } from "vitest";
import { ensureApexChartsSvgGlobal } from "../apexchartsCompat";

describe("ensureApexChartsSvgGlobal", () => {
  it("creates the global SVG binding expected by ApexCharts", () => {
    const hadSvg = Object.prototype.hasOwnProperty.call(window, "SVG");
    const originalSvg = window.SVG;

    try {
      delete window.SVG;

      ensureApexChartsSvgGlobal();

      expect(Object.prototype.hasOwnProperty.call(window, "SVG")).toBe(true);
      expect(window.SVG).toBeUndefined();
    } finally {
      if (hadSvg) {
        window.SVG = originalSvg;
      } else {
        delete window.SVG;
      }
    }
  });
});
