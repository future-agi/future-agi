import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, TextField, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import TraceChip from "src/sections/common/Trace/TraceChip";
import Term from "src/sections/common/ux/Term";
import { VOCAB } from "src/sections/common/vocab";
import {
  checksSummary,
  clipText,
  conversationOfCells,
  openingMessage,
  conversationSummary,
  isEmptyValue,
  startStateSummary,
} from "./mappingModel";

/** Amber `Draft` chip (Falcon drafted it; review before running). */
export const DraftChip = () => {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  return (
    <CustomTooltip
      show
      title="Drafted automatically from the session · review before running"
      size="small"
      arrow
    >
      <Chip
        label="Draft"
        size="small"
        sx={{
          height: 18,
          typography: "s3",
          fontWeight: 500,
          bgcolor: alpha(theme.palette.warning.main, 0.12),
          color: dark ? "warning.light" : "warning.dark",
          "& .MuiChip-label": { px: 0.75 },
        }}
      />
    </CustomTooltip>
  );
};

/** Grey kind badge: `Task` / `Conversation` / `Trace` / `Row`. */
export const KindBadge = ({ kind }) => {
  const label =
    kind === "task"
      ? VOCAB.taskBadge
      : kind === "conversation"
        ? VOCAB.conversationBadge
        : kind === "trace"
          ? "Trace"
          : "Row";
  return (
    <Chip
      label={label}
      size="small"
      sx={{
        height: 18,
        typography: "s3",
        fontWeight: 500,
        bgcolor: "action.hover",
        color: "text.secondary",
        "& .MuiChip-label": { px: 0.75 },
      }}
    />
  );
};
KindBadge.propTypes = { kind: PropTypes.string };

const DOT_COLOR = { pass: "green.600", fail: "red.600", none: "divider" };

/** One dot per turn, coloured by the per-turn eval (`●●○●●○`). */
export const TurnDots = ({ dots }) => (
  <Stack direction="row" spacing={0.375} alignItems="center" aria-hidden>
    {dots.map((d, i) => (
      <Box
        key={i}
        sx={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          bgcolor: DOT_COLOR[d] ?? "divider",
        }}
      />
    ))}
  </Stack>
);
TurnDots.propTypes = { dots: PropTypes.arrayOf(PropTypes.string) };

