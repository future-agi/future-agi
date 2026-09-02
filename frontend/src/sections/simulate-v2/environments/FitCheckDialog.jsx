import PropTypes from "prop-types";
import { useEffect } from "react";
import { alpha } from "@mui/material/styles";
import { Dialog, Box, Stack, Typography, LinearProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import { BootSequence } from "../components/loading";

const TWIN_TINT = "#7857FC";

/**
 * Fit-check popup. Replaces the previous inline "does your agent fit"
 * step — the check reads more like an operation (we're probing a live
 * endpoint) than a form section, so it lives in a modal that runs the
 * scripted probe and auto-completes into the review screen.
 *
 * Non-dismissible while probing so the user can't half-open the review
 * screen with an incomplete probe underneath.
 */
export default function FitCheckDialog({ open, probeSteps, onDone }) {
  useEffect(() => {
    if (!open) return undefined;
    return () => {};
  }, [open]);

  return (
    <Dialog
      open={open}
      disableEscapeKeyDown
      PaperProps={{
        sx: {
          borderRadius: 2, maxWidth: 520, width: "100%",
          bgcolor: "background.paper", backgroundImage: "none",
          border: "1px solid", borderColor: "divider",
        },
      }}
    >
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 2 }}>
          <Box sx={{
            width: 30, height: 30, borderRadius: 1,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: TWIN_TINT,
          }}>
            <Iconify icon="solar:shield-check-bold" width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 700 }}>Checking your agent fits</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Probing declared tools against what this template calls
            </Typography>
          </Box>
        </Stack>

        <Box sx={{
          p: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}>
          <BootSequence
            steps={probeSteps.map((p) => `${p.label} — ${p.result}`)}
            accent={TWIN_TINT}
            stepMs={700}
            onDone={onDone}
          />
        </Box>

        <LinearProgress sx={{
          mt: 2, height: 3, borderRadius: 2,
          bgcolor: "background.neutral",
          "& .MuiLinearProgress-bar": { bgcolor: TWIN_TINT, borderRadius: 2 },
        }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1, textAlign: "center" }}>
          Prototype resolves in a few seconds · in production this is a live probe
        </Typography>
      </Box>
    </Dialog>
  );
}

FitCheckDialog.propTypes = {
  open: PropTypes.bool,
  probeSteps: PropTypes.array,
  onDone: PropTypes.func,
};
