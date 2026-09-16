import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "./buildTones";

// The shape of one live pipeline step (a BUILD_PIPELINE entry decorated with a
// derived `status` and, on a failed step, its `failure`). Exported so the
// popover reuses the exact same contract.
export const STEP_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  phase: PropTypes.string,
  milestone: PropTypes.string,
  label: PropTypes.string,
  detail: PropTypes.string,
  status: PropTypes.oneOf(["done", "running", "pending", "failed"]),
  failure: PropTypes.shape({
    stepId: PropTypes.string,
    title: PropTypes.string,
    detail: PropTypes.string,
    retryable: PropTypes.bool,
    onRetry: PropTypes.func,
  }),
});

/*
  One pipeline row.

  Failed rows expand — a red banner underneath with the error title, detail
  and a retry button. Nothing worth reading is more than one click away.
*/
export default function PipelineRow({ step }) {
  const [open, setOpen] = useState(step?.status === "failed");
  const failed = step?.status === "failed";

  const icon = (() => {
    if (step?.status === "done") return <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: BUILD_TONES.green, display: "block" }} />;
    if (step?.status === "failed") return <Iconify icon="solar:close-circle-bold" width={16} sx={{ color: BUILD_TONES.red, display: "block" }} />;
    if (step?.status === "running") {
      return (
        <Box
          sx={{
            width: 12, height: 12, mt: "2px", borderRadius: "50%",
            border: "2px solid", borderColor: BUILD_TONES.amber, borderTopColor: "transparent",
            animation: "spin 0.8s linear infinite",
            "@keyframes spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
      );
    }
    return <Iconify icon="solar:circle-linear" width={16} sx={{ color: "text.disabled", display: "block" }} />;
  })();

  if (!step) return null;

  return (
    <Box>
      <Stack
        direction="row" alignItems="flex-start" spacing={1.25}
        onClick={() => failed && setOpen((o) => !o)}
        sx={{
          px: 2, py: 1,
          cursor: failed ? "pointer" : "default",
          "&:hover": failed ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Box sx={{ mt: "2px", flexShrink: 0 }}>{icon}</Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography
              sx={{
                typography: "s2", fontWeight: "fontWeightBold",
                color: step.status === "pending" ? "text.subtitle" : failed ? BUILD_TONES.red : "text.primary",
              }}
            >
              {step.label}
            </Typography>
            {failed && (
              <Iconify
                icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                width={12} sx={{ color: BUILD_TONES.red }}
              />
            )}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
            {step.detail}
          </Typography>
        </Box>
      </Stack>

      {failed && open && step.failure && (
        <Box
          sx={{
            mx: 1.5, mb: 1, px: 1.75, py: 1.5, borderRadius: 1,
            border: "1px solid", borderColor: alpha(BUILD_TONES.red, 0.35),
            bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: BUILD_TONES.red, mb: 0.5 }}>
            {step.failure.title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1.5 }}>
            {step.failure.detail}
          </Typography>
          <Stack direction="row" spacing={1}>
            {step.failure.retryable !== false && (
              <Button
                size="small" variant="contained"
                startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
                sx={{ typography: "s3", fontWeight: "fontWeightBold", bgcolor: BUILD_TONES.red, "&:hover": { bgcolor: BUILD_TONES.redHover } }}
                onClick={(e) => { e.stopPropagation(); step.failure.onRetry?.(); }}
              >
                Retry
              </Button>
            )}
            <Button
              size="small" variant="outlined"
              startIcon={<Iconify icon="solar:document-text-linear" width={13} />}
              sx={{
                typography: "s3", fontWeight: "fontWeightBold", color: "text.primary",
                borderColor: (t) => alpha(t.palette.text.primary, 0.2),
              }}
              onClick={(e) => e.stopPropagation()}
            >
              View log
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
}

PipelineRow.propTypes = { step: STEP_SHAPE };
