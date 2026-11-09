import { formatDateTime } from "../../utils/formatters";
import { formatLatency } from "../detail/modelFacts";
import { GATEWAY_MONO } from "../../utils/fonts";

// The auto routing page's recent-decisions grid. Minimum widths leave room
// for each label, the column menu and the sort arrow (columns.test.js).
export const decisionColumns = [
  {
    colId: "at",
    field: "at",
    headerName: "Time",
    valueFormatter: ({ value }) => (value ? formatDateTime(value) : "—"),
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
