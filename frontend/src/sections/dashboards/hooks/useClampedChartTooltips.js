import { useEffect, useRef } from "react";

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
 *  The observer is scoped to `observeRootRef` (defaulting to `containerRef`
 *  itself), not the document: on a dashboard with a dozen widgets, one
 *  body-wide observer per chart is a dozen subtrees being watched for a style
 *  write that only ever happens inside one chart. A chart that only renders
 *  once its query resolves leaves `containerRef` empty on mount, so a caller
 *  whose chart mounts late should pass a stable ancestor as `observeRootRef`
 *  instead — one that is present from the first render. If the resolved root
 *  is still null when the effect runs, this falls back to `document.body`
 *  rather than observing nothing for the rest of the widget's life.
 *  Containment against `containerRef.current` is still checked per mutation,
 *  once that ref is populated, so the clamp stays scoped to this chart.
 *  Records are filtered on the tooltip class first, which keeps the callback
 *  off the hot path of unrelated style writes.
 *
 *  A widget typically swaps `ref={containerRef}` across several conditionally
 *  rendered nodes over its life — a loading spinner, then the chart — so the
 *  resolved root can point at a new, detached node after a later render than
 *  the one the effect first ran on. Re-checking on every render (a cheap
 *  identity comparison) and re-observing when the root changed keeps the
 *  observer attached to whichever node is live, without recreating it. */
export default function useClampedChartTooltips(containerRef, observeRootRef) {
  const observerRef = useRef(null);
  const observedRootRef = useRef(null);

  useEffect(() => {
    if (!observerRef.current) {
      observerRef.current = new MutationObserver((records) => {
        for (const { target } of records) {
          if (!target.classList?.contains("apexcharts-tooltip")) continue;
          if (!containerRef.current?.contains(target)) continue;
          const top = Number.parseFloat(target.style.top);
          if (Number.isFinite(top) && top < 0) target.style.top = "0px";
        }
      });
    }

    const root = (observeRootRef || containerRef).current || document.body;
    if (root !== observedRootRef.current) {
      observerRef.current.disconnect();
      observerRef.current.observe(root, {
        attributes: true,
        subtree: true,
        attributeFilter: ["style"],
      });
      observedRootRef.current = root;
    }
  });

  useEffect(() => {
    return () => observerRef.current?.disconnect();
  }, []);
}
