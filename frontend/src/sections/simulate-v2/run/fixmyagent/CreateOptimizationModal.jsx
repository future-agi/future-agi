import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Dialog, TextField, Chip, Tooltip, IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { OPTIMIZERS, OPTIMIZER_MODELS, nextOptimizationName } from "../../_mock/optimizationRuns";

/**
 * Starting an optimization.
 *
 * The old modal asked for a name, an algorithm and a model, and that shape is
 * right — it is the smallest set of decisions somebody actually has to make.
 * Two things are added here, and both exist because the objective changed.
 *
 * When the score comes from a dataset, a trial is cheap and nobody counts them.
 * When it comes from the environment, a trial is a full sweep of the scenarios
 * through real tools, so the cost is stated before the button is pressed rather
 * than discovered afterwards.
 *
 * And the split is shown as a fact of the run, not buried in a settings panel.
 * The number this produces is only a prediction if some scenarios were held
 * back, so the screen that starts the search is where that has to be visible.
 */

export default function CreateOptimizationModal({ open, envState, included, split, onClose, onStart }) {
  const [name, setName] = useState("");
  const [optimizerId, setOptimizerId] = useState("protegi");
  const [model, setModel] = useState("claude-opus-5");

  useEffect(() => {
    if (open) {
      setName(nextOptimizationName(envState));
      setOptimizerId("protegi");
    }
  }, [open, envState]);

  const optimizer = OPTIMIZERS.find((o) => o.id === optimizerId) || OPTIMIZERS[0];
  const trials = optimizer.trialsFor(optimizer.config);
  const episodes = trials * (split?.trainMeasured || 0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ sx: { borderRadius: 1.5, backgroundImage: "none" } }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box flex={1}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Optimize my agent</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Searches for a better prompt, scored by this environment
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={17} />
        </IconButton>
      </Stack>

      <Stack spacing={2.5} sx={{ px: 2.5, py: 2.5, maxHeight: "62vh", overflowY: "auto" }}>
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.875 }}>Run name</Typography>
          <TextField
            size="small" fullWidth value={name} onChange={(e) => setName(e.target.value)}
            sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
          />
        </Box>

        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.875 }}>Optimizer</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>
            {OPTIMIZERS.map((o) => {
              const active = o.id === optimizerId;
              return (
                <Tooltip key={o.id} arrow title={o.fit}>
                  <Box
                    onClick={() => setOptimizerId(o.id)}
                    sx={{
                      px: 1.5, py: 1.125, borderRadius: 1, cursor: "pointer", flex: "1 1 46%", minWidth: 190,
                      border: "1px solid",
                      borderColor: active ? "#7857FC" : "divider",
                      bgcolor: (t) => (active ? alpha("#7857FC", t.palette.mode === "dark" ? 0.1 : 0.05) : "transparent"),
                      "&:hover": { borderColor: active ? "#7857FC" : "text.disabled" },
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Iconify
                        icon={active ? "solar:check-circle-bold" : "solar:circle-linear"}
                        width={14} sx={{ color: active ? "#7857FC" : "text.disabled" }}
                      />
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{o.label}</Typography>
                      <Box flex={1} />
                      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                        {o.trialsFor(o.config)}
                      </Typography>
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{o.desc}</Typography>
                  </Box>
                </Tooltip>
              );
            })}
          </Stack>
        </Box>

        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.875 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Model</Typography>
            <Tooltip
              arrow
              title="The model doing the optimizing — reading failures and writing candidate prompts. Not the model your agent runs on."
            >
              <Box component="span" sx={{ display: "flex" }}>
                <Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.disabled" }} />
              </Box>
            </Tooltip>
          </Stack>
          <Stack direction="row" spacing={0.875} flexWrap="wrap" rowGap={0.875}>
            {OPTIMIZER_MODELS.map((m) => {
              const active = m.id === model;
              return (
                <Tooltip key={m.id} arrow title={m.note}>
                  <Chip
                    label={m.label}
                    onClick={() => setModel(m.id)}
                    sx={{
                      height: 27, borderRadius: 0.75, border: "1px solid",
                      borderColor: active ? "#7857FC" : "divider",
                      bgcolor: (t) => (active ? alpha("#7857FC", t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent"),
                      color: active ? "text.primary" : "text.secondary",
                      "& .MuiChip-label": { px: 1.125, typography: "s2", fontWeight: 600 },
                      "&:hover": { bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.12 : 0.06) },
                    }}
                  />
                </Tooltip>
              );
            })}
          </Stack>
        </Box>

        {/* What the search starts from, and what it is never allowed to see. */}
        <Box
          sx={{
            px: 2, py: 1.75, borderRadius: 1, border: "1px solid", borderColor: "divider",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.015),
          }}
        >
          <Stack spacing={1.25}>
            <Row
              label="Starts from"
              value={`${included.length} ${included.length === 1 ? "change" : "changes"} you included`}
              note={included.length
                ? "Every candidate is built from these, so each trial can be read line by line."
                : "Nothing included — the search will explore every change the diagnosis found."}
            />
            <Row
              label="Scenarios"
              value={`${split?.trainMeasured || 0} train · ${split?.heldMeasured || 0} held out`}
              note="Release blockers are dealt into both halves. The headline number comes from the held-out set, which the optimizer never sees."
            />
            <Row
              label="Cost"
              value={`${trials} trials × ${split?.trainMeasured || 0} scenarios = ${episodes} episodes`}
              note="A trial is a full run through the real tools, not a dataset lookup."
            />
          </Stack>
        </Box>
      </Stack>

      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          Only the prompt is searched. Tools, memory and architecture stay as changes you hand off.
        </Typography>
        <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained" color="primary"
          disabled={!name.trim()}
          onClick={() => onStart({ name: name.trim(), optimizerId, model })}
          startIcon={<Iconify icon="solar:play-bold" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Start optimization
        </Button>
      </Stack>
    </Dialog>
  );
}

CreateOptimizationModal.propTypes = {
  open: PropTypes.bool,
  envState: PropTypes.object,
  included: PropTypes.array,
  split: PropTypes.object,
  onClose: PropTypes.func,
  onStart: PropTypes.func,
};

function Row({ label, value, note }) {
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", width: 78, flexShrink: 0 }}>
          {label}
        </Typography>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{value}</Typography>
      </Stack>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", pl: "86px" }}>{note}</Typography>
      )}
    </Box>
  );
}

Row.propTypes = { label: PropTypes.string, value: PropTypes.string, note: PropTypes.string };
