import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Term from "./Term";
import { sortVerdicts } from "./verdictModel";

export const VERDICT_TONE_STYLE = Object.freeze({
  better: {
    color: "green.600",
    icon: "mdi:check-circle",
    label: "Better than the baseline",
  },
  mixed: {
    color: "amber.500",
    icon: "mdi:scale-balance",
    label: "Mixed: some tasks got better and some worse, or a costly win",
  },
  tie: {
    color: "text.disabled",
    icon: "mdi:approximately-equal",
    // IT-R5 G2 P5 (rule 37): the plain words, neutral grey.
    label: "Too close to call: the difference could be chance",
  },
  worse: {
    color: "red.600",
    icon: "mdi:alert-circle",
    label: "Worse than the baseline",
  },
});

/** The tone glyph: colour + icon + accessible name (never colour alone). */
export function VerdictToneDot({ tone, size = 18 }) {
  const s = VERDICT_TONE_STYLE[tone] ?? VERDICT_TONE_STYLE.tie;
  return (
    <CustomTooltip show size="small" title={s.label} placement="top">
      <Box
        component="span"
        role="img"
        aria-label={s.label}
        sx={{
          display: "inline-flex",
          color: s.color,
          flexShrink: 0,
          mt: "1px",
        }}
      >
        <Iconify icon={s.icon} width={size} />
      </Box>
    </CustomTooltip>
  );
}
VerdictToneDot.propTypes = { tone: PropTypes.string, size: PropTypes.number };

/**
 * The verdict's sentence alone (run names bold, Terms wrapped). IT-R5 G2
 * P5: `secondary` is the quieter second line (s2, grey, wraps to 2 lines).
 */
export function VerdictText({ verdict, compact, secondary = false, sx }) {
  return (
    <Typography
      component="p"
      typography={compact || secondary ? "s2" : "s1"}
      color={secondary ? "text.secondary" : "text.primary"}
      sx={{
        m: 0,
        minWidth: 0,
        display: "-webkit-box",
        WebkitBoxOrient: "vertical",
        WebkitLineClamp: compact ? 1 : 2,
        overflow: "hidden",
        ...sx,
      }}
    >
      {verdict.parts.map((p, i) => {
        if (p.t === "run")
          return (
            <Box
              key={i}
              component="span"
              sx={{ fontWeight: "fontWeightSemiBold" }}
            >
              {p.text}
            </Box>
          );
        if (p.t === "term")
          return (
            <Term key={i} k={p.k}>
              {p.text}
            </Term>
          );
        return <React.Fragment key={i}>{p.text}</React.Fragment>;
      })}
    </Typography>
  );
}
VerdictText.propTypes = {
  verdict: PropTypes.object,
  compact: PropTypes.bool,
  secondary: PropTypes.bool,
  sx: PropTypes.object,
};

const DARK_LINK_SX = {
  color: (t) =>
    t.palette.mode === "dark"
      ? t.palette.accent?.brand ?? t.palette.primary.light
      : t.palette.primary.main,
};

function VerdictAction({ action }) {
  const common = {
    size: "small",
    variant: "text",
    color: "primary",
    sx: {
      typography: "s2",
      px: 0.75,
      minWidth: 0,
      whiteSpace: "nowrap",
      // Dark primary is monochrome (brand guide), so a text action would
      // read as plain text: links take the dark brand accent instead.
      ...DARK_LINK_SX,
    },
    endIcon: action.icon ? <Iconify icon={action.icon} width={14} /> : null,
  };
  const button = action.href ? (
    <Button {...common} component={RouterLink} to={action.href}>
      {action.label}
    </Button>
  ) : (
    <Button {...common} onClick={action.onClick} disabled={action.disabled}>
      {action.label}
    </Button>
  );
  return action.tooltip ? (
    <CustomTooltip show describeChild size="small" title={action.tooltip}>
      <span>{button}</span>
    </CustomTooltip>
  ) : (
    button
  );
}
VerdictAction.propTypes = { action: PropTypes.object };

/**
 * VerdictLine — the answer-first sentence (R3 G0 §B2): tone dot, one `s1`
 * sentence (max 2 lines; `compact` 1 line, `s2`) with run names bold and
 * `pts` / `Confidence` wrapped in Term, then up to 2 actions.
 *
 * Props (frozen):
 * @param {object} props.verdict  verdictOf() result
 * @param {{label: string, onClick?: Function, href?: string, icon?: string,
 *   tooltip?: string, disabled?: boolean}[]} [props.actions]  ≤ 2 shown
 *   (`See the 7 tasks that got worse`, `Why?`)
 * @param {boolean} [props.compact]
 * @param {object} [props.sx]
 */
export default function VerdictLine({
  verdict,
  actions = [],
  compact = false,
  sx,
}) {
  if (!verdict) return null;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={1}
      data-testid="verdict-line"
      data-tone={verdict.tone}
      sx={{ minWidth: 0, ...sx }}
    >
      <VerdictToneDot tone={verdict.tone} size={compact ? 16 : 18} />
      <Stack
        direction={compact ? "row" : "column"}
        gap={compact ? 1 : 0.25}
        sx={{ minWidth: 0, flex: 1 }}
        alignItems={compact ? "center" : "flex-start"}
      >
        <VerdictText verdict={verdict} compact={compact} />
        {actions.length > 0 && (
          <Stack
            direction="row"
            gap={0.5}
            sx={{ ml: compact ? 0 : -0.75, flexShrink: 0 }}
          >
            {actions.slice(0, 2).map((a) => (
              <VerdictAction key={a.label} action={a} />
            ))}
          </Stack>
        )}
      </Stack>
    </Stack>
  );
}

VerdictLine.propTypes = {
  verdict: PropTypes.object,
  actions: PropTypes.arrayOf(PropTypes.object),
  compact: PropTypes.bool,
  sx: PropTypes.object,
};

/**
 * VerdictList — several candidates vs one baseline, best first. Shows
 * `max` lines and `+N more` that expands in place (`Show less` folds).
 *
 * Props (frozen):
 * @param {({verdict: object, actions?: object[], key?: string}|object)[]} props.items
 *   verdictOf() results, or `{verdict, actions, key}`
 * @param {number} [props.max=3]
 * @param {boolean} [props.compact]
 */
export function VerdictList({ items = [], max = 3, compact = false, sx }) {
  const [open, setOpen] = useState(false);
  const sorted = sortVerdicts(items);
  const shown = open ? sorted : sorted.slice(0, max);
  const hidden = sorted.length - shown.length;
  return (
    <Stack gap={0.75} sx={sx} data-testid="verdict-list">
      {shown.map((item, i) => {
        const verdict = item?.verdict ?? item;
        return (
          <VerdictLine
            key={item?.key ?? verdict?.sentence ?? i}
            verdict={verdict}
            actions={item?.actions ?? []}
            compact={compact}
          />
        );
      })}
      {(hidden > 0 || open) && sorted.length > max && (
        <Box>
          <Button
            size="small"
            variant="text"
            color="inherit"
            onClick={() => setOpen((o) => !o)}
            sx={{
              typography: "s2",
              color: "text.secondary",
              px: 0.75,
              ml: 2.5,
            }}
          >
            {open ? "Show less" : `+${hidden} more`}
          </Button>
        </Box>
      )}
    </Stack>
  );
}

VerdictList.propTypes = {
  items: PropTypes.array,
  max: PropTypes.number,
  compact: PropTypes.bool,
  sx: PropTypes.object,
};
