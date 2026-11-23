import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";
import {
  CHECK,
  OUTCOME,
  checklistTally,
  conversationLabelsOf,
  evidenceStepsOf,
} from "./issueEvidence";

/*
  One conversation, as the analyzer stored it: its outcome and labels, the
  three steps that make the case (where it starts, where it goes wrong, where
  it shows), and its requirement checklist. Key-value rows; the full step
  reel with each step's input and output sits right below.
*/

const MONO = "ui-monospace, SFMono-Regular, monospace";

const linkSx = {
  ...focusRing,
  flexShrink: 0,
  fontSize: "12px",
  fontWeight: 500,
  color: "text.secondary",
  cursor: "pointer",
  whiteSpace: "nowrap",
  "&:hover": { color: "text.primary", textDecoration: "underline" },
};

function Row({ label, children, last }) {
  const cell = {
    px: 1.5,
    py: 0.75,
    borderBottom: last ? 0 : "1px solid",
    borderColor: "divider",
  };
  return (
    <>
      <Typography
        component="dt"
        fontSize="12px"
        color="text.tertiary"
        sx={{ ...cell, lineHeight: "20px", whiteSpace: "nowrap" }}
      >
        {label}
      </Typography>
      <Box component="dd" sx={{ ...cell, m: 0, minWidth: 0 }}>
        {children}
      </Box>
    </>
  );
}
Row.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  last: PropTypes.bool,
};

/* "policy check: (skipped)  →  Action taken outside the rule" */
function InOut({ step }) {
  if (!step.wentIn && !step.cameOut) return null;
  const code = (v) => (
    <Tooltip title={v} arrow placement="top-start">
      <Box
        component="code"
        sx={{
          minWidth: 0,
          maxWidth: "100%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          px: 0.75,
          borderRadius: "4px",
          bgcolor: "action.hover",
          fontFamily: MONO,
          fontSize: "12px",
          lineHeight: "20px",
          color: "text.primary",
        }}
      >
        {v}
      </Box>
    </Tooltip>
  );
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      columnGap={0.75}
      rowGap={0.25}
      sx={{ mt: 0.25, minWidth: 0 }}
    >
      {step.wentIn && code(step.wentIn)}
      {step.wentIn && step.cameOut && (
        <Iconify
          icon="solar:arrow-right-linear"
          width={12}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      )}
      {step.cameOut && code(step.cameOut)}
    </Stack>
  );
}
InOut.propTypes = { step: PropTypes.object.isRequired };

function Checklist({ list, onJump }) {
  const [all, setAll] = useState(false);
  const open = list.filter((c) => c.status !== "met");
  const shown = all ? list : open;
  const hidden = list.length - open.length;
  return (
    <Stack gap={0.25}>
      <Typography
        fontSize="13px"
        color="text.primary"
        sx={{ lineHeight: "20px", fontVariantNumeric: "tabular-nums" }}
      >
        {checklistTally(list)}
      </Typography>
      {shown.map((c, i) => {
        const k = CHECK[c.status] || CHECK.unclear;
        return (
          <Stack
            key={i}
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ minHeight: 24, minWidth: 0 }}
          >
            <Tooltip title={k.label} arrow>
              <Box component="span" sx={{ display: "inline-flex" }}>
                <Iconify icon={k.icon} width={14} sx={{ color: k.color }} />
              </Box>
            </Tooltip>
            <Typography
              fontSize="13px"
              color="text.primary"
              noWrap
              sx={{ minWidth: 0 }}
            >
              {c.text}
              {c.note && (
                <Box component="span" sx={{ color: "text.tertiary" }}>
                  {` (${c.note})`}
                </Box>
              )}
            </Typography>
            {c.status === "broken" && c.proofStep != null && onJump && (
              <Box
                {...clickable(() => onJump(c.proofStep), { role: "link" })}
                sx={linkSx}
              >
                See proof
              </Box>
            )}
          </Stack>
        );
      })}
      {hidden > 0 && (
        <Box
          {...clickable(() => setAll((v) => !v), { expanded: all })}
          sx={{ ...linkSx, alignSelf: "flex-start" }}
        >
          {all ? "Show only what's open" : `Show all ${list.length}`}
        </Box>
      )}
    </Stack>
  );
}
Checklist.propTypes = {
  list: PropTypes.array.isRequired,
  onJump: PropTypes.func,
};

export default function TraceFindings({ trace, onJump, bordered }) {
  const steps = evidenceStepsOf(trace);
  const list = trace?.checklist || [];
  const hasOutcome = Boolean(trace?.outcome);
  if (!hasOutcome && !steps.length && !list.length) return null;
  const o = OUTCOME[trace.outcome] || OUTCOME.unclear;
  const labels = conversationLabelsOf(trace);

  const rows = [];
  if (hasOutcome)
    rows.push({
      key: "outcome",
      label: "Outcome",
      body: (
        <Typography fontSize="13px" sx={{ lineHeight: "20px" }}>
          <Box component="span" sx={{ color: o.color, fontWeight: 500 }}>
            {o.label}
          </Box>
          {labels.length > 0 && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {` · ${labels.join(" · ")}`}
            </Box>
          )}
        </Typography>
      ),
    });
  steps.forEach((s) =>
    rows.push({
      key: s.key,
      label: s.label,
      body: (
        <>
          <Stack direction="row" alignItems="baseline" gap={1} minWidth={0}>
            <Typography
              fontSize="13px"
              color={s.key === "decisive" ? "status.danger.fg" : "text.primary"}
              sx={{ lineHeight: "20px", flex: 1, minWidth: 0 }}
            >
              {s.step.text}
            </Typography>
            {onJump && (
              <Box
                {...clickable(() => onJump(s.index), { role: "link" })}
                sx={linkSx}
              >
                {`Step ${s.index + 1}`}
              </Box>
            )}
          </Stack>
          <InOut step={s.step} />
        </>
      ),
    }),
  );
  if (list.length)
    rows.push({
      key: "checklist",
      label: "Requirements",
      body: <Checklist list={list} onJump={onJump} />,
    });

  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: "minmax(112px, max-content) minmax(0, 1fr)",
        bgcolor: "background.paper",
        ...(bordered
          ? { border: "1px solid", borderColor: "divider", borderRadius: "8px" }
          : { borderBottom: "1px solid", borderColor: "divider" }),
      }}
    >
      {rows.map((r, i) => (
        <Row key={r.key} label={r.label} last={i === rows.length - 1}>
          {r.body}
        </Row>
      ))}
    </Box>
  );
}

TraceFindings.propTypes = {
  trace: PropTypes.object,
  /* Jump to a step in the reel below; leave out where there is no reel. */
  onJump: PropTypes.func,
  /* Its own panel border (stand-alone) rather than a strip inside a panel. */
  bordered: PropTypes.bool,
};
