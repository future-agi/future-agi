import { DEFAULT_WIDGET_HEIGHT } from "./constants";

const MAX_WIDGETS_PER_ROW = 4;

/** Group a flat sorted widget list into rows based on cumulative widths.
 *  Widgets in each row are normalized so their widths sum to exactly 12. */
export function computeRows(widgets) {
  const sorted = [...widgets].sort((a, b) => a.position - b.position);
  const rows = [];
  let currentRow = [];
  let rowWidth = 0;
  for (const w of sorted) {
    const width = w.width || 12;
    if (rowWidth + width > 12 && currentRow.length > 0) {
      rows.push(currentRow);
      currentRow = [{ ...w, width }];
      rowWidth = width;
    } else {
      currentRow.push({ ...w, width });
      rowWidth += width;
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);

  return rows;
}

/**
 * Where a new widget goes when it is added to the row holding `anchorWidgetId`
 * (the row "+" on a dashboard). Returns the new widget's layout and a builder
 * for the reorder payload that shares the row's width evenly, or null when the
 * row no longer exists or is already full.
 */
export function getAddToRowPlacement(widgets, anchorWidgetId) {
  if (!anchorWidgetId) return null;
  const rows = computeRows(widgets || []);
  const rowIdx = rows.findIndex((row) =>
    row.some((w) => String(w.id) === String(anchorWidgetId)),
  );
  const row = rows[rowIdx];
  if (!row || row.length >= MAX_WIDGETS_PER_ROW) return null;

  // Redistribute evenly across the row plus the new widget.
  const newCount = row.length + 1;
  const perWidget = Math.floor(12 / newCount);
  const remainder = 12 - perWidget * newCount;
  // Just after the last widget in this row.
  const lastPos = row[row.length - 1]?.position ?? 0;

  return {
    layout: {
      width: perWidget,
      height: row[0]?.height || DEFAULT_WIDGET_HEIGHT,
      position: lastPos + 1,
    },
    orderWith: (newWidgetId) => {
      const order = [];
      for (let ri = 0; ri < rows.length; ri++) {
        for (let wi = 0; wi < rows[ri].length; wi++) {
          const w = rows[ri][wi];
          order.push({
            id: w.id,
            width:
              ri === rowIdx
                ? perWidget + (wi < remainder ? 1 : 0)
                : w.width || 12,
          });
        }
        // The new widget goes at the end of the target row.
        if (ri === rowIdx) {
          order.push({
            id: newWidgetId,
            width: perWidget + (row.length < remainder ? 1 : 0),
          });
        }
      }
      return order;
    },
  };
}
