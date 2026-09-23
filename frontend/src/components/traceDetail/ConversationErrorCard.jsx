import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { getMarkColor } from "src/utils/utils";

/*
  Error localization for a conversation: one card per flagged turn, most
  severe first. Shows the customer line that prompted the turn, the agent's
  reply highlighted, and why it failed + the fix — no clipping, no toggles.
  Segments carry `turn` ({ index, text, context }) and `severity`.
*/

const SEVERITY = {
  high: { label: "High", color: "#DC2626" },
  medium: { label: "Medium", color: "#EA580C" },
  low: { label: "Low", color: "#CA8A04" },
};
const ORDER = { high: 0, medium: 1, low: 2 };

const severityOf = (seg) => {
  if (SEVERITY[seg?.severity]) return seg.severity;
  const w = seg?.weight ?? seg?.rank ?? 0;
  return w >= 0.8 ? "high" : w >= 0.55 ? "medium" : "low";
};

function SeverityChip({ level }) {
  const s = SEVERITY[level];
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{ px: 0.75, py: 0.25, borderRadius: 0.5, bgcolor: alpha(s.color, 0.12) }}
    >
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: s.color }} />
      <Typography sx={{ typography: "s3", fontWeight: 600, color: s.color, lineHeight: 1.4 }}>
        {s.label}
      </Typography>
    </Stack>
  );
}
SeverityChip.propTypes = { level: PropTypes.string };

function Line({ who, children, muted }) {
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      <Typography
        sx={{
          typography: "s3", fontWeight: 600, width: 62, flexShrink: 0,
          color: "text.disabled", textTransform: "uppercase", letterSpacing: 0.3,
        }}
      >
        {who}
      </Typography>
      <Typography sx={{ typography: "s2", color: muted ? "text.secondary" : "text.primary", lineHeight: 1.6, minWidth: 0 }}>
        {children}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { who: PropTypes.node, children: PropTypes.node, muted: PropTypes.bool };

function Note({ icon, label, children }) {
  if (!children) return null;
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start">
      <Iconify icon={icon} width={14} sx={{ color: "text.subtitle", mt: "3px", flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.primary" }}>{label}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", lineHeight: 1.6 }}>{children}</Typography>
      </Box>
    </Stack>
  );
}
Note.propTypes = { icon: PropTypes.string, label: PropTypes.node, children: PropTypes.node };

export default function ConversationErrorCard({ segments }) {
  const ordered = [...(segments || [])]
    .filter((s) => s?.turn)
    .sort((a, b) => (ORDER[severityOf(a)] - ORDER[severityOf(b)])
      || ((a.priority ?? 0) - (b.priority ?? 0))
      || (a.turn.index - b.turn.index));
  if (!ordered.length) return null;

  return (
    <Stack spacing={1}>
      {ordered.map((seg, i) => {
        const level = severityOf(seg);
        return (
          <Box
            key={seg.unit_key || seg.unitKey || i}
            sx={{
              p: 1.5, borderRadius: 1,
              border: "1px solid", borderColor: "divider",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.01),
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.25 }}>
              <SeverityChip level={level} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Turn {seg.turn.index}
              </Typography>
            </Stack>

            <Stack spacing={0.75} sx={{ mb: 1.5 }}>
              {seg.turn.context && <Line who="Customer" muted>{seg.turn.context}</Line>}
              <Line who="Agent">
                <Box
                  component="span"
                  sx={{
                    bgcolor: getMarkColor(seg.weight ?? seg.rank ?? 0.9),
                    borderRadius: "2px",
                    px: "2px",
                    boxDecorationBreak: "clone",
                    WebkitBoxDecorationBreak: "clone",
                  }}
                >
                  {seg.turn.text}
                </Box>
              </Line>
            </Stack>

            <Stack spacing={1} sx={{ pt: 1.25, borderTop: "1px solid", borderColor: "divider" }}>
              <Note icon="solar:danger-circle-linear" label={seg.verdict === "passed" ? "Why it lost points" : "Why it failed"}>
                {seg.reason}
              </Note>
              <Note icon="solar:lightbulb-bolt-linear" label="Suggested fix">{seg.improvement}</Note>
            </Stack>
          </Box>
        );
      })}
    </Stack>
  );
}

ConversationErrorCard.propTypes = { segments: PropTypes.array };
