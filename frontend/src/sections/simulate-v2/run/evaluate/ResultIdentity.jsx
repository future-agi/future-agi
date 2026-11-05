import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import {
  evidenceLabelOf,
  evidenceRunsOf,
  rangesOf,
} from "../../_mock/evalEvidence";
import { aboutCredits, readsLabelOf, whenOf } from "../../_mock/trialQuestions";

/*
  What a saved evaluation result says about itself: its id, the evaluator and
  version, the evidence it read (which run, which trials), and that the
  results it sits beside were left as they were.
*/
const Row = ({ label, children }) => (
  <Box
    sx={{
      display: "grid",
      gridTemplateColumns: "96px minmax(0, 1fr)",
      columnGap: 2,
      py: 0.75,
      borderBottom: "1px solid",
      borderColor: "divider",
    }}
  >
    <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
      {label}
    </Typography>
    <Box sx={{ typography: "s1", color: "text.primary", minWidth: 0 }}>
      {children}
    </Box>
  </Box>
);
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

export default function ResultIdentity({ result, trials, runId, runLabel }) {
  const [ids, setIds] = useState(false);
  const evaluator = result.evaluator || { name: "Instant eval", version: "v1" };
  /* Results saved before evidence was recorded read the run they sit in. */
  const runs = result.evidence?.runs?.length
    ? result.evidence.runs.map((r) => ({
        ...r,
        nos: r.trials,
        here: r.run === runId,
      }))
    : evidenceRunsOf(trials, runId).map((r) => ({
        ...r,
        label: r.label || runLabel,
      }));
  const fields = result.evidence?.fields;
  return (
    <Box sx={{ mt: 2, maxWidth: 640 }}>
      <Row label="Result">{result.id}</Row>
      <Row label="Evaluator">
        {evaluator.name} {evaluator.version}
      </Row>
      <Row label="Reads">
        {fields?.length
          ? fields.map(evidenceLabelOf).join(", ")
          : readsLabelOf(result.reads)}
      </Row>
      <Row label="Evidence">
        {runs.map((r) => (
          <Typography key={r.run} sx={{ typography: "s1" }}>
            {r.label} · trials {rangesOf(r.nos)}
          </Typography>
        ))}
        {runs.some((r) => r.ids?.length) && (
          <>
            <Button
              size="small"
              variant="outlined"
              onClick={() => setIds((v) => !v)}
              sx={{ mt: 0.75, color: "text.primary" }}
            >
              {ids ? "Hide trial ids" : "Show trial ids"}
            </Button>
            {ids &&
              runs.map((r) => (
                <Typography
                  key={r.run}
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    mt: 0.5,
                    wordBreak: "break-all",
                  }}
                >
                  {r.label}: {(r.ids || []).join(", ")}
                </Typography>
              ))}
          </>
        )}
      </Row>
      <Row label="Saved">
        By {result.by} · {whenOf(result.at)} · {aboutCredits(result.credits)}
      </Row>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        Original results are kept.
      </Typography>
    </Box>
  );
}
ResultIdentity.propTypes = {
  result: PropTypes.object.isRequired,
  trials: PropTypes.array.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
};
