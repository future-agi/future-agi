import PropTypes from "prop-types";
import { Alert, Box, Typography } from "@mui/material";
import { useTestOutcome } from "src/api/webhooks";
import { OUTCOME_TEXT, fmtMs } from "./copy";
import { visibleControls } from "./text";

// The answer to a test: the status code (or what went wrong), the time it took,
// and the receiver's excerpt as inert text.
function TestAnswer({ result }) {
  const ok = result.outcome === "success";
  const answer = result.status_code
    ? `Your endpoint answered ${result.status_code}`
    : `The test ${OUTCOME_TEXT[result.outcome] || result.outcome}`;
  return (
    <Alert severity={ok ? "success" : "error"} sx={{ mt: 1 }} role="status">
      <Typography variant="body2">
        {answer} in {fmtMs(result.latency_ms)}.
      </Typography>
      {result.response_excerpt && (
        <Box
          component="pre"
          sx={{
            m: 0,
            mt: 0.5,
            fontFamily: "monospace",
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {visibleControls(result.response_excerpt)}
        </Box>
      )}
    </Alert>
  );
}

TestAnswer.propTypes = { result: PropTypes.object.isRequired };

// A 202: the worker is still sending. The delivery is followed until it settles
// (DESIGN.md section 8.2), so the answer shows here, where the test was sent.
function QueuedTest({ queued }) {
  const { data: outcome } = useTestOutcome(queued);
  if (outcome) return <TestAnswer result={outcome} />;
  return (
    <Alert severity="info" sx={{ mt: 1 }} role="status">
      The test is on its way. Its answer will show here when your endpoint
      replies, and in the delivery log.
    </Alert>
  );
}

QueuedTest.propTypes = { queued: PropTypes.object.isRequired };

// The result of POST /webhooks/endpoints/{id}/test/ (DESIGN.md section 8.2):
// 200 with the answer, or 202 with the ids when the worker is still sending.
export default function TestResult({ result, status }) {
  if (!result) return null;
  if (status === 202 || result.status_code === undefined) {
    return <QueuedTest queued={result} />;
  }
  return <TestAnswer result={result} />;
}

TestResult.propTypes = { result: PropTypes.object, status: PropTypes.number };
