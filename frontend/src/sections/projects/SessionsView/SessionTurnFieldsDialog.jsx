import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
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
import PathInput from "src/sections/common/Values/PathInput";
import {
  DEFAULT_SESSION_TURN_FIELDS,
  SESSION_TURN_COLUMNS,
  SESSION_TURN_METRICS,
  normalizeSessionTurnFields,
  sessionTurnFieldError,
} from "./sessionTurnFields";

// Mounted fresh on each open/scope so cancel discards only the dialog draft.
export default function SessionTurnFieldsDialog({
  open,
  fields,
  sample,
  onSave,
  onClose,
  saveDescription,
}) {
  const [draft, setDraft] = useState(() => normalizeSessionTurnFields(fields));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const invalid = draft.some(sessionTurnFieldError);
  const change = (index, field) =>
    setDraft((items) => items.map((item, i) => (i === index ? field : item)));
  const move = (index, direction) =>
    setDraft((items) => {
      const next = [...items];
      [next[index], next[index + direction]] = [
        next[index + direction],
        next[index],
      ];
      return next;
    });
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(draft);
      onClose();
    } catch {
      setError(
        "Turn fields were not saved. Your edits are still here. Retry save when the connection is available.",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={saving ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="turn-fields-title"
    >
      <DialogTitle id="turn-fields-title">Turn fields</DialogTitle>
      <DialogContent>
        <Stack gap={2}>
          <Typography variant="body2">
            Choose the values and metrics shown on every turn. Paths start with
            input or output, for example input.messages[-1].content.
          </Typography>
          {saveDescription && (
            <Typography variant="body2" color="text.secondary">
              {saveDescription}
            </Typography>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          <Box
            component="fieldset"
            disabled={saving}
            sx={{ border: 0, p: 0, m: 0, minWidth: 0 }}
          >
            <Stack gap={2}>
              {draft.map((field, index) => (
                <Stack
                  key={index}
                  role="group"
                  aria-label={`Field ${index + 1}`}
                  gap={1}
                  sx={{
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 1.5,
                  }}
                >
                  <Stack direction="row" gap={1} flexWrap="wrap">
                    <TextField
                      disabled={saving}
                      select
                      size="small"
                      label="Field type"
                      value={field.kind}
                      onChange={(event) =>
                        change(
                          index,
                          event.target.value === "metric"
                            ? { kind: "metric", metric: "total_latency_ms" }
                            : {
                                kind: "value",
                                label: "Output",
                                path: "output",
                              },
                        )
                      }
                      sx={{ minWidth: 130 }}
                    >
                      <MenuItem value="value">Value</MenuItem>
                      <MenuItem value="metric">Metric</MenuItem>
                    </TextField>
                    {field.kind === "metric" ? (
                      <TextField
                        disabled={saving}
                        select
                        size="small"
                        label="Metric"
                        value={field.metric}
                        onChange={(event) =>
                          change(index, {
                            ...field,
                            metric: event.target.value,
                          })
                        }
                        sx={{ flex: 1, minWidth: 150 }}
                      >
                        {Object.entries(SESSION_TURN_METRICS).map(
                          ([metric, label]) => (
                            <MenuItem key={metric} value={metric}>
                              {label}
                            </MenuItem>
                          ),
                        )}
                      </TextField>
                    ) : (
                      <TextField
                        size="small"
                        label="Label"
                        value={field.label}
                        onChange={(event) =>
                          change(index, { ...field, label: event.target.value })
                        }
                        sx={{ flex: 1, minWidth: 150 }}
                      />
                    )}
                  </Stack>
                  {field.kind === "value" && (
                    <PathInput
                      value={field.path}
                      columns={SESSION_TURN_COLUMNS}
                      sampleRow={sample}
                      onChange={(path) => change(index, { ...field, path })}
                      error={sessionTurnFieldError(field)}
                    />
                  )}
                  <Stack direction="row" gap={1} flexWrap="wrap">
                    <Button
                      size="small"
                      disabled={index === 0 || saving}
                      aria-label={`Move field ${index + 1} up`}
                      onClick={() => move(index, -1)}
                    >
                      Move up
                    </Button>
                    <Button
                      size="small"
                      disabled={index === draft.length - 1 || saving}
                      aria-label={`Move field ${index + 1} down`}
                      onClick={() => move(index, 1)}
                    >
                      Move down
                    </Button>
                    <Button
                      size="small"
                      aria-label={`Remove field ${index + 1}`}
                      onClick={() =>
                        setDraft((items) => items.filter((_, i) => i !== index))
                      }
                    >
                      Remove
                    </Button>
                  </Stack>
                </Stack>
              ))}
              {draft.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No turn fields selected. Evaluations and View Trace remain
                  available.
                </Typography>
              )}
              <Stack direction="row" gap={1}>
                <Button
                  onClick={() =>
                    setDraft((items) => [
                      ...items,
                      { kind: "value", label: "Output", path: "output" },
                    ])
                  }
                >
                  Add field
                </Button>
                <Button onClick={() => setDraft(DEFAULT_SESSION_TURN_FIELDS)}>
                  Restore defaults
                </Button>
              </Stack>
            </Stack>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={saving} onClick={onClose}>
          Cancel
        </Button>
        <Button variant="contained" disabled={saving || invalid} onClick={save}>
          {saving ? "Saving…" : error ? "Retry save" : "Apply & save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
SessionTurnFieldsDialog.propTypes = {
  open: PropTypes.bool,
  fields: PropTypes.array,
  sample: PropTypes.object,
  onSave: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  saveDescription: PropTypes.string,
};
