import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

export function escalationReasonText(issue) {
  if (issue.status_origin === "manual") return "Set by a user";
  const evidence = issue.escalation;
  if (issue.status_origin !== "system" || !evidence) return "";
  const units =
    evidence.unit === "failed_evaluations" ? "failed evaluations" : "findings";
  const number = (value) =>
    Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
  const baseline =
    evidence.baseline === 0
      ? "no occurrences in the baseline"
      : `baseline ${number(evidence.baseline)}/hour`;
  const summary = `${number(evidence.current_count)} ${units} across ${number(evidence.affected_trace_count)} traces in the evaluated hour; ${baseline}; escalation threshold ${number(evidence.entry_threshold)}.`;
  return issue.status === "escalating"
    ? `${summary} Growth confirmed for ${evidence.entry_confirmation_minutes} minutes.`
    : summary;
}

export default function EscalationReason({ issue }) {
  const text = escalationReasonText(issue);
  if (!text) return null;
  const evaluatedAt =
    issue.status_origin === "system" && issue.escalation?.evaluated_at;
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ overflowWrap: "anywhere" }}
    >
      {text}
      {evaluatedAt && (
        <>
          {" "}
          Last evaluated{" "}
          <time dateTime={evaluatedAt}>
            {new Date(evaluatedAt).toLocaleString()}
          </time>
          .
        </>
      )}
    </Typography>
  );
}

EscalationReason.propTypes = { issue: PropTypes.object.isRequired };
