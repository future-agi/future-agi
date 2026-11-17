// W7 A0: the Details tab of a typed eval (a decision tree, a remote scorer)
// and the composite jury report. EvalDetailPage mounts it for typed evals;
// the wizard's binding panel opens it in a side panel (`inPanel`). A type
// whose detail is not real yet shows nothing.
import React from "react";
import PropTypes from "prop-types";
import { TYPED_DETAILS, hasTypedDetail } from "./typedViews";

export default function TypedEvalDetail({
  evalType,
  templateId,
  evalData,
  inPanel = false,
}) {
  const Detail = TYPED_DETAILS[evalType];
  if (!hasTypedDetail(evalType)) return null;
  return (
    <Detail templateId={templateId} evalData={evalData} inPanel={inPanel} />
  );
}

TypedEvalDetail.propTypes = {
  evalType: PropTypes.string,
  templateId: PropTypes.string,
  evalData: PropTypes.object,
  inPanel: PropTypes.bool,
};