const Line = ({ label, labelWidth = 64, children }) => (
  <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
    <Typography
      typography="s3"
      color="text.disabled"
      noWrap
      title={label}
      sx={{ width: labelWidth, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Box
      sx={{
        minWidth: 0,
        flex: 1,
        display: "flex",
        alignItems: "center",
        gap: 0.75,
      }}
    >
      {children}
    </Box>
  </Stack>
);
Line.propTypes = {
  label: PropTypes.node,
  labelWidth: PropTypes.number,
  children: PropTypes.node,
};

const Clip = ({ value, n = 80, mono }) => {
  const text = clipText(value, n);
  const full = clipText(value, 2000);
  return (
    <CustomTooltip
      show={full.length > text.length}
      title={full}
      size="small"
      arrow
    >
      <Typography
        typography="s2"
        color={text ? "text.primary" : "text.disabled"}
        noWrap
        sx={{ minWidth: 0, ...(mono ? { fontFamily: "monospace" } : {}) }}
      >
        {text || "empty"}
      </Typography>
    </CustomTooltip>
  );
};
Clip.propTypes = {
  value: PropTypes.any,
  n: PropTypes.number,
  mono: PropTypes.bool,
};

/** The conversation line: `6 turns · 3 tool calls · ends: "…"` + dots. */
export const ConversationLine = ({ conversation }) => {
  const s = conversationSummary(conversation);
  const turns = conversation?.turns ?? [];
  const first = turns.find((t) => t.role === "customer")?.content;
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={
        first
          ? `First: ${clipText(first, 120)}${s.ends ? ` · Last: ${s.ends}` : ""}`
          : s.text
      }
    >
      <Stack spacing={0.5} sx={{ minWidth: 0 }}>
        <Typography typography="s2" color="text.primary" noWrap>
          {s.text}
        </Typography>
        <TurnDots dots={s.dots} />
      </Stack>
    </CustomTooltip>
  );
};
ConversationLine.propTypes = { conversation: PropTypes.object };

const EDITABLE = (v) => typeof v === "string";

/**
 * How one imported item will look in the grid (R2-SPEC §7.3.6), compact
 * and read-only: a task (goal, start state, checks), a conversation
 * (turns, tool calls, dots), a frozen trace (TraceChip) or a row
 * (first cells). With `editing`, string cells become text fields.
 */
const ImportItemPreview = ({
  item,
  displayKind,
  traceLabel,
  traceState = "ok",
  edits,
  editing,
  onEditCell,
}) => {
  const cells = { ...(item?.cells ?? {}), ...(edits ?? {}) };
  const kind = displayKind ?? item?.kind ?? "row";

  if (editing) {
    const editable = Object.entries(cells).filter(([, v]) => EDITABLE(v));
    if (!editable.length)
      return (
        <Typography typography="s3" color="text.disabled">
          Nothing to edit here: these cells hold structured values.
        </Typography>
      );
    return (
      <Stack spacing={1}>
        {editable.map(([name, value]) => (
          <TextField
            key={name}
            label={name}
            size="small"
            fullWidth
            multiline
            maxRows={4}
            value={value}
            onChange={(e) => onEditCell?.(name, e.target.value)}
            inputProps={{ "aria-label": `Edit ${name}` }}
          />
        ))}
      </Stack>
    );
  }

  if (kind === "trace")
    return (
      <Stack direction="row" spacing={1} alignItems="center">
        <TraceChip
          state={traceLabel ? traceState : "running"}
          label={traceLabel ?? "Reading trace…"}
          tooltip={
            traceLabel
              ? `${traceLabel} · frozen at import`
              : "Reading the trace tree"
          }
        />
      </Stack>
    );

  if (kind === "task") {
    const checks = checksSummary(cells.checks);
    return (
      <Stack spacing={0.5}>
        <Typography typography="s1" fontWeight={500} noWrap>
          {clipText(cells.title, 80) || "Untitled task"}
        </Typography>
        <Clip value={cells.instruction} n={90} />
        <Line label="Goal">
          <Clip value={cells.customer_goal} n={70} />
          {!isEmptyValue(cells.customer_goal) && <DraftChip />}
        </Line>
        <Line label={<Term k="startState">Start state</Term>}>
          <Typography typography="s2" color="text.primary" noWrap>
            {startStateSummary(cells.start_state) || "empty"}
          </Typography>
        </Line>
        <Line label={<Term k="rowChecks">Checks</Term>}>
          <Typography typography="s2" color="text.primary" noWrap>
            {checks.text}
          </Typography>
          {checks.count > 0 && (
            <Chip
              label={VOCAB.checkChip}
              size="small"
              sx={{
                height: 18,
                typography: "s3",
                bgcolor: "action.hover",
                color: "text.secondary",
                fontWeight: 500,
                "& .MuiChip-label": { px: 0.75 },
              }}
            />
          )}
          {checks.draft && <DraftChip />}
        </Line>
      </Stack>
    );
  }

  const conv = conversationOfCells(cells);
  if (kind === "conversation" || conv) {
    return (
      <Stack spacing={0.5}>
        <Typography typography="s1" fontWeight={500} noWrap>
          {clipText(cells.title, 80) ||
            clipText(openingMessage(conv), 80) ||
            "Untitled conversation"}
        </Typography>
        {conv ? (
          <ConversationLine conversation={conv} />
        ) : (
          <Typography typography="s3" color="text.disabled">
            No conversation recorded
          </Typography>
        )}
        {cells.turn != null && (
          <Line label="Turn">
            <Typography typography="s2">{cells.turn}</Typography>
          </Line>
        )}
        {cells.failing_turn != null && (
          <Line label="Failing turn">
            <Typography typography="s2">{cells.failing_turn}</Typography>
          </Line>
        )}
      </Stack>
    );
  }

  const entries = Object.entries(cells).slice(0, 3);
  return (
    <Stack spacing={0.5}>
      {entries.map(([name, value]) => (
        <Line key={name} label={name} labelWidth={112}>
          {isEmptyValue(value) ? (
            <Typography typography="s2" color="text.disabled">
              {/^expected/i.test(name) && item?.needs_review
                ? "empty · needs review"
                : "empty"}
            </Typography>
          ) : (
            <Clip value={value} n={70} />
          )}
        </Line>
      ))}
    </Stack>
  );
};

ImportItemPreview.propTypes = {
  item: PropTypes.object,
  displayKind: PropTypes.string,
  traceLabel: PropTypes.string,
  traceState: PropTypes.string,
  edits: PropTypes.object,
  editing: PropTypes.bool,
  onEditCell: PropTypes.func,
};

export default ImportItemPreview;
