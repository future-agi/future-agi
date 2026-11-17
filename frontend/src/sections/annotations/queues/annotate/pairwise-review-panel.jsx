import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Alert, Button, Stack, TextField, Typography } from "@mui/material";

const CHOICE_LABELS = { A: "A is better", B: "B is better", tie: "Tie" };

export default function PairwiseReviewPanel({
  annotations, ownAnnotation, reviewNotes, showReviewActions, isPending,
  onApprove, onReject, onDirtyChange,
}) {
  const [notes, setNotes] = useState("");
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  return (
    <Stack spacing={2} sx={{ p: 2, overflow: "auto", height: "100%" }}>
      <Typography variant="subtitle1">Pairwise submissions</Typography>
      {ownAnnotation && <Typography variant="body2">Your saved choice: {ownAnnotation.choice === "tie" ? "Tie" : ownAnnotation.choice}</Typography>}
      {ownAnnotation?.comment && <Typography variant="body2">{ownAnnotation.comment}</Typography>}
      {!annotations.length && <Alert severity="info">No pairwise votes in this view.</Alert>}
      {annotations.map((vote) => (
        <Stack key={vote.id || vote.annotator} spacing={0.5}>
          <Typography variant="subtitle2">{vote.annotator_name || "Queue member"}</Typography>
          <Typography variant="body2">{CHOICE_LABELS[vote.choice] || vote.choice}</Typography>
          {vote.comment && <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{vote.comment}</Typography>}
        </Stack>
      ))}
      {reviewNotes && <Alert severity="info">{reviewNotes}</Alert>}
      {showReviewActions && (
        <>
          <TextField
            label="Review feedback" multiline minRows={3} value={notes} disabled={isPending}
            helperText="Required when requesting changes."
            onChange={(event) => {
              setNotes(event.target.value);
              onDirtyChange?.(Boolean(event.target.value));
            }}
          />
          <Stack direction="row" spacing={1}>
            <Button variant="contained" disabled={isPending} onClick={() => onApprove({ notes: notes.trim() })}>Approve</Button>
            <Button variant="outlined" disabled={isPending || !notes.trim()} onClick={() => onReject({ notes: notes.trim() })}>Request changes</Button>
          </Stack>
        </>
      )}
    </Stack>
  );
}

PairwiseReviewPanel.propTypes = {
  annotations: PropTypes.array.isRequired,
  ownAnnotation: PropTypes.object,
  reviewNotes: PropTypes.string,
  showReviewActions: PropTypes.bool,
  isPending: PropTypes.bool,
  onApprove: PropTypes.func.isRequired,
  onReject: PropTypes.func.isRequired,
  onDirtyChange: PropTypes.func,
};
