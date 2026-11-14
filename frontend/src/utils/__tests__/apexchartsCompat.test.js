import { describe, expect, it } from "vitest";
import { ensureApexChartsSvgGlobal } from "../apexchartsCompat";

describe("ensureApexChartsSvgGlobal", () => {
  it("installs the svg.js global expected by ApexCharts", () => {
    const hadSvg = Object.prototype.hasOwnProperty.call(window, "SVG");
    const originalSvg = window.SVG;

    try {
      delete window.SVG;

      ensureApexChartsSvgGlobal();

      expect(Object.prototype.hasOwnProperty.call(window, "SVG")).toBe(true);
      expect(typeof window.SVG.invent).toBe("function");
      expect(typeof window.SVG.Doc).toBe("function");
    } finally {
      if (hadSvg) {
        window.SVG = originalSvg;
      } else {
        delete window.SVG;
      }
    }
  });

  it("keeps an existing usable svg.js global", () => {
    const hadSvg = Object.prototype.hasOwnProperty.call(window, "SVG");
    const originalSvg = window.SVG;
    const existingSvg = { invent: () => {}, Doc: function Doc() {} };

    try {
      window.SVG = existingSvg;

      ensureApexChartsSvgGlobal();

      expect(window.SVG).toBe(existingSvg);
    } finally {
      if (hadSvg) {
        window.SVG = originalSvg;
      } else {
        delete window.SVG;
      }
    }
  });
});
