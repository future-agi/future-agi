import { useEffect } from "react";

/** Keeps an ApexCharts tooltip inside the container that clips it.
 *
 *  Apex places the tooltip entirely above the cursor — `cursorY - gridTop -
 *  tooltipHeight` — and never clamps that at 0; it clamps x three ways and
 *  clamps y only against the grid's bottom. Any point in the top
 *  `tooltipHeight` px of the plot therefore gets a negative top and is drawn
 *  above the canvas, where the surrounding `overflow: hidden` slices it. On a
 *  widget card that is most of the plot: 134px of tooltip against a 230px grid.
 *
 *  The container cannot simply drop the overflow (the chart's ResizeObserver
 *  then loses its height constraint and the canvas grows unbounded),
 *  `tooltip.fixed` is ignored on the intersect path these charts use, and a
 *  chart-level `mouseMove` hook loses the race — Apex rewrites the style after
 *  it, even a frame later. Watching the attribute is what reliably catches the
 *  write, whenever Apex makes it. Only a negative top is rewritten, so a
 *  tooltip that already fits is left following the cursor.
 *
 *  The observer watches the document rather than `containerRef.current`,
 *  because a chart that only renders once its query resolves leaves that ref
 *  empty on mount — an observer attached then would watch nothing for the rest
 *  of the widget's life. Containment is checked per mutation instead, once the
 *  ref is populated, so the clamp stays scoped to this chart. Records are
 *  filtered on the tooltip class first, which keeps the callback off the hot
 *  path of unrelated style writes. */
export default function useClampedChartTooltips(containerRef) {
  useEffect(() => {
    const observer = new MutationObserver((records) => {
      for (const { target } of records) {
        if (!target.classList?.contains("apexcharts-tooltip")) continue;
        if (!containerRef.current?.contains(target)) continue;
        const top = Number.parseFloat(target.style.top);
        if (Number.isFinite(top) && top < 0) target.style.top = "0px";
      }
    });

    observer.observe(document.body, {
      attributes: true,
      subtree: true,
      attributeFilter: ["style"],
    });
    return () => observer.disconnect();
  }, [containerRef]);
}
