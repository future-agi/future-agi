import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Alert, Button, Stack, TextField, Typography } from "@mui/material";

const CHOICES = [
  { value: "A", label: "A is better" },
  { value: "B", label: "B is better" },
  { value: "tie", label: "Tie" },
];

export default function PairwiseVotePanel({
  annotation,
  instructions,
  onSubmit,
  onDirtyChange,
  isPending,
  readOnly,
  readOnlyReason,
}) {
  const [comment, setComment] = useState(annotation?.comment || "");

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  return (
    <Stack spacing={2} sx={{ p: 2, overflow: "auto" }}>
      <Typography variant="subtitle1">Compare A and B</Typography>
      {instructions && <Typography variant="body2">{instructions}</Typography>}
      {annotation?.choice && (
        <Typography variant="body2" color="text.secondary">
          Your saved choice: {annotation.choice === "tie" ? "Tie" : annotation.choice}
        </Typography>
      )}
      {readOnlyReason && <Alert severity="info">{readOnlyReason}</Alert>}
      <TextField
        label="Comment"
        multiline
        minRows={3}
        value={comment}
        disabled={readOnly || isPending}
        onChange={(event) => {
          setComment(event.target.value);
          onDirtyChange?.(event.target.value !== (annotation?.comment || ""));
        }}
        helperText="Optional. Add your reasoning before choosing a result."
      />
      <Stack spacing={1}>
        {CHOICES.map(({ value, label }) => (
          <Button
            key={value}
            variant="outlined"
            disabled={readOnly || isPending}
            onClick={() => onSubmit({ choice: value, comment })}
          >
            {label}
          </Button>
        ))}
      </Stack>
      <Typography variant="caption" color="text.secondary">
        Choosing a result saves your vote and opens the next item.
      </Typography>
    </Stack>
  );
}

PairwiseVotePanel.propTypes = {
  annotation: PropTypes.object,
  instructions: PropTypes.string,
  onSubmit: PropTypes.func.isRequired,
  onDirtyChange: PropTypes.func,
  isPending: PropTypes.bool,
  readOnly: PropTypes.bool,
  readOnlyReason: PropTypes.string,
};
