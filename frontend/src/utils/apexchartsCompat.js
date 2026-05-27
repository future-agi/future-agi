export const ensureApexChartsSvgGlobal = () => {
  if (typeof window === "undefined") return;

  // ApexCharts 3.x references `SVG` as a free browser global inside its
  // bundled module. Defining the global property before chart chunks load keeps
  // chart-heavy routes from crashing without changing individual chart call
  // sites.
  if (!Object.prototype.hasOwnProperty.call(window, "SVG")) {
    window.SVG = undefined;
  }
};

ensureApexChartsSvgGlobal();
