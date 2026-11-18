import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { TRIAL_CHANGES_COPY as C } from "./trialChanges.constants";

const quiet = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};
const HEADING = { typography: "s2", fontWeight: 600 };

// A change committed after a refusal gets its own line, never read as the agent having waited.
export default function TrialDecisions({ approvals, decisions }) {
  return (
    <>
      {approvals.length > 0 && (
        <Box>
          <Typography sx={HEADING}>{C.approvalsHeading}</Typography>
          {approvals.map((one, index) => (
            <Typography
              key={`approval-${index}`}
              sx={
                one.committed_after_refusal
                  ? { ...quiet, color: "error.main" }
                  : quiet
              }
            >
              {[
                C.approval(one),
                C.verdicts[one.verdict],
                one.committed_after_refusal
                  ? C.wroteAfterRefusal
                  : one.acted_before_decision
                    ? C.actedBefore
                    : C.waitedFor,
                one.after_decision.length > 0 &&
                  C.afterDecision(one.after_decision),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          ))}
        </Box>
      )}
      {decisions.length > 0 && (
        <Box>
          <Typography sx={HEADING}>{C.decisionsHeading}</Typography>
          {decisions.map((one, index) => (
            <Box key={`${one.actor}-${index}`}>
              {/* The row's sentence as the server words it. */}
              <Typography sx={quiet}>{one.line}</Typography>
              {one.committed_after_refusal && (
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, color: "error.main" }}
                  data-testid="committed-after-refusal"
                >
                  {C.committedAfterRefusal(one.name)}
                </Typography>
              )}
            </Box>
          ))}
        </Box>
      )}
    </>
  );
}

TrialDecisions.propTypes = {
  approvals: PropTypes.arrayOf(
    PropTypes.shape({
      request: PropTypes.shape({ action: PropTypes.string }).isRequired,
      decision: PropTypes.shape({
        by: PropTypes.string,
        // Null when nobody decided.
        name: PropTypes.string,
        action: PropTypes.string,
      }).isRequired,
      after_decision: PropTypes.arrayOf(
        PropTypes.shape({
          action: PropTypes.string,
          outcome: PropTypes.string,
        }),
      ).isRequired,
      acted_before_decision: PropTypes.bool.isRequired,
      verdict: PropTypes.oneOf(["approved", "refused"]),
      committed_after_refusal: PropTypes.bool,
    }),
  ).isRequired,
  decisions: PropTypes.arrayOf(
    PropTypes.shape({
      actor: PropTypes.string,
      name: PropTypes.string.isRequired,
      // The whole row, worded by the server.
      line: PropTypes.string.isRequired,
      committed_after_refusal: PropTypes.bool,
    }),
  ).isRequired,
};
