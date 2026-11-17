import React from "react";
import PropTypes from "prop-types";
import { Alert, AlertTitle, Link, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";

// A refusal never sends another request. Navigation opens an existing console
// page; a remedy without a destination is instruction text, not a dead link.
export default function ManagedRefusalAlert({ refusal, status }) {
  return (
    <Alert severity="error">
      <AlertTitle>{refusal.title}</AlertTitle>
      <Typography
        typography="s1"
        component="div"
        sx={{ overflowWrap: "anywhere" }}
      >
        {status != null ? `HTTP ${status}: ` : ""}
        {refusal.body}
      </Typography>
      {refusal.candidates?.length > 0 && (
        <Typography
          component="ul"
          typography="s1"
          sx={{ my: 1, pl: 2.5 }}
          aria-label="Candidate model IDs"
        >
          {refusal.candidates.map((id) => (
            <Typography
              component="li"
              typography="s1"
              key={id}
              sx={{ overflowWrap: "anywhere" }}
            >
              {id}
            </Typography>
          ))}
        </Typography>
      )}
      {refusal.evidence && (
        <Typography
          typography="s2"
          component="div"
          sx={{ mt: 1, overflowWrap: "anywhere" }}
        >
          Gateway dispatch:{" "}
          {refusal.evidence.dispatched === true
            ? "yes"
            : refusal.evidence.dispatched === false
              ? "no"
              : "unknown"}
          {refusal.evidence.requestId &&
            ` · Request ID: ${refusal.evidence.requestId}`}
        </Typography>
      )}
      {refusal.action && (
        <Typography typography="s2" component="div" sx={{ mt: 1 }}>
          {refusal.action.href ? (
            <Link component={RouterLink} href={refusal.action.href}>
              {refusal.action.label}
            </Link>
          ) : (
            refusal.action.label
          )}
        </Typography>
      )}
    </Alert>
  );
}

ManagedRefusalAlert.propTypes = {
  refusal: PropTypes.shape({
    title: PropTypes.string.isRequired,
    body: PropTypes.string.isRequired,
    candidates: PropTypes.arrayOf(PropTypes.string),
    evidence: PropTypes.shape({
      dispatched: PropTypes.oneOf([true, false, "unknown"]).isRequired,
      requestId: PropTypes.string,
    }),
    action: PropTypes.shape({
      label: PropTypes.string.isRequired,
      href: PropTypes.string,
    }),
  }).isRequired,
  status: PropTypes.number,
};
