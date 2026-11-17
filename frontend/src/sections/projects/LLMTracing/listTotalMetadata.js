import {
  getQueryReadMessage,
  getQueryReadState,
} from "src/utils/queryReadState";
import { listWindowWrap } from "./listWindowWrap";

const normalizeRowCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0;
};

/**
 * List rows returned by the bounded selectors are individually proven exact.
 * A sampled/lower-bound aggregate marker therefore must not turn those rows
 * into an "incomplete" UI state or produce a sampling banner. Keep a retry
 * message only for a genuinely degraded empty page or a transport failure.
 */
export const getListReadMessage = (payload, { isError = false } = {}) => {
  const readState = getQueryReadState(payload, { isError });
  const rows = payload?.result?.table ?? payload?.table;
  if (Array.isArray(rows) && rows.length > 0) return null;
  if (readState === "sampled") return null;
  return getQueryReadMessage(readState);
};

/**
 * Keep an API lower bound separate from an exact row count. Consumers that
 * require an exact total should only use totalRowCount.
 */
export const getListTotalState = (metadata = {}) => {
  const totalRowCountIsLowerBound =
    metadata?.total_rows_is_lower_bound === true;
  const reportedTotal = normalizeRowCount(metadata?.total_rows);

  return {
    totalRowCount: totalRowCountIsLowerBound ? null : reportedTotal,
    totalRowCountLowerBound: totalRowCountIsLowerBound ? reportedTotal : null,
    totalRowCountIsLowerBound,
  };
};

/**
 * In server-side select-all mode toggledNodes contains exclusions, so
 * subtracting them from a reported lower bound remains a lower bound. An exact
 * total can leave nothing selected (an empty list, or every row unticked);
 * only a total that is not known yet keeps the one-row floor.
 */
export const getSelectionCountState = ({
  selectAll,
  toggledNodes,
  totalRowCount,
  totalRowCountLowerBound,
  totalRowCountIsLowerBound,
}) => {
  const toggledCount = Array.isArray(toggledNodes) ? toggledNodes.length : 0;
  if (!selectAll) {
    return { count: toggledCount, isLowerBound: false };
  }

  const reportedTotal = totalRowCountIsLowerBound
    ? totalRowCountLowerBound
    : totalRowCount;
  const totalIsKnown =
    Boolean(totalRowCountIsLowerBound) ||
    (typeof totalRowCount === "number" && Number.isFinite(totalRowCount));

  return {
    count: Math.max(
      normalizeRowCount(reportedTotal) - toggledCount,
      totalIsKnown ? 0 : 1,
    ),
    isLowerBound: totalRowCountIsLowerBound === true,
  };
};

// The setup guide the Observe list's first-run state links to.
export const OBSERVE_SETUP_DOCS_URL = "https://docs.futureagi.com/docs/observe";

/**
 * Description and actions for an exact empty Trace/Spans page: name the time
 * range that was read and offer one next step. Chip filters that matched
 * nothing get "Clear filters"; otherwise a longer range (when the caller has
 * one) and the setup guide. Without a range label it returns {}, so grids that
 * pass none keep their bare title. An unfiltered list given a listWindow
 * (buildListWindow) also gets a wrap: once the project's last trace is read and
 * falls outside the window, that state replaces this one.
 */
export const getEmptyListOverlay = ({
  noun,
  rangeLabel,
  hasFilters = false,
  onClearFilters,
  onWidenRange,
  listWindow,
}) => {
  if (!rangeLabel) return {};
  const range = `the selected time range (${rangeLabel})`;
  if (hasFilters) {
    return {
      description: `No ${noun} match these filters in ${range}.`,
      actions: onClearFilters
        ? [{ label: "Clear filters", onClick: onClearFilters }]
        : [],
    };
  }
  return {
    description: `There are no ${noun} in ${range}. Try a longer range, or check that your app is sending traces to this project.`,
    actions: [
      ...(onWidenRange
        ? [{ label: "Show past 30 days", onClick: onWidenRange }]
        : []),
      { label: "Set up tracing", href: OBSERVE_SETUP_DOCS_URL },
    ],
    ...(listWindow ? { wrap: listWindowWrap({ noun, listWindow }) } : {}),
  };
};

export const formatSelectionCount = ({ count, isLowerBound }) =>
  `${isLowerBound ? "≥" : ""}${normalizeRowCount(count).toLocaleString()}`;
