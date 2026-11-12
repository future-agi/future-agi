// W4 §9 (M6): "Correct this answer" under the answer being reviewed.
// The reviewer edits the run's output; "Also set as expected" also suggests
// it to the dataset (Corrections, where an editor saves it as a version).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useSubmitResultReview } from "src/api/develop/review";
import { notify } from "src/sections/common/ux/toast";
import {
  changedFrom,
  correctionBody,
  correctionToast,
} from "./correctAnswerModel";

const asText = (v) =>
  typeof v === "string" ? v : v == null ? "" : JSON.stringify(v, null, 2);

export default function CorrectAnswerCard({
  datasetId,
  exampleId,
  run,
  baseRunId,
  pairwise = false,
  output,
  canCorrect = true,
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(asText(output));
  const [asExpected, setAsExpected] = useState(false);
  const submit = useSubmitResultReview(datasetId, {
    onSuccess: (result) => {
      notify(correctionToast(run?.label, result), {
        variant: "success",
        key: "correct-answer",
      });
      setOpen(false);
    },
  });
  if (!run || !exampleId || !canCorrect) return null;
  const body = correctionBody({
    exampleId,
    runId: run.run_id,
    baseRunId,
    pairwise,
    text,
    setAsExpected: asExpected,
  });
  if (!open)
    return (
      <Box sx={{ mt: 1 }}>
        <Button
          size="small"
          onClick={() => {
            setText(asText(output));
            setOpen(true);
          }}
          data-testid="correct-answer-open"
        >
          Correct this answer
        </Button>
      </Box>
    );
  return (
    <Stack spacing={1} sx={{ mt: 1 }} data-testid="correct-answer">
      <Typography typography="s2" fontWeight={600}>
        {`Correct ${run.label ?? "this run"}'s answer`}
      </Typography>
      <TextField
        multiline
        minRows={3}
        size="small"
        value={text}
        onChange={(e) => setText(e.target.value)}
        inputProps={{ "aria-label": "Corrected answer" }}
      />
      <FormControlLabel
        control={
          <Checkbox
            size="small"
            checked={asExpected}
            onChange={(e) => setAsExpected(e.target.checked)}
          />
        }
        label="Also set as expected"
      />
      <Stack direction="row" spacing={1}>
        <Button
          size="small"
          variant="contained"
          disabled={
            !body || !changedFrom(asText(output), text) || submit.isPending
          }
          onClick={() => body && submit.mutate(body)}
        >
          Save correction
        </Button>
        <Button size="small" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </Stack>
    </Stack>
  );
}

CorrectAnswerCard.propTypes = {
  datasetId: PropTypes.string.isRequired,
  exampleId: PropTypes.string,
  run: PropTypes.object,
  baseRunId: PropTypes.string,
  pairwise: PropTypes.bool,
  output: PropTypes.any,
  canCorrect: PropTypes.bool,
};
