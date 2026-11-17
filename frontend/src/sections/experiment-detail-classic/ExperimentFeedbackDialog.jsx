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
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/** Save row feedback without implicitly retuning a shared evaluator or rerunning. */
export default function ExperimentFeedbackDialog({
  experimentId,
  metricId,
  columnId,
  rowId,
  onClose,
}) {
  const [value, setValue] = useState("");
  const [explanation, setExplanation] = useState("");
  const [savedId, setSavedId] = useState(null);
  const routes = endpoints.develop.experiment.feedback;
  const complete = Boolean(experimentId && metricId && columnId && rowId);
  const previous = useQuery({
    queryKey: ["classic-experiment-feedback", experimentId, metricId, rowId],
    enabled: complete,
    queryFn: async () =>
      (
        await axios.get(routes.getDetails(experimentId), {
          params: { user_eval_metric_id: metricId, row_id: rowId },
        })
      ).data.result.feedback,
    retry: false,
  });
  const save = useMutation({
    mutationFn: async () => {
      const response = await axios.post(routes.create(experimentId), {
        source: "experiment",
        source_id: columnId,
        row_id: rowId,
        user_eval_metric: metricId,
        value: value.trim(),
        explanation: explanation.trim(),
      });
      if (!response.data?.result?.id)
        throw new Error("Feedback was not confirmed. Please try again.");
      return response.data.result.id;
    },
    onSuccess: (id) => {
      setSavedId(id);
      previous.refetch();
    },
    retry: false,
  });
  const close = () => {
    if (!save.isPending) onClose();
  };
  const update = (setter) => (event) => {
    setter(event.target.value);
    setSavedId(null);
    save.reset();
  };
  return (
    <Dialog
      open
      onClose={close}
      aria-labelledby="experiment-feedback-title"
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle id="experiment-feedback-title">
        Experiment feedback
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography color="text.secondary">
            Store feedback for this result. This does not retune the evaluator
            or rerun the experiment.
          </Typography>
          {!complete && (
            <Alert severity="error">
              The result identity is unavailable. Reopen the row to try again.
            </Alert>
          )}
          {previous.isError && (
            <Alert severity="error">Could not load saved feedback.</Alert>
          )}
          {save.isError && (
            <Alert severity="error">
              {
                /** @type {Error & { response?: { data?: { detail?: string } } }} */ (
                  save.error
                )?.response?.data?.detail ||
                  save.error?.message ||
                  "Could not save feedback."
              }
            </Alert>
          )}
          {savedId && <Alert severity="success">Feedback saved</Alert>}
          <TextField
            label="Feedback value"
            value={value}
            onChange={update(setValue)}
            disabled={save.isPending}
            required
          />
          <TextField
            label="Explanation"
            value={explanation}
            onChange={update(setExplanation)}
            disabled={save.isPending}
            multiline
            minRows={3}
            required
          />
          <Typography variant="subtitle2">
            Saved feedback for this evaluator and row, across variants
          </Typography>
          {(previous.data || []).map((item) => (
            <Stack key={item.id} spacing={0.5}>
              <Typography variant="subtitle2">
                Saved feedback: {item.value}
              </Typography>
              <Typography sx={{ whiteSpace: "pre-wrap" }}>
                {item.comment}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={close} disabled={save.isPending}>
          Close
        </Button>
        <Button
          variant="contained"
          onClick={() => save.mutate()}
          disabled={
            !complete ||
            !value.trim() ||
            !explanation.trim() ||
            save.isPending ||
            Boolean(savedId)
          }
        >
          {save.isPending ? "Saving feedback..." : "Save feedback"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
ExperimentFeedbackDialog.propTypes = {
  experimentId: PropTypes.string,
  metricId: PropTypes.string,
  columnId: PropTypes.string,
  rowId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};
