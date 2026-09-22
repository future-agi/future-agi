import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip, Popover, TextField } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Trial presets. The PRD (§10.2 AC-10.7) requires k configurable
  ("k times"), not a fixed set — the numbers here are the ones the
  PRD's own test cases exercise: 3 (comment in LiveRunView, and
  the smallest n where "passed twice, failed once" is sayable),
  5 (TC-12.7 uses 5/8), 8 (TC-10.5, TC-12.7). 1 is the trivial
  single-shot. Custom covers anything else in 1–20.

  No descriptor text — "smoke" and "deep audit" belong to the
  coverage dial in §9.2 (how many scenarios to generate), which
  is a different dial. Reusing those words here would conflate
  two orthogonal concepts.
*/
const TRIAL_PRESETS = [1, 3, 5, 8];
const DEFAULT_TRIALS_LABEL = 3;

/**
 * Bulk-action bar for the scenario table.
 *
 * Renders IN-PLACE where the search / group-by / filter toolbar
 * normally lives — same row of the SectionCard, same height, same
 * horizontal padding. When one or more scenarios are checked, the
 * toolbar transforms into this bar; when nothing is checked, the
 * toolbar returns. It's the Gmail / GitHub-changes-page pattern:
 * one location for both states, so the actions appear exactly
 * where the user's eye already is.
 *
 * PRD §10.2 AC-10.7 (MUST) — "Reliability (repeat): Re-run a
 * scenario k times to measure consistency (pass across trials)."
 * The Trials pill picks k inline, so a smoke run and a deep-audit
 * run share one control instead of hiding k behind a modal.
 *
 * Hierarchy (left → right):
 *   [count]  Clear   |    Edit    Delete   Trials [k▾]   [▶ Run N × k]
 *   context  utility      secondary  destructive  dial       PRIMARY
 */
