// Room checks for the gateway's AG Grids, from measurements in Chromium at
// 1440 px on the Future AGI grid theme (theme/ag-theme.js: headers 500 14px
// Inter, cells 400 12px Inter, both bundled), measured in a browser on the
// Logs grid at 1440 px, light theme.
//
// A header cell keeps 32 px of padding, 24 px for the column-menu button when
// the grid shows it (GatewayGrid does unless a column suppresses it), and
// 24 px for the sort arrow once the column is sorted. A column whose minWidth
// is less than its label plus that room ends in an ellipsis whenever the grid
// sits at its minimum widths: at 1440 with many columns, and at 390.
//
// Use with the fake grid: render the page, then check `mountedGrids()`.

export const HEADER_PADDING_PX = 32;
export const HEADER_MENU_PX = 24;
export const HEADER_SORT_PX = 24;
// Cells measured 30 px (15 + 15); rounded up.
export const CELL_PADDING_PX = 32;

// Header label widths, rounded up plus 1 px for subpixel differences. A label
// missing here needs Infinity, so a new column fails until it is measured.
export const HEADER_TEXT_PX = {
  "": 0,
  Agent: 41,
  Application: 77,
  Attempts: 62,
  "Avg latency": 79,
  "Billing mode": 83,
  "Cache hits": 72,
  Channel: 57,
  Cooldown: 69,
  Cost: 33,
  "Cost / request": 96,
  "Credential type": 104,
  Duration: 58,
  Endpoint: 61,
  "Error rate": 64,
  "Error rate (24 h)": 108,
  Errors: 42,
  "Fires when": 75,
  Format: 48,
  Group: 43,
  "Last call": 57,
  // Not browser-measured: Inter 500 14 px advance widths (83.2 px, no
  // kerning; computed from the font file), rounded up plus 2 px. The same
  // method reads 5 of 7 measured labels exactly and the others within 2 px.
  "Last request": 86,
  "Last seen": 66,
  Latency: 55,
  Markup: 53,
  Model: 43,
  Models: 51,
  Name: 41,
  Notifies: 53,
  "P95 latency": 80,
  Provider: 58,
  Requests: 64,
  "Requests (24 h)": 107,
  Rule: 31,
  Run: 27,
  "Sample message": 116,
  Service: 52,
  Session: 54,
  "Session ID": 72,
  // Not browser-measured: the measured "Service" (52) with its "c" taken
  // out and "t" and "y" added, at Inter 500 14 px (about 56.7 px), rounded
  // up plus 3 px.
  Severity: 60,
  Share: 40,
  Started: 50,
  State: 36,
  Status: 44,
  Step: 32,
  Steps: 40,
  "Time in calls": 85,
  Timestamp: 76,
  Tokens: 50,
  "Tokens / s": 70,
  Type: 35,
  "Upstream cost": 99,
  "Used by": 56,
  // Not browser-measured: Inter 500 14 px advance widths from the bundled
  // font file, no kerning, rounded up plus 1 px.
  Created: 55,
  Events: 47,
  "Last delivery": 87,
  Webhook: 66,
  // Guardrails > Rules. Not browser-measured: Inter 500 14 px advance
  // widths from the bundled font file (@fontsource/inter latin 500), no
  // kerning, rounded up plus 1 px. The same method reads the measured
  // "Status", "Rule" and "Service" above exactly.
  Accuracy: 66,
  Action: 45,
  Blocked: 55,
  Matches: 59,
  On: 21,
  "Runs on": 56,
  Threshold: 69,
};

// Cell strings in the cell font (tabular numbers), rounded up plus 1 px.
export const CELL_TEXT_PX = {
  // The widest cost cell is "$0.000999" (formatCostCell: "<$0.0001" below
  // that, cents from $1). Budgeted at the width measured for "$0.0000050",
  // one character wider, so the Cost column keeps room to spare.
  "$0.000999": 75,
  // formatLogTime, the widest digits.
  "Oct 20 23:59:59.999": 123,
  // The widest step chain in the demo data (Runs > Steps), as rendered.
  "demo steps, widest": 195,
  // Not browser-measured: Inter 400 12 px advance widths
  //, rounded up plus 1 px.
  // The provider label of a managed call, and the
  // longest model id in the demo data.
  "Future AGI managed": 118,
  "meta-llama/llama-3.1-8b-instruct": 189,
  // An API key's two-ended prefix in the gateway mono at s2 (12 px). Not
  // browser-measured: 20 cells of a monospace face, at most 0.602 em each
  // (Menlo, DejaVu Sans Mono; Fira Code and JetBrains Mono are 0.6),
  // rounded up plus 1 px.
  "sk-agentcc-4f2a…9c1d": 146,
};

