import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import ScopeFilterBar from "src/sections/projects/ScopeFilterBar";
import { insightsKeys, patchConfig } from "./insightsApi";
import { formatCount } from "./insightsFormat";
import { useCancellable } from "./insightsHooks";
import { LENSES, attributeError, unitTitle } from "./insightsLenses";
import {
  SAMPLE_SIZES,
  cleanAttributes,
  editableScope,
  isFixedScope,
  lastScope,
  scopeDateLabel,
  storedScope,
} from "./insightsScope";
import { AttributesEditor } from "./NewReportTune";

const fromConfig = (config) => {
  const saved = lastScope(config);
  return {
    name: config.name || "",
    question: config.question || "",
    lens: config.lens || "usage",
    agentDescription: config.agent_description || "",
    instructions: config.instructions || "",
    attributes: (config.attributes || []).map((a) => ({ ...a })),
    sampleSize: config.sample_size || 300,
    // Rows handed over stay as they are; a date and filters can change.
    scope: isFixedScope(saved) ? null : editableScope(saved, config.unit),
    scopeChanged: false,
  };
};

const toBody = (draft) => ({
  name: draft.name.trim().slice(0, 255),
  question: draft.question.trim().slice(0, 500),
  lens: draft.lens,
  agent_description: draft.agentDescription.trim().slice(0, 500),
  instructions: draft.instructions.trim().slice(0, 1000),
  attributes: cleanAttributes(draft.attributes),
  sample_size: draft.sampleSize,
  ...(draft.scope && draft.scopeChanged
    ? { scope: storedScope(draft.scope) }
    : {}),
});

// Fields the server kept as sent. An older server saves only the name.
function unsaved(sent, saved) {
  if (!saved) return [];
  const same = (a, b) => JSON.stringify(a ?? "") === JSON.stringify(b ?? "");
  const keys = (list) =>
    (list || []).map((a) => `${a.key}:${a.type}`).join("|");
  return [
    !same(sent.question, saved.question) && "question",
    !same(sent.lens, saved.lens) && "lens",
    !same(sent.agent_description, saved.agent_description) &&
      "agent description",
    !same(sent.instructions, saved.instructions) && "instructions",
    keys(sent.attributes) !== keys(saved.attributes) && "attributes",
    saved.sample_size !== undefined &&
      sent.sample_size !== saved.sample_size &&
      "sample size",
  ].filter(Boolean);
}

