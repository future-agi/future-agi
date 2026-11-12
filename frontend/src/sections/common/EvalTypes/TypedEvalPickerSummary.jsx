// W7 A0: the wizard picker's preview of a typed eval (integration S9): a read-only
// summary of its definition. Typed evals are edited in Evals, never in the
// picker. Dispatches on the eval type; a type whose summary is not real yet
// shows nothing.
import React from "react";
import PropTypes from "prop-types";
import DagPickerSummary from "./DagPickerSummary";
import RemotePickerSummary from "./RemotePickerSummary";

const SUMMARIES = { dag: DagPickerSummary, remote: RemotePickerSummary };

export default function TypedEvalPickerSummary({ evalType, evalData }) {
  const View = SUMMARIES[evalType];
  if (!View?.ready) return null;
  return <View evalData={evalData} />;
}

TypedEvalPickerSummary.propTypes = {
  evalType: PropTypes.string,
  evalData: PropTypes.object,
};
