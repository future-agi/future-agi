import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { isSurfaceShown } from "src/launch/launchSwitch";
import ScriptBlock from "src/sections/common/RunSources/ScriptBlock";
import { ciCommentStep } from "src/sections/common/RunSources/snippets/ciComment";

function CICommentOptions({ experimentId, runs, baselineId, datasetBaseline }) {
  const baselines = runs.filter((run) => !run.not_found);
  if (
    datasetBaseline?.run_id &&
    !baselines.some((run) => run.run_id === datasetBaseline.run_id)
  )
    baselines.push(datasetBaseline);
  const initialBaseline = baselineId ?? datasetBaseline?.run_id ?? "";
  const [baseline, setBaseline] = useState(
    baselines.some((run) => run.run_id === initialBaseline)
      ? initialBaseline
      : "",
  );
  const [threshold, setThreshold] = useState("0");
  const [topN, setTopN] = useState("5");
  let snippet = "";
  let message = "";
  try {
    snippet = ciCommentStep({
      experimentId,
      baselineRunId: baselines.some((run) => run.run_id === baseline)
        ? baseline
        : "",
      maxRegressed: threshold.trim() ? Number(threshold) : NaN,
      topN: topN.trim() ? Number(topN) : NaN,
    });
  } catch (error) {
    message = error.message;
  }
  return (
    <Stack gap={2}>
      <Typography variant="body2">
        Run and upload evaluations using your existing Your code CI workflow,
        then add this step in the same job. The upload step has id fi_evaluation
        and publishes run_id only after success. The candidate comes from that
        step on every execution; the selected baseline stays fixed. Both runs
        must be completed and use the same dataset version, trial plan and
        evaluators.
      </Typography>
      <Typography variant="body2">
        For an older or custom upload step, use id: fi_evaluation and write{" "}
        <code>
          {'printf \'run_id=%s\\n\' "$FI_RUN_ID" >> "$GITHUB_OUTPUT"'}
        </code>{" "}
        only after successful evaluation/upload, where FI_RUN_ID is the actual
        run uploaded by this job. Keep the upload failure fatal; do not reuse an
        earlier run ID as a fallback. No comment is posted if an earlier step
        fails or this output is missing.
      </Typography>
      <TextField
        select
        fullWidth
        label="Baseline run"
        value={baseline}
        onChange={(event) => setBaseline(event.target.value)}
        SelectProps={{ native: true }}
        InputLabelProps={{ shrink: true }}
      >
        <option value="">Choose a baseline</option>
        {baselines.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            {run.label ?? run.run_id}
          </option>
        ))}
      </TextField>
      <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
        <TextField
          fullWidth
          type="number"
          label="Allowed regressions"
          value={threshold}
          onChange={(event) => setThreshold(event.target.value)}
          inputProps={{ min: 0, step: 1 }}
          helperText="Row/evaluator comparisons, not unique rows."
        />
        <TextField
          fullWidth
          type="number"
          label="Regressed rows to include"
          value={topN}
          onChange={(event) => setTopN(event.target.value)}
          inputProps={{ min: 1, max: 20, step: 1 }}
        />
      </Stack>
      <Typography variant="body2">
        Set repository secrets FI_API_KEY and FI_SECRET_KEY for the same
        workspace, plus variables FI_API_URL (API base URL) and FI_APP_URL (app
        origin). Grant the job contents: read and pull-requests: write. This
        uses curl, jq and gh on ubuntu-latest. Fork pull requests are skipped;
        do not switch to pull_request_target. The comment is posted before a
        failing verdict fails the step. It includes row and evaluator IDs and
        scores, never raw row contents.
      </Typography>
      {snippet ? (
        <ScriptBlock
          text={snippet}
          language="yaml"
          full
          copyTooltip="Copy GitHub Actions step"
          testId="ci-comment-snippet"
        />
      ) : (
        <Alert severity="info">{message}</Alert>
      )}
    </Stack>
  );
}

const optionProps = {
  experimentId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  baselineId: PropTypes.string,
  datasetBaseline: PropTypes.object,
};
CICommentOptions.propTypes = optionProps;

function RunInCIDoor(props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Stack spacing={0.5} alignItems="flex-start">
        <Button variant="outlined" onClick={() => setOpen(true)}>
          Run in CI
        </Button>
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="exp.results.ciComment-scope-line"
        >
          Copy a GitHub Actions step to comment on run regressions after your
          existing evaluation upload.
        </Typography>
      </Stack>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        aria-labelledby="run-in-ci-title"
        maxWidth="md"
        fullWidth
      >
        <DialogTitle id="run-in-ci-title">Run in CI</DialogTitle>
        <DialogContent>{open && <CICommentOptions {...props} />}</DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default function RunInCIPanel(props) {
  if (!isSurfaceShown("exp.results.ciComment")) return null;
  return <RunInCIDoor {...props} />;
}
RunInCIPanel.propTypes = optionProps;
