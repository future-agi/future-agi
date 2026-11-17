import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fmtMs } from "src/sections/common/format/fmt";
import { KIND_WORD } from "./StepCell";

const KIND_COLOR = {
  light: {
    customer: "#9CA3AF",
    reply: "#3B82F6",
    tool: "#0D9488",
    retrieval: "#8B5CF6",
    file_edit: "#A16207",
    command: "#A16207",
    test: "#A16207",
    subagent: "#A16207",
    end: "#475569",
    think: "#CBD5E1",
  },
  dark: {
    customer: "#6B7280",
    reply: "#60A5FA",
    tool: "#2DD4BF",
    retrieval: "#A78BFA",
    file_edit: "#D97706",
    command: "#D97706",
    test: "#D97706",
    subagent: "#D97706",
    end: "#94A3B8",
    think: "#475569",
  },
};
const FW_COLOR = { light: "#DC2626", dark: "#F87171" };
const FD_COLOR = { light: "#7C3AED", dark: "#C4B5FD" };
const LABEL_W = 116;
const KEY_TEXT =
  "One bar per run, one block per aligned step. Blocks in the same column did the same action. Click a block to open that step in every run.";

const legendItemSx = {
  display: "inline-flex",
  alignItems: "center",
  gap: "4px",
  typography: "s3",
  fontSize: 11,
  color: "text.secondary",
  whiteSpace: "nowrap",
};

const markerSx = {
  all: "unset",
  position: "absolute",
  top: 2,
  typography: "s3",
  fontWeight: 700,
  whiteSpace: "nowrap",
  // Stays inside the map: long labels end in … (full text in the tooltip).
  maxWidth: 180,
  overflow: "hidden",
  textOverflow: "ellipsis",
  cursor: "pointer",
  "&:hover": { textDecoration: "underline" },
  "&:focus-visible": { outline: "2px solid", outlineOffset: 1 },
};

const LEGEND = [
  ["customer", "Customer"],
  ["reply", "Reply"],
  ["tool", "Tool call"],
  ["retrieval", "Search"],
  ["file_edit", "Code change"],
  ["think", "Thinking"],
  ["end", "End"],
];

const cellMs = (c) =>
  !c
    ? 0
    : c.type === "gap"
      ? (c.steps ?? []).reduce((a, s) => a + (s.ms ?? 0), 0)
      : (c.step?.ms ?? 0) +
        (c.subSteps ?? []).reduce((a, s) => a + (s.ms ?? 0), 0);

/** The map's key (legend): one line of text + the swatches (Map `Key`). */
export function RibbonKey() {
  const theme = useTheme();
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  return (
    <Box sx={{ maxWidth: 320 }}>
      <Typography typography="s2" sx={{ mb: 0.75 }}>
        {KEY_TEXT}
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          gap: "6px 12px",
          "& > span": legendItemSx,
        }}
      >
        <>
          {LEGEND.map(([k, label]) => (
            <Box component="span" key={k}>
              <Box
                component="i"
                sx={{
                  width: 10,
                  height: 8,
                  borderRadius: "2px",
                  bgcolor: KIND_COLOR[mode][k],
                }}
              />
              {label}
            </Box>
          ))}
          <Box component="span">
            <Box
              component="i"
              sx={{
                width: 10,
                height: 8,
                borderRadius: "2px",
                background: `repeating-linear-gradient(135deg, ${KIND_COLOR[mode].tool} 0 2px, transparent 2px 5px)`,
              }}
            />
            only this run
          </Box>
          <Box component="span">
            <Box
              component="i"
              sx={{
                width: 10,
                height: 8,
                borderRadius: "2px",
                border: "1px dashed",
                borderColor: "divider",
              }}
            />
            missing
          </Box>
          <Box component="span">
            <Box
              component="i"
              sx={{
                width: 10,
                height: 8,
                borderRadius: "2px",
                border: "2px solid",
                borderColor: FW_COLOR[mode],
              }}
            />
            tool error
          </Box>
          <Box component="span">✕ failed check</Box>
        </>

        <Box component="span">= same · ~ changed · + only here · − missing</Box>
      </Box>
    </Box>
  );
}

