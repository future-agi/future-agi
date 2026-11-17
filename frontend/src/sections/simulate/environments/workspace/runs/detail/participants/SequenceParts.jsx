import { useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { SEQUENCE_COPY as C } from "./sequence.constants";

export function Pill({ n, bad, refused }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 20,
        height: 18,
        px: 0.625,
        borderRadius: "9px",
        typography: "s3",
        fontWeight: 600,
        fontVariantNumeric: "tabular-nums",
        flexShrink: 0,
        bgcolor: bad ? "error.main" : refused ? "warning.main" : "text.primary",
        color: bad || refused ? "common.white" : "background.paper",
      }}
    >
      {n}
    </Box>
  );
}
Pill.propTypes = {
  n: PropTypes.number,
  bad: PropTypes.bool,
  refused: PropTypes.bool,
};

/* A small on/off control for the view (not an action). */
export function Toggle({ on, onClick, children, disabled }) {
  return (
    <Box
      component="button"
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      sx={{
        height: 24,
        px: 1,
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: on ? "action.selected" : "background.paper",
        color: disabled ? "text.disabled" : "text.primary",
        typography: "s3",
        fontWeight: 500,
        cursor: disabled ? "default" : "pointer",
        whiteSpace: "nowrap",
        "&:hover": disabled
          ? {}
          : { bgcolor: on ? "action.selected" : "action.hover" },
      }}
    >
      {on && <Iconify icon="solar:check-read-linear" width={14} />}
      {children}
    </Box>
  );
}
Toggle.propTypes = {
  on: PropTypes.bool,
  onClick: PropTypes.func,
  children: PropTypes.node,
  disabled: PropTypes.bool,
};

export function Legend({ seq }) {
  const theme = useTheme();
  const sub = theme.palette.text.secondary;
  const bad = theme.palette.error.main;
  const warn = theme.palette.warning.main;
  const has = (t) => seq.rows.some((r) => r.type === t);
  const item = (key, line, text) => (
    <Stack key={key} direction="row" spacing={0.75} alignItems="center">
      {line}
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {text}
      </Typography>
    </Stack>
  );
  const seg = (stroke, dash) => (
    <svg width="22" height="6" aria-hidden="true">
      <line
        x1="0"
        y1="3"
        x2="22"
        y2="3"
        stroke={stroke}
        strokeWidth="1.5"
        strokeDasharray={dash}
      />
    </svg>
  );
  return (
    <Stack
      direction="row"
      spacing={1.75}
      useFlexGap
      flexWrap="wrap"
      rowGap={0.5}
    >
      {/* What the small tag in front of each arrow's words means. */}
      {[
        ["SAID", "words to or from the caller", true],
        [
          "TASK",
          "handed to a worker",
          seq.steps.some((x) => x.kind === "handoff"),
        ],
        ["RESULT", "a worker's answer", has("back") || has("none")],
        ["TOOL", "a tool call", has("tool")],
      ]
        .filter(([, , on]) => on)
        .map(([k, text]) =>
          item(
            k,
            <Typography
              component="span"
              sx={{
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.06em",
                color: "text.secondary",
              }}
            >
              {k}
            </Typography>,
            text,
          ),
        )}
      {has("back") && item("back", seg(sub, "4 3"), "came back")}
      {has("none") && item("none", seg(bad, "5 3"), "no answer")}
      {seq.steps.some((x) => x.refused) &&
        item("refused", seg(warn), "refused by the world")}
      {seq.steps.some((x) => x.kind === "tool" && x.bad) &&
        item("failed", seg(bad), "failed")}
    </Stack>
  );
}
Legend.propTypes = { seq: PropTypes.object.isRequired };

export function Quote({ children, code, bad }) {
  return (
    <Box
      sx={{
        mt: 0.5,
        px: 1.25,
        py: 1,
        borderRadius: "6px",
        bgcolor: (t) =>
          bad
            ? alpha(t.palette.error.main, 0.08)
            : t.palette.background.neutral,
        color: bad ? "error.main" : "text.secondary",
        typography: "s2",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        ...(code && {
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 11.5,
        }),
      }}
    >
      {children}
    </Box>
  );
}
Quote.propTypes = {
  children: PropTypes.node,
  code: PropTypes.bool,
  bad: PropTypes.bool,
};

// Sits on the first line of the boxed value beside it (the box's 4px margin + 8px padding).
export const Label = ({ children }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary", pt: 1.5 }}>
    {children}
  </Typography>
);
Label.propTypes = { children: PropTypes.node };

// Copy a link that opens the run on this call and step.
export function CopyLink({ url }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* Clipboard refused: the link stays on the button's tooltip. */
    }
  };
  return (
    <Tooltip title={copied ? C.linkCopied : C.copyLink} arrow>
      <IconButton
        size="small"
        onClick={copy}
        aria-label={C.copyLink}
        sx={{
          width: 24,
          height: 24,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
        }}
      >
        <Iconify
          icon={copied ? "solar:check-read-linear" : "solar:link-linear"}
          width={14}
        />
      </IconButton>
    </Tooltip>
  );
}
CopyLink.propTypes = { url: PropTypes.string.isRequired };

/* The picked step: what was asked and what came back, in full, with what
   it cost and links out. */
