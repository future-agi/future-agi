/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";

import { unavailableCopy } from "./SandboxErrorState";
import StatePanel from "./StatePanel";

const UNAVAILABLE = "sandboxes_unavailable";

/**
 * Whether a list or settings read says sandboxes are not set up on this
 * deployment: `{ reason }` when it does, else null. EE answers both reads 200
 * with `caps.createRefusal.code` "sandboxes_unavailable" (and an empty list);
 * an older EE answered 503 with that code. Decided by the code alone, never by
 * the running limit or the sizes on offer.
 */
export function sandboxesUnavailable({ data, error } = {}) {
  const refusal = data?.caps?.createRefusal;
  if (refusal?.code === UNAVAILABLE) return { reason: refusal.reason ?? null };
  if (!data && error?.code === UNAVAILABLE)
    return { reason: error.message ?? null };
  return null;
}

/**
 * The page's answer when sandboxes are not set up here: a calm, normal state,
 * not an error, with nothing to retry or create.
 */
export default function SandboxesUnavailable({ reason }) {
  const copy = unavailableCopy(reason);
  return (
    <StatePanel
      testId="sandboxes-unavailable"
      icon={copy.icon}
      title={copy.title}
      description={copy.description}
    />
  );
}

SandboxesUnavailable.propTypes = {
  /** The deployment's reason; shown only when a customer may read it. */
  reason: PropTypes.string,
};
