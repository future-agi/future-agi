// W7 A0: a typed cell's own details (the results drawer's ScoreCard, integration
// S7; the grid tooltip with `compact`, S8). Dispatches on cell.typed.kind;
// a type whose view is not real yet shows nothing.
import React from "react";
import PropTypes from "prop-types";
import CompositeScoreDetails from "./CompositeScoreDetails";
import DagScoreDetails from "./DagScoreDetails";
import RemoteScoreDetails from "./RemoteScoreDetails";

const DETAILS = {
  composite: CompositeScoreDetails,
  dag: DagScoreDetails,
  remote: RemoteScoreDetails,
};

export default function TypedScoreDetails({ cell, compact = false }) {
  const View = DETAILS[cell?.typed?.kind];
  if (!View?.ready) return null;
  return <View cell={cell} compact={compact} />;
}

TypedScoreDetails.propTypes = {
  cell: PropTypes.object,
  compact: PropTypes.bool,
};
