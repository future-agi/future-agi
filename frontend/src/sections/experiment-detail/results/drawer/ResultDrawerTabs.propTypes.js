import PropTypes from "prop-types";

// Props every result drawer tab receives (frozen at L0; PRODUCT-SPEC §3.11).
export const RESULT_DRAWER_TAB_PROPTYPES = {
  // dataset the results belong to
  datasetId: PropTypes.string,
  // R3 row in full mode (`&example_id=&full=true`): example_id, row_order,
  // inputs, expected, results{[runId]: {..., trials_detail[]}}, preference,
  // neighbors
  row: PropTypes.object,
  // canonical Run objects in view, baseline first
  runs: PropTypes.arrayOf(PropTypes.object),
  baselineId: PropTypes.string,
  // the candidate run the drawer focuses (`run=`)
  focusedRunId: PropTypes.string,
  // 1-based trial (`trial=`)
  trial: PropTypes.number,
  // blind review (`blind=1`)
  blind: PropTypes.bool,
  // move to the next row of the filtered, sorted set (auto-advance)
  onNext: PropTypes.func,
};
