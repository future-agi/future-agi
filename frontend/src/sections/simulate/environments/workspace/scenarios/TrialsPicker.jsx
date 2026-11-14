import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box, Stack, Typography, Button, Tooltip, Popover, TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";

// Reliability dial — pick how many times each scenario should run. 1 is the
// single-shot; 3/5/8 are the common repeat counts; Custom covers anything else
// in 1–20. Same presets and popover shape as the selection bar's inline picker,
// extracted so the environment header can share the identical control.
const TRIAL_PRESETS = [1, 3, 5, 8];
const DEFAULT_TRIALS_LABEL = 1;

export default function TrialsPicker({ trials, onChange, scenarioCount = 0 }) {
  const [anchor, setAnchor] = useState(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState(String(trials || 1));

  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  const totalRuns = scenarioCount * k;
  // A value no preset matches was set through Custom, so Custom is the row
  // that shows as chosen.
  const isCustom = !TRIAL_PRESETS.includes(k);

  const apply = (v) => {
    onChange?.(v);
    setAnchor(null);
    setCustomOpen(false);
    setCustomValue(String(v));
  };
  const commitCustom = () => {
    const n = Math.max(1, Math.min(20, Math.floor(Number(customValue) || 1)));
    apply(n);
  };

  return (
    <>
      <Tooltip arrow title="How many times to run each scenario (reliability across trials)">
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={(e) => setAnchor(e.currentTarget)}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          startIcon={<Iconify icon="solar:repeat-linear" width={14} />}
          sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
        >
          Repeats: {k}
        </Button>
      </Tooltip>

      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setCustomOpen(false); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240, p: 0.5, mt: 0.5 } } }}
      >
        <Box sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", color: "text.primary" }}>
            Repeats per scenario
          </Typography>
          {scenarioCount > 0 && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
              {scenarioCount} {scenarioCount === 1 ? "scenario" : "scenarios"} × {k} {k === 1 ? "repeat" : "repeats"} = {totalRuns} runs
            </Typography>
          )}
          <Typography sx={{ typography: "s3", color: k < 3 ? "status.warning.fg" : "text.subtitle", mt: 0.25 }}>
            Use 3 or more for a result you&apos;d base a decision on.
          </Typography>
        </Box>
        {TRIAL_PRESETS.map((v) => {
          const active = v === k;
          return (
            <Box
              key={v}
              role="menuitemradio"
              aria-checked={active}
              onClick={() => apply(v)}
              sx={{
                display: "flex", alignItems: "center", gap: 1,
                px: 1.5, minHeight: 30, borderRadius: 1, cursor: "pointer",
                bgcolor: active ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box sx={{
                width: 34, textAlign: "left",
                typography: "s1", fontWeight: active ? "fontWeightMedium" : "fontWeightRegular",
                color: "text.primary",
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
              {active && <Iconify icon="solar:check-linear" width={14} sx={{ color: "text.primary" }} />}
            </Box>
          );
        })}
        <Box sx={{ borderTop: "1px solid", borderColor: "divider", mt: 0.5, pt: 0.5 }}>
          {!customOpen ? (
            <Box
              role="menuitemradio"
              aria-checked={isCustom}
              onClick={() => setCustomOpen(true)}
              sx={{
                display: "flex", alignItems: "center", gap: 1,
                px: 1.5, minHeight: 30, borderRadius: 1, cursor: "pointer",
                bgcolor: isCustom ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify icon="solar:pen-2-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s1", fontWeight: isCustom ? "fontWeightMedium" : "fontWeightRegular", color: "text.primary" }}>
                Custom…
              </Typography>
              {isCustom && (
                <Typography sx={{
                  typography: "s1", fontWeight: "fontWeightMedium", color: "text.primary",
                  fontVariantNumeric: "tabular-nums",
                }}>
                  {k}×
                </Typography>
              )}
              <Box sx={{ flex: 1 }} />
              {isCustom && <Iconify icon="solar:check-linear" width={14} sx={{ color: "text.primary" }} />}
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
                inputProps={{ min: 1, max: 20, style: { width: 56 } }}
                sx={{ "& .MuiOutlinedInput-root": { fontVariantNumeric: "tabular-nums" } }}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
                1 – 20 repeats
              </Typography>
              <Button
                size="small" variant="contained"
                onClick={commitCustom}
              >
                Set
              </Button>
            </Stack>
          )}
        </Box>
      </Popover>
    </>
  );
}
TrialsPicker.propTypes = {
  trials: PropTypes.number,
  onChange: PropTypes.func,
  scenarioCount: PropTypes.number,
  // Accepted for existing callers; the button now follows the theme size.
  size: PropTypes.oneOf(["sm", "xs"]),
};
