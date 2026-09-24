import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip, Popover, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

// Trial presets — 1 is the single-shot; 3/5/8 are the common repeat counts;
// Custom covers anything else in 1–20. Same set as the header TrialsPicker so
// the vocabulary is one thing.
const TRIAL_PRESETS = [1, 3, 5, 8];
const DEFAULT_TRIALS_LABEL = 1;
const RED = BUILD_TONES.red;

/**
 * Bulk-action bar for the scenario table.
 *
 * Renders IN-PLACE where the search / group-by / filter toolbar normally lives
 * — same row of the SectionCard, same height, same padding. When one or more
 * scenarios are checked the toolbar transforms into this bar (Gmail /
 * GitHub-changes pattern): one location for both states, so the actions appear
 * where the eye already is.
 *
 * Hierarchy (left → right):
 *   [count] ✕  ·  Select all N matching  |  Delete  Repeats[k▾]  ▶ Run (N)
 *   context       escalation (paging)        destructive  dial  PRIMARY
 *
 * Editing is a per-row action (the pencil opens the edit drawer), not a bulk
 * one, so the bar carries no Edit button.
 *
 * `matching` carries our pagination's predicate-selection context so a
 * selection can escalate to "all N matching" without every id being loaded.
 * Each of `onRun` / `onTrialsChange` is optional and gates its own button.
 */
export default function SelectionBar({
  count, trials, onTrialsChange, onRun, onDelete, onClear, matching,
}) {
  const [trialsAnchor, setTrialsAnchor] = useState(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState(String(trials || 1));

  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  const totalRuns = count * k;
  const estSeconds = totalRuns * 10; // ~10s / run in the mock player
  const timeHint = estSeconds < 60
    ? `~${estSeconds}s`
    : `~${Math.max(1, Math.round(estSeconds / 60))} min`;
  const runLabel = `Run simulation (${count})`;

  // Predicate-selection escalation: in include-mode, offer "select all N
  // matching" once the whole visible page is checked and there's more beyond it.
  const isAll = matching?.mode === "all";
  const canEscalate =
    matching && !isAll && matching.pageCount > 0 && matching.total > matching.pageCount;

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
        // Left inset matches the table's checkbox column (TableCell pl: 1.5), so
        // the count chip sits directly over the row checkboxes below. Right keeps
        // the toolbar's inset for the Run button.
        pl: 1.5, pr: 2.5, py: 1.25,
        minHeight: 52,
        bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      {/* CONTEXT — count + clear-all in one pill. The × is the primary
          clear-all, on the same object as the count. */}
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{
          flexShrink: 0,
          pl: 0.375, pr: 0.375, py: 0.375, borderRadius: 999,
          border: "1px solid",
          borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.12),
          bgcolor: "transparent",
        }}
      >
        {isAll ? (
          // Whole-match scope stated as one phrase so the escalation reads as
          // done (and the paging contract can assert it as a unit).
          <Typography sx={{
            typography: "s2", fontWeight: 600, fontSize: 13,
            color: "text.primary", whiteSpace: "nowrap", px: 0.5,
          }}>
            All {count.toLocaleString()} matching {count === 1 ? "scenario" : "scenarios"} selected
          </Typography>
        ) : (
          <>
            <Box sx={{
              width: 22, height: 22, borderRadius: "50%",
              display: "flex", alignItems: "center", justifyContent: "center",
              bgcolor: "text.primary",
              color: (t) => (t.palette.mode === "dark" ? "background.default" : "background.paper"),
              fontSize: 11, fontWeight: 700, fontVariantNumeric: "tabular-nums",
            }}>
              {count}
            </Box>
            <Typography sx={{
              typography: "s2", fontWeight: 600, fontSize: 13,
              color: "text.primary", whiteSpace: "nowrap", pl: 0.25,
            }}>
              {count === 1 ? "scenario selected" : "scenarios selected"}
            </Typography>
          </>
        )}
        <Tooltip arrow title="Clear selection">
          <Box
            component="button"
            type="button"
            onClick={onClear}
            aria-label="Clear selection"
            sx={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: 999,
              bgcolor: "transparent", border: "none", cursor: "pointer",
              color: "text.subtitle",
              transition: "background-color 120ms, color 120ms",
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.08),
                color: "text.primary",
              },
            }}
          >
            <Iconify icon="eva:close-fill" width={14} />
          </Box>
        </Tooltip>
      </Stack>

      {/* ESCALATION — bridge to the whole match while the page is fully checked
          and there's more beyond it. Disappears once taken (mode → all). */}
      {canEscalate && (
        <Button
          size="small"
          onClick={matching.onSelectAll}
          sx={{
            typography: "s2", fontWeight: 700, flexShrink: 0,
            color: "primary.main", minWidth: 0, px: 1,
            whiteSpace: "nowrap",
            "&:hover": { bgcolor: "transparent", textDecoration: "underline" },
          }}
        >
          Select all {matching.total.toLocaleString()} matching
        </Button>
      )}

      <Box sx={{ flex: 1 }} />

      {/* DESTRUCTIVE — Delete */}
      <Tooltip arrow title="Delete selected scenarios">
        <Button
          size="small"
          onClick={onDelete}
          variant="outlined"
          startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12.5,
            color: RED,
            borderColor: alpha(RED, 0.4),
            px: 1.5, py: 0.5, minWidth: 0,
            whiteSpace: "nowrap",
            "&:hover": {
              borderColor: RED,
              bgcolor: (t) => alpha(RED, t.palette.mode === "dark" ? 0.1 : 0.05),
            },
          }}
        >
          Delete
        </Button>
      </Tooltip>

      {/* TRIALS — reliability dial */}
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
            Repeats: {k}
          </Button>
        </Tooltip>
      )}

      {/* PRIMARY — Run N selected (× k) */}
      {onRun && (
        <Tooltip
          arrow
          title={k > 1
            ? `${count} ${count === 1 ? "scenario" : "scenarios"} × ${k} repeats = ${totalRuns} runs · ${timeHint}`
            : `${count} ${count === 1 ? "scenario" : "scenarios"} × 1 repeat · ${timeHint}`}
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
              color: (t) => (t.palette.mode === "dark" ? "background.default" : "background.paper"),
              boxShadow: "none",
              whiteSpace: "nowrap",
              "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
            }}
          >
            {runLabel}
          </Button>
        </Tooltip>
      )}

      {/* Repeats popover — presets + custom */}
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
            Repeats per scenario
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5, mt: 0.25 }}>
            {count} {count === 1 ? "scenario" : "scenarios"} × {k} {k === 1 ? "repeat" : "repeats"} = {totalRuns} runs
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
                1 – 20 repeats
              </Typography>
              <Button
                size="small" variant="contained"
                onClick={commitCustom}
                sx={{
                  typography: "s3", fontWeight: 700,
                  bgcolor: "text.primary",
                  color: (t) => (t.palette.mode === "dark" ? "background.default" : "background.paper"),
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
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  // Select-all-matching context from the pagination predicate. Absent → a plain
  // page selection with no escalation. { mode, total, pageCount, onSelectAll }.
  matching: PropTypes.shape({
    mode: PropTypes.oneOf(["include", "all"]),
    total: PropTypes.number,
    pageCount: PropTypes.number,
    onSelectAll: PropTypes.func,
  }),
};