/**
 * The divergence ribbon: one 10 px row per lane, a segment per aligned row
 * (colour = step kind, hatched = folded gap, red outline = tool error, ✕ =
 * failed check), with `▲ First wrong step` and `│ First difference`.
 * Click a segment = scroll every lane to it and open it.
 */
export default function DivergenceRibbon({
  rows,
  lanes,
  firstWrong,
  firstDifference,
  width,
  onSelect,
  onFirstWrong,
  onFirstDifference,
  anchor,
  names,
  lead,
  trail,
  firstWrongText,
  firstDiffText,
}) {
  const theme = useTheme();
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  const flex = useMemo(() => {
    if (width !== "time") return rows.map(() => 1);
    const ms = rows.map((r) => Math.max(...r.cells.map(cellMs)));
    const max = Math.max(1, ...ms);
    return ms.map((m) => Math.max(0.35, (m / max) * 6));
  }, [rows, width]);
  const failedRow = (l, r) => {
    const keys = (lanes[l].checks ?? [])
      .filter((c) => c.passed === false && Number.isFinite(c.cause_step_n))
      .map((c) => c.cause_step_n);
    const c = r.cells[l];
    if (!c) return false;
    const ns =
      c.type === "gap"
        ? c.steps.map((s) => s.n)
        : [c.step?.n, ...(c.subSteps ?? []).map((s) => s.n)];
    return keys.some((k) => ns.includes(k));
  };
  const fwIdx = rows.findIndex((r) => r.id === firstWrong?.rowId);
  const fdIdx = rows.findIndex((r) => r.id === firstDifference?.rowId);
  const pos = (i) => {
    const total = flex.reduce((a, b) => a + b, 0);
    const before = flex.slice(0, i).reduce((a, b) => a + b, 0);
    return ((before + flex[i] / 2) / total) * 100;
  };
  if (!rows.length) return <Box sx={{ height: 24 }} />;
  return (
    <Box
      sx={{
        px: 2,
        pt: 0.5,
        pb: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
      role="region"
      aria-label="Map"
    >
      <Stack
        direction="row"
        sx={{ position: "relative", height: 20, ml: `${LABEL_W}px` }}
      >
        {lead ? (
          <Box
            sx={{
              position: "absolute",
              left: -LABEL_W,
              top: 0,
              height: 20,
              display: "flex",
              alignItems: "center",
            }}
          >
            {lead}
          </Box>
        ) : null}
        {trail ? (
          <Box
            sx={{
              position: "absolute",
              right: 0,
              top: 0,
              height: 20,
              display: "flex",
              alignItems: "center",
              zIndex: 1,
            }}
          >
            {trail}
          </Box>
        ) : null}
        {fwIdx >= 0 ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title={`${firstWrongText ?? "The first step that caused a failing check"} · click to open it`}
          >
            <Box
              component="button"
              type="button"
              onClick={onFirstWrong}
              data-marker="first-wrong"
              sx={{
                ...markerSx,
                left: `${pos(fwIdx)}%`,
                // Near the right edge the label sits left of its marker.
                transform:
                  pos(fwIdx) > 70
                    ? "translateX(calc(-100% + 6px))"
                    : "translateX(-6px)",
                color: FW_COLOR[mode],
              }}
            >
              {pos(fwIdx) > 70 ? "First wrong step ▲" : "▲ First wrong step"}
            </Box>
          </CustomTooltip>
        ) : null}
        {fdIdx >= 0 ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title={`${firstDiffText ?? "The first action that differs from the baseline"} · click to open it`}
          >
            <Box
              component="button"
              type="button"
              onClick={onFirstDifference}
              data-marker="first-difference"
              sx={{
                ...markerSx,
                left: `${pos(fdIdx)}%`,
                transform:
                  (fwIdx >= 0 && Math.abs(pos(fdIdx) - pos(fwIdx)) < 14) ||
                  pos(fdIdx) > 70
                    ? "translate(-100%, 0) translateX(-12px)"
                    : "translateX(-2px)",
                color: FD_COLOR[mode],
              }}
            >
              │ First difference
            </Box>
          </CustomTooltip>
        ) : null}
      </Stack>
      <Stack spacing="3px" sx={{ position: "relative" }}>
        {lanes.map((lane, l) => (
          <Stack
            key={lane.id}
            direction="row"
            alignItems="center"
            sx={{ height: 12 }}
          >
            <Typography
              typography="s3"
              sx={{
                width: LABEL_W,
                flexShrink: 0,
                fontSize: 11,
                fontWeight: 700,
                color: lane.color?.solid,
                lineHeight: "12px",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                pr: 1,
              }}
            >
              {names?.get(lane.id) ??
                lane.blindName ??
                (lane.kind === "production" ? "Prod" : lane.label)}
            </Typography>
            <Stack direction="row" sx={{ flex: 1, gap: "2px", height: 10 }}>
              {rows.map((r, i) => {
                const c = r.cells[l];
                const step =
                  c?.type === "step"
                    ? c.step
                    : c?.type === "gap"
                      ? c.steps[0]
                      : null;
                const err =
                  c?.type === "step"
                    ? Boolean(c.step?.error)
                    : (c?.steps ?? []).some((s) => s.error);
                const failed = failedRow(l, r);
                const gap = c?.type === "gap";
                const color = step
                  ? KIND_COLOR[mode][step.kind] ?? KIND_COLOR[mode].think
                  : "transparent";
                const tip = !c
                  ? `${lane.blindName ?? lane.label} · nothing here`
                  : gap
                    ? `${lane.blindName ?? lane.label} · ${c.count} folded ${c.count === 1 ? "step" : "steps"} (${c.summary})`
                    : `${lane.blindName ?? lane.label} · step ${step.n} · ${step.name}${step.kind === "end" ? "" : ` · ${KIND_WORD[step.kind] ?? step.kind}`} · ${fmtMs(step.ms ?? 0)}${err ? " · error" : ""}${failed ? " · failed check" : ""}`;
                return (
                  <CustomTooltip
                    describeChild
                    key={r.id}
                    show
                    size="small"
                    title={tip}
                  >
                    <Box
                      component="button"
                      type="button"
                      aria-label={tip}
                      onClick={() => onSelect(r)}
                      sx={{
                        all: "unset",
                        flex: flex[i],
                        minWidth: 4,
                        height: 10,
                        borderRadius: "2px",
                        cursor: "pointer",
                        position: "relative",
                        boxSizing: "border-box",
                        bgcolor: gap ? "transparent" : color,
                        background: gap
                          ? `repeating-linear-gradient(135deg, ${color} 0 2px, transparent 2px 5px)`
                          : undefined,
                        border: !c ? "1px dashed" : err ? "2px solid" : "none",
                        borderColor: !c
                          ? "divider"
                          : err
                            ? FW_COLOR[mode]
                            : undefined,
                        outline: anchor === r.id ? "2px solid" : "none",
                        outlineColor: theme.palette.text.primary,
                        outlineOffset: 1,
                        "&:focus-visible": {
                          outline: "2px solid",
                          outlineColor: theme.palette.primary.main,
                        },
                      }}
                    >
                      {failed ? (
                        <Box
                          component="span"
                          sx={{
                            position: "absolute",
                            inset: 0,
                            display: "grid",
                            placeItems: "center",
                            fontSize: 9,
                            lineHeight: 1,
                            fontWeight: 800,
                            color: "#fff",
                            textShadow: "0 0 2px rgba(0,0,0,0.6)",
                          }}
                        >
                          ✕
                        </Box>
                      ) : null}
                    </Box>
                  </CustomTooltip>
                );
              })}
            </Stack>
          </Stack>
        ))}
        {fdIdx >= 0 ? (
          <Box
            sx={{
              position: "absolute",
              top: -2,
              bottom: -2,
              left: `calc(${LABEL_W}px + (100% - ${LABEL_W}px) * ${pos(fdIdx) / 100})`,
              width: "2px",
              bgcolor: FD_COLOR[mode],
              pointerEvents: "none",
            }}
          />
        ) : null}
      </Stack>
    </Box>
  );
}

DivergenceRibbon.propTypes = {
  rows: PropTypes.array.isRequired,
  lanes: PropTypes.array.isRequired,
  firstWrong: PropTypes.object,
  firstDifference: PropTypes.object,
  width: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onFirstWrong: PropTypes.func,
  onFirstDifference: PropTypes.func,
  anchor: PropTypes.string,
  names: PropTypes.instanceOf(Map),
  lead: PropTypes.node,
  trail: PropTypes.node,
  firstWrongText: PropTypes.string,
  firstDiffText: PropTypes.string,
};
