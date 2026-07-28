import SvgJs from "svg.js";

const isUsableSvgJs = (value) =>
  Boolean(value) &&
  typeof value.invent === "function" &&
  typeof value.Doc === "function";

export const ensureApexChartsSvgGlobal = () => {
  if (typeof window === "undefined") return;

  // ApexCharts 3.x references `SVG` as a free browser global inside its
  // bundled module and its svg.filter plugin expects svg.js to already be
  // installed there.
  if (!isUsableSvgJs(window.SVG)) {
    window.SVG = SvgJs;
  }
};

ensureApexChartsSvgGlobal();
