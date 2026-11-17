// W4 §9 (M6): "Correct this answer" under the answer being reviewed.
// The reviewer edits the run's output and may tick "Also set as expected".
// Saving reviews isn't available in this release: Save correction stays on
// screen turned off, described by the sentence the card shows once.
import React, { useId, useState } from "react";
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

const asText = (v) =>
  typeof v === "string" ? v : v == null ? "" : JSON.stringify(v, null, 2);

export default function CorrectAnswerCard({
  exampleId,
  run,
  output,
  canCorrect = true,
}) {
  const unavailableId = useId();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(asText(output));
  const [asExpected, setAsExpected] = useState(false);
  if (!run || !exampleId || !canCorrect) return null;
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
      <Typography id={unavailableId} typography="s2" color="text.secondary">
        Saving reviews isn&apos;t available in this release.
      </Typography>
      <Stack direction="row" spacing={1}>
        <Button
          size="small"
          variant="contained"
          disabled
          aria-describedby={unavailableId}
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
  exampleId: PropTypes.string,
  run: PropTypes.object,
  output: PropTypes.any,
  canCorrect: PropTypes.bool,
};
