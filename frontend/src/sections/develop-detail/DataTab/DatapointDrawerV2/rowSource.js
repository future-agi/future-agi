// Where an imported row came from (`row_meta.source`), as this build may show
// it. Every place that renders a row's source (the Row view pills, its menu
// items and kind tabs, the grid's Source cell) reads the source through
// `shownRowSource`, so a build that hides the row source surface shows no
// source anywhere, whatever the read returns.
import { isSurfaceShown } from "src/launch/launchSwitch";

export const ROW_SOURCE_SURFACE = "dataset.rowExtras.conversation";

/** The row's source, or `null` while the row source surface is hidden. */
export function shownRowSource(source) {
  if (!source) return null;
  return isSurfaceShown(ROW_SOURCE_SURFACE) ? source : null;
}

/** True for a source that names a trace to open (a trace or a span import). */
export function opensTrace(source) {
  const kind = source?.kind ?? source?.type ?? null;
  return (kind === "trace" || kind === "span") && Boolean(source?.trace_id);
}
