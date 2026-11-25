import {
  TRACE_COLUMNS,
  defaultTraceColumns,
} from "../detail/trace/traceTable.constants";
import { COMPARE_COPY } from "../../workspace.constants";

// The run detail's columns plus a compare-only Status column: each call's
// verdict and how many sub-goals it met.
export const STATUS_COLUMN = {
  key: "status",
  label: COMPARE_COPY.statusColumn,
  defaultOn: true,
  group: "Run details",
};
export const COMPARE_COLUMNS = [STATUS_COLUMN, ...TRACE_COLUMNS];

export const defaultCompareColumns = () =>
  new Set([...defaultTraceColumns(), STATUS_COLUMN.key]);

// A scenario row's own columns, drawn once across its run lines. Scenario is
// the row's leading column, so the picker doesn't offer it again.
export const SHARED_COLUMNS = new Set([
  "persona",
  "situation",
  "subGoals",
  "idealOutcome",
  "conversationBranch",
]);
export const ROW_LEAD_COLUMNS = new Set(["scenario"]);

// The fixed columns a line renders, in order.
export const COMPARE_CELL_KEYS = COMPARE_COLUMNS.filter(
  (c) => c.key !== "subGoalEvals" && !ROW_LEAD_COLUMNS.has(c.key),
).map((c) => c.key);

export const MIN_COMPARE_RUNS = 2;

// The runs in the URL, in order, unique and capped.
export const parseRunIds = (value, max) =>
  [
    ...new Set(
      (value || "")
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean),
    ),
  ].slice(0, max);
