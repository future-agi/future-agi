import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { GridTable, Section } from "./trialChangesParts";
import {
  MONO,
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
  decisionSentence,
} from "./trialChanges.constants";

/* What the agent did about a decision: a change committed after a refusal
   gets its own words, never read as the agent having waited. */
const agentLine = (one) =>
  one.committed_after_refusal
    ? { text: C.wroteAfterRefusal, color: "error.main" }
    : one.acted_before_decision
      ? { text: C.actedBefore, color: "warning.dark" }
      : { text: C.waitedFor, color: "text.secondary" };

export default function TrialDecisions({ approvals, decisions }) {
  return (
    <>
      {approvals.length > 0 && (
        <Section title={C.approvalsHeading} count={approvals.length}>
          <GridTable
            cols="minmax(0, 1.1fr) minmax(0, 1fr) minmax(0, 1.2fr)"
            head={[
              C.approvalsHead.request,
              C.approvalsHead.decision,
              C.approvalsHead.agent,
            ]}
            rows={approvals.map((one, index) => {
              const agent = agentLine(one);
              return {
                key: `approval-${index}`,
                label: [
                  C.approval(one),
                  C.verdicts[one.verdict],
                  agent.text,
                  one.after_decision.length > 0 &&
                    C.afterDecision(one.after_decision),
                ]
                  .filter(Boolean)
                  .join(" · "),
                cells: [
                  <Typography
                    key="q"
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      wordBreak: "break-word",
                    }}
                  >
                    {one.request.action || "A request"}
                  </Typography>,
                  <Stack key="d" spacing={0.125}>
                    <Typography sx={{ typography: "s3", fontWeight: 500 }}>
                      {one.decision.name || "—"}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color:
                          one.verdict === "refused"
                            ? "error.main"
                            : one.verdict === "approved"
                              ? "success.dark"
                              : "text.secondary",
                      }}
                    >
                      {[
                        C.verdicts[one.verdict],
                        one.decision.action || C.noDecision,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </Stack>,
                  <Typography key="a" sx={{ ...quiet, color: agent.color }}>
                    {agent.text.charAt(0).toUpperCase() + agent.text.slice(1)}
                  </Typography>,
                ],
                below: one.after_decision.length > 0 && (
                  <Typography sx={quiet}>
                    {C.afterDecision(one.after_decision)}
                  </Typography>
                ),
              };
            })}
          />
        </Section>
      )}
      {decisions.length > 0 && (
        <Section title={C.decisionsHeading} count={decisions.length}>
          <GridTable
            cols="minmax(0, 1fr)"
            rows={decisions.map((one, index) => ({
              key: `${one.actor}-${index}`,
              // The server's sentence stays as the row's label.
              label: one.line,
              cells: [
                <Box key="l">
                  <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                    {one.name}
                  </Typography>
                  <Typography sx={{ ...quiet, color: "text.primary" }}>
                    {decisionSentence(one)}
                  </Typography>
                  {one.committed_after_refusal && (
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "error.main",
                        mt: 0.25,
                      }}
                      data-testid="committed-after-refusal"
                    >
                      {C.committedAfterRefusal(one.name)}
                    </Typography>
                  )}
                </Box>,
              ],
            }))}
          />
        </Section>
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
      play: PropTypes.string,
      action: PropTypes.string,
      outcome: PropTypes.string,
      verdict: PropTypes.string,
      why: PropTypes.string,
      committed_after_refusal: PropTypes.bool,
    }),
  ).isRequired,
};