// Status chip labels (GatewayStatusChip, no icon) in the chip font, Inter 400
// 11 px (typography s3). Not browser-measured: Inter's advance widths from
// the bundled font file (@fontsource/inter latin 400), no kerning, rounded up
// plus 1 px. The same method reads "Future AGI managed" above at 116.7 px.
export const CHIP_TEXT_PX = {
  "Closed for good": 85,
  "Not enforced": 70,
};
// A chip's own room around its label: 2 px left padding (no icon), the
// label's 6 + 8 px padding and a 1 px border each side (CustomStatusChip).
export const CHIP_CHROME_PX = 18;

/** The room a cell needs to show this status chip whole. */
export function chipNeeds(label) {
  return (CHIP_TEXT_PX[label] ?? Infinity) + CHIP_CHROME_PX + CELL_PADDING_PX;
}

// Grid widths at 1440 px (gateway rail and page frame padding taken off),
// less a 17 px classic vertical scrollbar: a grid whose columns' minimum
// widths add up to more scrolls sideways at 1440.
export const GRID_WIDTH_1440 = { page: 1206, analytics: 1180 };
export const SCROLLBAR_PX = 17;
export const gridBudget1440 = (kind = "page") =>
  GRID_WIDTH_1440[kind] - SCROLLBAR_PX;

/** Every column as the grid sees it (each def over the defaults), hidden ones too. */
export function allColumns(gridProps) {
  const defaults = gridProps.defaultColDef || {};
  return (gridProps.columnDefs || []).map((col) => ({ ...defaults, ...col }));
}

/** The visible columns as the grid sees them. */
export function effectiveColumns(gridProps) {
  return allColumns(gridProps).filter((col) => !col.hide);
}

/** The narrowest a column gets: its minWidth, or its width when it does not flex. */
export function minimumWidth(col) {
  return Math.max(col.minWidth ?? 0, col.flex ? 0 : col.width ?? 0);
}

/** The room a column's header needs: label, padding, menu and sort arrow. */
export function headerNeeds(col) {
  const label = col.headerName ?? "";
  const text = HEADER_TEXT_PX[label] ?? Infinity;
  return (
    text +
    HEADER_PADDING_PX +
    (col.suppressHeaderMenuButton ? 0 : HEADER_MENU_PX) +
    (col.sortable ? HEADER_SORT_PX : 0)
  );
}

/**
 * Columns whose header would be cut off at their minimum width. Hidden
 * columns count too: the user can show them.
 */
export function cutHeaders(gridProps) {
  return allColumns(gridProps)
    .filter((col) => minimumWidth(col) < headerNeeds(col))
    .map(
      (col) =>
        `${col.headerName || col.colId}: ${minimumWidth(col)} < ${headerNeeds(col)}`,
    );
}

/** The sum of the visible columns' minimum widths. */
export function minimumGridWidth(gridProps) {
  return effectiveColumns(gridProps).reduce(
    (sum, col) => sum + minimumWidth(col),
    0,
  );
}

/** The column with this colId (or field), as the grid sees it. */
export function columnOf(gridProps, id) {
  return allColumns(gridProps).find((col) => (col.colId || col.field) === id);
}

/**
 * Each visible column's width in a grid this wide, the way AG Grid flexes
 * them: fixed columns keep their width; flexing ones share the rest by flex,
 * and one that would fall under its minWidth (or over its maxWidth) is held
 * there while the others share what is left.
 */
export function flexWidths(gridProps, total) {
  const idOf = (col) => col.colId || col.field;
  const widths = {};
  let space = total;
  let flexing = [];
  for (const col of effectiveColumns(gridProps)) {
    if (col.flex) flexing.push(col);
    else {
      widths[idOf(col)] = minimumWidth(col);
      space -= widths[idOf(col)];
    }
  }
  for (;;) {
    const totalFlex = flexing.reduce((sum, col) => sum + col.flex, 0);
    const held = flexing.find((col) => {
      const width = (space * col.flex) / totalFlex;
      return width < (col.minWidth ?? 0) || width > (col.maxWidth ?? Infinity);
    });
    if (!held) {
      for (const col of flexing)
        widths[idOf(col)] = (space * col.flex) / totalFlex;
      return widths;
    }
    const width = (space * held.flex) / totalFlex;
    widths[idOf(held)] =
      width < (held.minWidth ?? 0) ? held.minWidth : held.maxWidth;
    space -= widths[idOf(held)];
    flexing = flexing.filter((col) => col !== held);
  }
}
