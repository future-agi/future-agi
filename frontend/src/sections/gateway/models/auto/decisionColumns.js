import { createElement } from "react";
import PropTypes from "prop-types";

import { formatDateTime } from "../../utils/formatters";
import { exactLogTime } from "../../logs/logDates";
import { formatLatency } from "../detail/modelFacts";
import { GATEWAY_MONO } from "../../utils/fonts";
import { decisionOutcome, instantOf } from "./autoDecisions";

// A decision's time: the date and minute, with the exact instant and its
// zone on hover, as Logs shows a request's time.
function DecisionTimeCell({ value }) {
  if (!value) return "—";
  return createElement(
    "span",
    { "data-testid": "decision-time", title: exactLogTime(value) },
    formatDateTime(value),
  );
}
DecisionTimeCell.propTypes = { value: PropTypes.string };

// By instant, not text (stamps carry offsets and fractions); a tie by row id.
const byInstant = (a, b, nodeA, nodeB) => {
  const ta = instantOf(a) ?? -Infinity;
  const tb = instantOf(b) ?? -Infinity;
  if (ta !== tb) return ta < tb ? -1 : 1;
  return String(nodeA?.data?.id ?? "").localeCompare(
    String(nodeB?.data?.id ?? ""),
  );
};

// The auto routing page's recent-decisions grid. Minimum widths leave room
// for each label, the column menu and the sort arrow (columns.test.js).
export const decisionColumns = [
  {
    colId: "at",
    field: "at",
    headerName: "Time",
    cellRenderer: DecisionTimeCell,
    comparator: byInstant,
    sort: "desc",
    minWidth: 200,
  },
  {
    colId: "chosen",
    field: "chosen",
    headerName: "Chosen model",
    minWidth: 220,
    flex: 2,
    cellStyle: { fontFamily: GATEWAY_MONO },
  },
  {
    colId: "outcome",
    headerName: "Request outcome",
    valueGetter: ({ data }) => decisionOutcome(data),
    minWidth: 250,
  },
  { colId: "tier", field: "tier", headerName: "Task", minWidth: 100 },
  {
    colId: "preference",
    field: "preference",
    headerName: "Preference",
    minWidth: 140,
  },
  {
    colId: "considered",
    field: "considered",
    headerName: "Considered",
    type: "rightAligned",
    minWidth: 140,
  },
  {
    colId: "fallbacks",
    field: "fallbacks",
    headerName: "Fallbacks",
    valueFormatter: ({ value }) => (value?.length ? value.join(", ") : "—"),
    sortable: false,
    minWidth: 220,
    flex: 2,
  },
  {
    colId: "latencyMs",
    field: "latencyMs",
    headerName: "Latency",
    valueFormatter: ({ value }) => (value == null ? "—" : formatLatency(value)),
    type: "rightAligned",
    minWidth: 116,
  },
];

// Where the columns run past the grid's edge (1440 px and narrower, beside
// the page's other sections) Time stays pinned in view, at a fixed width (a
// pinned column with flex takes a share of the grid). Otherwise (a phone)
// null unpins it: AG Grid keeps a pin a column def leaves undefined.
export function decisionColumnsFor({ narrow = false } = {}) {
  return decisionColumns.map((column) => {
    if (column.colId !== "at") return column;
    return narrow
      ? { ...column, pinned: "left", width: column.minWidth }
      : { ...column, pinned: null };
  });
}