/** The report's settings in a side drawer: edit them for the next run. */
export default function EditDrawer({
  open,
  projectId,
  config,
  scopeLabel,
  onClose,
  onRunNow,
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => fromConfig(config));
  const [result, setResult] = useState(null); // {missed: [...]}
  const saving = useCancellable("The report could not be saved.");
  const update = (patch) => {
    setResult(null);
    setDraft((current) => ({ ...current, ...patch }));
  };
  // The server needs a name (1-255 characters); a blank one used to surface
  // as a retry message that could never succeed.
  const problem = !draft.name.trim()
    ? "Add a name"
    : !draft.question.trim()
      ? "Add a question"
      : draft.attributes.some((a) => attributeError(a, draft.attributes))
        ? "Fix the attributes"
        : "";

  const save = () =>
    saving.run(async () => {
      const body = toBody(draft);
      const saved = await patchConfig(config.id, body);
      if (saved && typeof saved === "object")
        queryClient.setQueryData(insightsKeys.config(config.id), (current) =>
          current ? { ...current, ...saved, runs: current.runs } : saved,
        );
      queryClient.invalidateQueries({
        queryKey: insightsKeys.config(config.id),
      });
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      setResult({
        missed: unsaved(body, saved),
        // The server forgets the categories when the question or lens moves.
        rediscover:
          Boolean(config.has_taxonomy) && saved?.has_taxonomy === false,
      });
      return saved;
    });

  const busy = saving.state.status === "busy";
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: 600, maxWidth: "100vw" } }}
    >
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            px: 2.5,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle1" component="h2">
              Edit report
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Changes apply from the next run
            </Typography>
          </Box>
          <IconButton aria-label="Close" onClick={onClose} size="small">
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
          <Stack spacing={2.25}>
            <TextField
              size="small"
              label="Name"
              value={draft.name}
              onChange={(event) =>
                update({ name: event.target.value.slice(0, 255) })
              }
              disabled={busy}
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              size="small"
              label="Question"
              value={draft.question}
              onChange={(event) =>
                update({ question: event.target.value.slice(0, 500) })
              }
              disabled={busy}
              required
              multiline
              minRows={2}
              InputLabelProps={{ shrink: true }}
              helperText="The report's heading and what its summary answers"
            />
            <Stack direction="row" spacing={1.5}>
              <TextField
                select
                size="small"
                label="Lens"
                value={draft.lens}
                onChange={(event) => update({ lens: event.target.value })}
                disabled={busy}
                sx={{ flex: 1 }}
              >
                {LENSES.map((lens) => (
                  <MenuItem key={lens.key} value={lens.key}>
                    {lens.title}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                select
                size="small"
                label="Sample size"
                value={draft.sampleSize}
                onChange={(event) =>
                  update({ sampleSize: Number(event.target.value) })
                }
                disabled={busy}
                sx={{ width: 140 }}
              >
                {SAMPLE_SIZES.map((size) => (
                  <MenuItem key={size} value={size}>
                    {formatCount(size)}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
            {(draft.question.trim() !== (config.question || "").trim() ||
              draft.lens !== config.lens) &&
              config.has_taxonomy && (
                <Alert severity="info" sx={{ py: 0 }}>
                  A new question or lens changes what the categories mean, so
                  the next run finds its categories afresh.
                </Alert>
              )}
            <Stack direction="row" spacing={1.5}>
              <TextField
                size="small"
                label="Reads"
                value={unitTitle(config.unit)}
                disabled
                sx={{ flex: 1 }}
                helperText="Fixed for a report"
              />
              {!draft.scope && (
                <TextField
                  size="small"
                  label="Scope"
                  value={scopeLabel || "All"}
                  disabled
                  sx={{ flex: 1 }}
                  helperText="The rows handed over"
                />
              )}
            </Stack>
            {draft.scope && (
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                  Which data
                </Typography>
                <ScopeFilterBar
                  projectId={projectId}
                  unit={config.unit}
                  scope={draft.scope}
                  onChange={(scope) => update({ scope, scopeChanged: true })}
                  disabled={busy}
                  dateLabel={scopeDateLabel(draft.scope)}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  component="p"
                  sx={{ mt: 0.75 }}
                >
                  The next run reads these; a preset window moves to the time it
                  runs.
                </Typography>
              </Box>
            )}
            <TextField
              size="small"
              label="What the agent does"
              value={draft.agentDescription}
              onChange={(event) =>
                update({ agentDescription: event.target.value.slice(0, 500) })
              }
              disabled={busy}
              multiline
              minRows={2}
              InputLabelProps={{ shrink: true }}
              helperText="Helps the report tell the agent's mistakes from the user's."
            />
            <TextField
              size="small"
              label="Extra instructions"
              value={draft.instructions}
              onChange={(event) =>
                update({ instructions: event.target.value.slice(0, 1000) })
              }
              disabled={busy}
              multiline
              minRows={2}
              InputLabelProps={{ shrink: true }}
            />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.25 }}>
                Attributes
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mb: 1.25 }}
              >
                Read from every item and summarised per category. A yes/no
                attribute can also decide which items are included.
              </Typography>
              <AttributesEditor
                attributes={draft.attributes}
                onChange={(attributes) => update({ attributes })}
                disabled={busy}
                compact
              />
            </Box>
            {saving.state.status === "error" && (
              <Alert severity="error">{saving.state.error}</Alert>
            )}
            {result &&
              (result.missed.length ? (
                <Alert severity="warning">
                  The name was saved, but this server does not save the{" "}
                  {result.missed.join(", ")} of a report yet.
                </Alert>
              ) : (
                <Alert
                  severity="success"
                  action={
                    <Button color="inherit" size="small" onClick={onRunNow}>
                      Run now
                    </Button>
                  }
                >
                  {result.rediscover
                    ? "Saved. The question or lens changed, so the next run finds its categories afresh."
                    : "Saved. The next run uses these settings."}
                </Alert>
              ))}
          </Stack>
        </Box>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{
            pl: 2.5,
            // Clear of the app's floating assistant buttons.
            pr: 10,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
            {problem}
          </Typography>
          <Button color="inherit" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="contained"
            onClick={save}
            disabled={busy || Boolean(problem)}
          >
            Save
          </Button>
        </Stack>
      </Stack>
    </Drawer>
  );
}

EditDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  projectId: PropTypes.string,
  config: PropTypes.object.isRequired,
  scopeLabel: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onRunNow: PropTypes.func.isRequired,
};
