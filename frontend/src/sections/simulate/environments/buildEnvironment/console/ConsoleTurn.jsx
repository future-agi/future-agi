import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse } from "@mui/material";
import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";

const MONO = "ui-monospace, Menlo, monospace";

// Module-local (not exported) so it can be shared by Step and Turn without
// tripping react-refresh/only-export-components.
const STEP_SHAPE = PropTypes.shape({
  kind: PropTypes.oneOf(["think", "note", "tool", "file", "json"]),
  text: PropTypes.string,
  label: PropTypes.string,
  result: PropTypes.string,
  path: PropTypes.string,
  note: PropTypes.string,
  value: PropTypes.string,
});

/* ── one turn ────────────────────────────────────────────────────────────── */

export function Turn({ turn }) {
  if (!turn) return null;

  if (turn.role === "user") {
    return (
      <Stack alignItems="flex-end">
        <Box
          sx={{
            maxWidth: "82%", px: 1.75, py: 1.125, borderRadius: 2,
            bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.14 : 0.08),
            border: "1px solid",
            borderColor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.22 : 0.16),
          }}
        >
          <Typography sx={{ typography: "s2", lineHeight: 1.55, color: "text.primary" }}>{turn.text}</Typography>
        </Box>
      </Stack>
    );
  }

  return (
    <Stack spacing={1.5}>
      {turn.title && (
        // Section header — reads like a document heading, not a chip label.
        // Uppercase + tracked so it marks a new phase in the builder's
        // narrative and doesn't collide with the tool-call rows underneath.
        <Typography
          sx={{
            typography: "s3", fontWeight: "fontWeightBold",
            color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6,
            pt: 0.5,
          }}
        >
          {turn.title}
        </Typography>
      )}
      {(turn.steps || []).map((step, i) => <Step key={i} step={step} />)}
    </Stack>
  );
}
Turn.propTypes = {
  turn: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.oneOf(["builder", "user"]),
    title: PropTypes.string,
    text: PropTypes.string,
    steps: PropTypes.arrayOf(STEP_SHAPE),
  }),
};

/* ── one step ────────────────────────────────────────────────────────────── */

// Prose reads like a document; tool calls and file writes render as a *line*
// (a small coloured dot + mono label + muted result); expandable payloads stay
// expandable but drop their heavy background fill.
export function Step({ step }) {
  const [open, setOpen] = useState(false);

  if (!step) return null;

  if (step.kind === "think" || step.kind === "note") {
    return (
      <Typography
        sx={{
          typography: "s2", lineHeight: 1.65,
          color: step.kind === "think" ? "text.secondary" : "text.primary",
        }}
      >
        {step.text}
      </Typography>
    );
  }

  if (step.kind === "file") {
    return (
      <QuietRow tint={BUILD_TONES.green}>
        <Typography sx={{ typography: "s3", fontFamily: MONO, flexShrink: 0, color: "text.primary" }}>
          {step.path}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
          {step.note}
        </Typography>
      </QuietRow>
    );
  }

  if (step.kind === "json") {
    return (
      <Box>
        <Stack
          direction="row" alignItems="center" spacing={1}
          onClick={() => setOpen((o) => !o)}
          sx={{
            py: 0.5, cursor: "pointer", borderRadius: 1,
            "&:hover": { bgcolor: "action.hover" },
            px: 0.75,
          }}
        >
          <Iconify
            icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
            width={12}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}>
            {step.label}
          </Typography>
        </Stack>
        <Collapse in={open} unmountOnExit>
          <Typography
            sx={{
              ml: 2.5, mt: 0.5, px: 1.25, py: 1, borderRadius: 1,
              typography: "s3", color: "text.secondary", whiteSpace: "pre-wrap",
              fontFamily: MONO,
              bgcolor: "background.neutral",
            }}
          >
            {step.value}
          </Typography>
        </Collapse>
      </Box>
    );
  }

  // tool call
  return (
    <QuietRow tint={BUILD_TONES.green}>
      <Typography sx={{ typography: "s3", fontFamily: MONO, flexShrink: 0, color: "text.primary" }}>
        {step.label}
      </Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
        {step.result}
      </Typography>
    </QuietRow>
  );
}
Step.propTypes = { step: STEP_SHAPE };

/* ── step-row primitive ──────────────────────────────────────────────────── */

// A coloured dot on the left, whatever the caller passes on the right. No
// background fill, no border — repeated in a row it reads as a list of results,
// not a stack of chips.
export function QuietRow({ tint, children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 0.5, py: 0.25 }}>
      <Box
        sx={{
          width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
          bgcolor: tint,
          boxShadow: (t) => `0 0 0 3px ${alpha(tint, t.palette.mode === "dark" ? 0.15 : 0.12)}`,
        }}
      />
      {children}
    </Stack>
  );
}
QuietRow.propTypes = { tint: PropTypes.string, children: PropTypes.node };

/* ── working indicator ───────────────────────────────────────────────────── */

export function Working({ label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Box
        sx={{
          width: 7, height: 7, borderRadius: "50%", bgcolor: "text.subtitle",
          animation: "pulse 1.2s ease-in-out infinite",
          "@keyframes pulse": { "0%,100%": { opacity: 0.3 }, "50%": { opacity: 1 } },
        }}
      />
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
}
Working.propTypes = { label: PropTypes.string };
