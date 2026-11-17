import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  useAddQueuePairs,
  extractErrorMessage,
} from "src/api/annotation-queues/annotation-queues";
import { SOURCE_OPTIONS } from "../constants";

const PAIR_SOURCE_OPTIONS = [
  ...SOURCE_OPTIONS,
  { value: "prototype_run", label: "Prototype Run" },
];
const PROJECT_SOURCE_TYPES = new Set([
  "trace", "observation_span", "trace_session",
]);

export default function AddPairDialog({ queueId, queue, onClose }) {
  const fixedSourceType = queue?.source_type ||
    (queue?.dataset ? "dataset_row" : queue?.agent_definition ? "call_execution" : null);
  const sourceOptions = queue?.project
    ? PAIR_SOURCE_OPTIONS.filter(({ value }) => PROJECT_SOURCE_TYPES.has(value))
    : PAIR_SOURCE_OPTIONS;
  const [sourceType, setSourceType] = useState(
    fixedSourceType || (queue?.project ? "trace" : "dataset_row"),
  );
  const [sourceA, setSourceA] = useState("");
  const [sourceB, setSourceB] = useState("");
  const [projectId, setProjectId] = useState(queue?.project || "");
  const [error, setError] = useState("");
  const { mutate: addPairs, isPending } = useAddQueuePairs();
  const needsProject = PROJECT_SOURCE_TYPES.has(sourceType);
  const sameSource = Boolean(sourceA.trim()) && sourceA.trim() === sourceB.trim();
  const valid = sourceA.trim() && sourceB.trim() && !sameSource &&
    (!needsProject || projectId.trim());

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!valid || isPending) return;
    setError("");
    const source = (id) => ({
      source_type: sourceType,
      source_id: id.trim(),
      ...(needsProject ? { project_id: projectId.trim() } : {}),
    });
    addPairs(
      { queueId, pairs: [{ source_a: source(sourceA), source_b: source(sourceB) }] },
      {
        onSuccess: onClose,
        onError: (failure) => {
          const message = extractErrorMessage(
            failure,
            "Could not add this pair. Check the source IDs and try again.",
          );
          setError(typeof message === "string" ? message : JSON.stringify(message));
        },
      },
    );
  };

  return (
    <Dialog
      open
      onClose={isPending ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="add-pair-title"
    >
      <form onSubmit={handleSubmit}>
        <DialogTitle id="add-pair-title">Add pair</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2">
              Choose two different sources to compare. A and B keep the order
              entered here.
            </Typography>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              select
              label="Source type"
              value={sourceType}
              disabled={isPending || Boolean(fixedSourceType)}
              onChange={(event) => setSourceType(event.target.value)}
            >
              {sourceOptions.map(({ value, label }) => (
                <MenuItem key={value} value={value}>{label}</MenuItem>
              ))}
            </TextField>
            {needsProject && (
              <TextField
                label="Project ID"
                value={projectId}
                required
                disabled={isPending || Boolean(queue?.project)}
                onChange={(event) => setProjectId(event.target.value)}
                helperText="Both sources must belong to this project."
              />
            )}
            <TextField
              label="Source A ID"
              value={sourceA}
              required
              disabled={isPending}
              inputProps={{ maxLength: 255 }}
              onChange={(event) => setSourceA(event.target.value)}
            />
            <TextField
              label="Source B ID"
              value={sourceB}
              required
              disabled={isPending}
              inputProps={{ maxLength: 255 }}
              onChange={(event) => setSourceB(event.target.value)}
              error={sameSource}
              helperText={sameSource
                ? "Choose a different source for B."
                : "Sources must be accessible in this workspace."}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={isPending}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!valid || isPending}>
            {isPending ? "Adding pair…" : "Add pair"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

AddPairDialog.propTypes = {
  queueId: PropTypes.string.isRequired,
  queue: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
