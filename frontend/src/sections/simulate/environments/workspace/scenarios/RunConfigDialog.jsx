import PropTypes from "prop-types";
import { useState, useEffect } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Dialog, DialogContent, IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";

// Trial presets identical to the SelectionBar / TrialsPicker so the vocabulary
// is one thing. Kept local so this dialog stays a leaf with no cross-imports.
const TRIAL_PRESETS = [1, 3, 5, 8];

// Run configuration dialog — opens when the user clicks "Run simulation" in the
// environment header. Shows how many scenarios will run, a repeats-per-scenario
// picker, and Cancel / Run. Modal so the
// "how many times?" question is unmissable for first-time users; the inline
// TrialsPicker pill in the header is the faster path once known.
export default function RunConfigDialog({
  open, onClose, scenarioCount, defaultTrials = 1, onConfirm,
}) {
  const [trials, setTrials] = useState(defaultTrials);
  const [custom, setCustom] = useState(false);
  const [customValue, setCustomValue] = useState(String(defaultTrials));

  useEffect(() => {
    if (open) {
      setTrials(defaultTrials);
      setCustom(false);
      setCustomValue(String(defaultTrials));
    }
  }, [open, defaultTrials]);

  const k = Math.max(1, Math.min(20, Number(trials) || 1));

  const applyCustom = () => {
    const n = Math.max(1, Math.min(20, Math.floor(Number(customValue) || 1)));
    setTrials(n);
    setCustom(false);
  };

  return (
    <Dialog
      open={open} onClose={onClose}
      maxWidth="xs" fullWidth
      slotProps={{ paper: { sx: { borderRadius: 1.5 } } }}
    >
      <DialogContent sx={{ p: 0 }}>
        <Stack direction="row" alignItems="center" spacing={1.5}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}
        >
          <Iconify icon="solar:play-bold" width={16} sx={{ color: "text.primary" }} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ fontSize: 15, fontWeight: 700, letterSpacing: -0.2 }}>
              Run simulation
            </Typography>
            <Typography sx={{ fontSize: 12, color: "text.subtitle", mt: 0.25 }}>
              {scenarioCount} {scenarioCount === 1 ? "scenario" : "scenarios"} in this environment
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="eva:close-fill" width={16} />
          </IconButton>
        </Stack>

        <Box sx={{ px: 2.5, pt: 2.25, pb: 1 }}>
          <Typography sx={{
            fontSize: 11, fontWeight: 600, color: "text.subtitle",
            textTransform: "uppercase", letterSpacing: 0.5, mb: 1.25,
          }}>
            Repeats per scenario
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 1.25 }}>
            {TRIAL_PRESETS.map((v) => {
              const active = !custom && v === k;
              return (
                <Box
                  key={v}
                  component="button"
                  type="button"
                  onClick={() => { setTrials(v); setCustom(false); }}
                  sx={{
                    flex: 1, py: 1, borderRadius: 1, cursor: "pointer",
                    bgcolor: "transparent", font: "inherit",
                    border: "1px solid",
                    borderColor: active ? "text.primary" : "divider",
                    color: active ? "text.primary" : "text.subtitle",
                    fontWeight: active ? 700 : 600,
                    fontSize: 14, fontVariantNumeric: "tabular-nums",
                    transition: "border-color 120ms, color 120ms",
                    "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.4), color: "text.primary" },
                  }}
                >
                  {v}×
                  {v === 1 && (
                    <Typography component="span" sx={{ display: "block", fontSize: 9.5, color: "text.subtitle", fontWeight: 500, mt: 0.25 }}>
                      Default
                    </Typography>
                  )}
                </Box>
              );
            })}
            <Box
              component="button"
              type="button"
              onClick={() => setCustom(true)}
              sx={{
                flex: 1, py: 1, borderRadius: 1, cursor: "pointer",
                bgcolor: "transparent", font: "inherit",
                border: "1px solid",
                borderColor: custom ? "text.primary" : "divider",
                color: custom ? "text.primary" : "text.subtitle",
                fontWeight: custom ? 700 : 600,
                fontSize: 13,
              }}
            >
              Custom
            </Box>
          </Stack>
          {custom && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.25 }}>
              <Box
                component="input"
                type="number"
                min={1}
                max={20}
                autoFocus
                value={customValue}
                onChange={(e) => setCustomValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyCustom(); }}
                sx={{
                  width: 88, py: 1, px: 1.25, borderRadius: 1,
                  border: "1px solid", borderColor: "divider", font: "inherit",
                  fontSize: 14, fontVariantNumeric: "tabular-nums",
                  bgcolor: "background.paper", color: "text.primary",
                  outline: "none",
                  "&:focus": { borderColor: "text.primary" },
                }}
              />
              <Typography sx={{ fontSize: 12, color: "text.subtitle", flex: 1 }}>
                1 – 20 repeats
              </Typography>
              <Button
                size="small" variant="contained"
                onClick={applyCustom}
                sx={{
                  fontSize: 12, fontWeight: 700,
                  bgcolor: "text.primary",
                  color: (t) => (t.palette.mode === "dark" ? "background.default" : "background.paper"),
                  boxShadow: "none", px: 1.5,
                  "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
                }}
              >
                Set
              </Button>
            </Stack>
          )}
        </Box>

        <Stack direction="row" spacing={1} sx={{
          px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider",
          justifyContent: "flex-end",
        }}>
          <Button
            onClick={onClose}
            sx={{
              fontSize: 13, fontWeight: 600, color: "text.subtitle",
              px: 2, "&:hover": { color: "text.primary", bgcolor: "action.hover" },
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={() => { onConfirm?.(k); onClose(); }}
            startIcon={<Iconify icon="solar:play-bold" width={13} />}
            sx={{
              fontSize: 13, fontWeight: 700, px: 2.25,
              bgcolor: "text.primary",
              color: (t) => (t.palette.mode === "dark" ? "background.default" : "background.paper"),
              boxShadow: "none",
              "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
            }}
          >
            Run simulation
          </Button>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}
RunConfigDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  scenarioCount: PropTypes.number,
  defaultTrials: PropTypes.number,
  onConfirm: PropTypes.func,
};
