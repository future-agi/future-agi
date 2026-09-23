import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
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

export default function TrialsPicker({ trials, onChange, scenarioCount = 0, size = "sm" }) {
  const [anchor, setAnchor] = useState(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState(String(trials || 1));

  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  const totalRuns = scenarioCount * k;

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

  const compact = size === "xs";

  return (
    <>
      <Tooltip arrow title="How many times to run each scenario — reliability across trials">
        <Button
          size="small"
          variant="outlined"
          onClick={(e) => setAnchor(e.currentTarget)}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={11} />}
          startIcon={<Iconify icon="solar:repeat-linear" width={13} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: compact ? 12 : 12.5,
            color: "text.primary",
            borderColor: "divider",
            px: compact ? 1 : 1.25, py: compact ? 0.375 : 0.5, minWidth: 0,
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

      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setCustomOpen(false); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240, p: 0.5, mt: 0.5 } } }}
      >
        <Box sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary" }}>
            Repeats per scenario
          </Typography>
          {scenarioCount > 0 && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5, mt: 0.25 }}>
              {scenarioCount} {scenarioCount === 1 ? "scenario" : "scenarios"} × {k} {k === 1 ? "repeat" : "repeats"} = {totalRuns} runs
            </Typography>
          )}
        </Box>
        {TRIAL_PRESETS.map((v) => {
          const active = v === k;
          return (
            <Box
              key={v}
              onClick={() => apply(v)}
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
    </>
  );
}
TrialsPicker.propTypes = {
  trials: PropTypes.number,
  onChange: PropTypes.func,
  scenarioCount: PropTypes.number,
  size: PropTypes.oneOf(["sm", "xs"]),
};