export default function SelectionBar({
  count, trials, onTrialsChange, onRun, onEdit, onDelete, onClear,
}) {
  const [trialsAnchor, setTrialsAnchor] = useState(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState(String(trials || 3));

  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  const totalRuns = count * k;
  const estSeconds = totalRuns * 10; // ~10s / run in the mock player
  const timeHint = estSeconds < 60
    ? `~${estSeconds}s`
    : `~${Math.max(1, Math.round(estSeconds / 60))} min`;
  const runLabel = k > 1
    ? `Run ${count} × ${k}`
    : `Run ${count} selected`;

  const applyPreset = (v) => {
    onTrialsChange?.(v);
    setTrialsAnchor(null);
    setCustomOpen(false);
    setCustomValue(String(v));
  };
  const commitCustom = () => {
    const n = Math.max(1, Math.min(20, Math.floor(Number(customValue) || 1)));
    onTrialsChange?.(n);
    setTrialsAnchor(null);
    setCustomOpen(false);
    setCustomValue(String(n));
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        width: "100%",
        px: 2.5, py: 1.25,
        minHeight: 52,
        bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      {/* CONTEXT — how many are selected */}
      <Stack direction="row" alignItems="center" spacing={0.875} sx={{ flexShrink: 0 }}>
        <Box sx={{
          width: 22, height: 22, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: "text.primary",
          color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
          fontSize: 11, fontWeight: 700, fontVariantNumeric: "tabular-nums",
        }}>
          {count}
        </Box>
        <Typography sx={{
          typography: "s2", fontWeight: 600, fontSize: 13,
          color: "text.primary", whiteSpace: "nowrap",
        }}>
          {count === 1 ? "scenario selected" : "scenarios selected"}
        </Typography>
      </Stack>

      {/* Clear — utility */}
      <Tooltip arrow title="Deselect all">
        <Button
          size="small"
          onClick={onClear}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12,
            color: "text.subtitle", minWidth: 0, px: 1, py: 0.375,
            "&:hover": { color: "text.primary", bgcolor: "action.hover" },
          }}
        >
          Clear
        </Button>
      </Tooltip>

      <Box sx={{ flex: 1 }} />

      {/* SECONDARY — Edit with builder chat */}
      {onEdit && (
        <Tooltip arrow title="Send this selection to the builder chat">
          <Button
            size="small"
            onClick={onEdit}
            variant="outlined"
            startIcon={<Iconify icon="solar:chat-round-line-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 600, fontSize: 12.5,
              color: "text.primary",
              borderColor: "divider",
              px: 1.5, py: 0.5, minWidth: 0,
              whiteSpace: "nowrap",
              "&:hover": {
                borderColor: (t) => alpha(t.palette.text.primary, 0.4),
                bgcolor: "action.hover",
              },
            }}
          >
            Edit
          </Button>
        </Tooltip>
      )}

      {/* DESTRUCTIVE — Delete */}
      <Tooltip arrow title="Delete selected scenarios">
        <Button
          size="small"
          onClick={onDelete}
          variant="outlined"
          startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12.5,
            color: "#DC2626",
            borderColor: (t) => alpha("#DC2626", 0.4),
            px: 1.5, py: 0.5, minWidth: 0,
            whiteSpace: "nowrap",
            "&:hover": {
              borderColor: "#DC2626",
              bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
            },
          }}
        >
          Delete
        </Button>
      </Tooltip>

      {/* TRIALS — reliability dial (PRD AC-10.7) */}
      {onTrialsChange && (
        <Tooltip arrow title="How many times to run each scenario — reliability across trials">
          <Button
            size="small"
            variant="outlined"
            onClick={(e) => setTrialsAnchor(e.currentTarget)}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={11} />}
            startIcon={<Iconify icon="solar:repeat-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 600, fontSize: 12.5,
              color: "text.primary",
              borderColor: "divider",
              px: 1.25, py: 0.5, minWidth: 0,
              whiteSpace: "nowrap",
              "&:hover": {
                borderColor: (t) => alpha(t.palette.text.primary, 0.4),
                bgcolor: "action.hover",
              },
            }}
          >
            Trials: {k}
          </Button>
        </Tooltip>
      )}

      {/* PRIMARY — Run N selected (× k) */}
      {onRun && (
        <Tooltip
          arrow
          title={k > 1
            ? `${count} ${count === 1 ? "scenario" : "scenarios"} × ${k} trials = ${totalRuns} runs · ${timeHint}`
            : `${count} ${count === 1 ? "scenario" : "scenarios"} × 1 trial · ${timeHint}`}
        >
          <Button
            size="small"
            variant="contained"
            onClick={() => onRun(k)}
            startIcon={<Iconify icon="solar:play-bold" width={13} />}
            sx={{
              typography: "s2", fontWeight: 700, fontSize: 12.5,
              px: 1.75, py: 0.5, minWidth: 0,
              bgcolor: "text.primary",
              color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
              boxShadow: "none",
              whiteSpace: "nowrap",
              "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
            }}
          >
            {runLabel}
          </Button>
        </Tooltip>
      )}

      {/* Trials popover — presets + custom */}
      <Popover
        open={!!trialsAnchor}
        anchorEl={trialsAnchor}
        onClose={() => { setTrialsAnchor(null); setCustomOpen(false); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240, p: 0.5, mt: 0.5 } } }}
      >
        <Box sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary" }}>
            Trials per scenario
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5, mt: 0.25 }}>
            {count} {count === 1 ? "scenario" : "scenarios"} × {k} trials = {totalRuns} runs
          </Typography>
        </Box>
        {TRIAL_PRESETS.map((v) => {
          const active = v === k;
          return (
            <Box
              key={v}
              onClick={() => applyPreset(v)}
              sx={{
                display: "flex", alignItems: "center", gap: 1,
                px: 1.5, py: 0.875, borderRadius: 0.75, cursor: "pointer",
                bgcolor: active ? "action.hover" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box sx={{
                width: 34, textAlign: "left",
                typography: "s2", fontWeight: active ? 700 : 600,
                color: active ? "primary.main" : "text.primary",
                fontVariantNumeric: "tabular-nums",
              }}>
                {v}×
              </Box>
              {v === DEFAULT_TRIALS_LABEL && (
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
                  Default
                </Typography>
              )}
              <Box sx={{ flex: 1 }} />
              {active && <Iconify icon="eva:checkmark-fill" width={14} sx={{ color: "primary.main" }} />}
            </Box>
          );
        })}
        <Box sx={{ borderTop: "1px solid", borderColor: "divider", mt: 0.5, pt: 0.5 }}>
          {!customOpen ? (
            <Box
              onClick={() => setCustomOpen(true)}
              sx={{
                display: "flex", alignItems: "center", gap: 1,
                px: 1.5, py: 0.875, borderRadius: 0.75, cursor: "pointer",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify icon="solar:pen-2-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
                Custom…
              </Typography>
            </Box>
          ) : (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ px: 1.5, py: 0.75 }}>
              <TextField
                size="small"
                type="number"
                autoFocus
                value={customValue}
                onChange={(e) => setCustomValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") commitCustom(); }}
                inputProps={{ min: 1, max: 20, style: { padding: "6px 8px", fontSize: 13, width: 56 } }}
                sx={{ "& .MuiOutlinedInput-root": { fontVariantNumeric: "tabular-nums" } }}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
                1 – 20 trials
              </Typography>
              <Button
                size="small" variant="contained"
                onClick={commitCustom}
                sx={{
                  typography: "s3", fontWeight: 700,
                  bgcolor: "text.primary",
                  color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
                  boxShadow: "none", minWidth: 0, px: 1.25, py: 0.375,
                  "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
                }}
              >
                Set
              </Button>
            </Stack>
          )}
        </Box>
      </Popover>
    </Stack>
  );
}
SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  trials: PropTypes.number,
  onTrialsChange: PropTypes.func,
  onRun: PropTypes.func,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
