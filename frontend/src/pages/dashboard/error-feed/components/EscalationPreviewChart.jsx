import React from "react";
import PropTypes from "prop-types";
import { Box, Typography, useTheme } from "@mui/material";
import {
  SAMPLE,
  SAMPLE_COUNTS,
  entryLevel,
  escalateAt,
  recoverAt,
  recoveryLevel,
  simulate,
} from "./escalationSettings";

const W = 520;
const H = 158;
const PLOT_TOP = 4;
const LANE = 20;
const PLOT_BOTTOM = 128;
const GUTTER = 104;
const PW = W - GUTTER;
// Approximate 12px Inter width, enough to decide whether a label fits.
const textWidth = (text) => text.length * 6.6;

// Keep labels in value order, at least `gap` apart, so leaders never cross.
function stackLabels(items, gap, lo, hi) {
  const sorted = [...items].sort((a, b) => a.y - b.y);
  sorted.forEach((item, i) => {
    item.ly = Math.max(item.y, lo, i ? sorted[i - 1].ly + gap : lo);
  });
  for (let i = sorted.length - 1; i >= 0; i--) {
    sorted[i].ly = Math.min(
      sorted[i].ly,
      i < sorted.length - 1 ? sorted[i + 1].ly - gap : hi,
    );
  }
  return sorted;
}

export function previewCaption(values, usual) {
  const spans = simulate(values, usual);
  const entry = escalateAt(values, usual).toLocaleString("en-US");
  const recovery = recoverAt(values, usual).toLocaleString("en-US");
  if (!spans.length)
    return `This spike stays under ${entry}, so it never escalates.`;
  const head = `Escalates after ${values.entry_confirmation_minutes} min at ${entry}+`;
  if (spans[0].end == null)
    return `${head}, and is still escalating at the end.`;
  return `${head}, recovers after ${values.recovery_confirmation_minutes} min at ${recovery} or fewer.`;
}

export default function EscalationPreviewChart({ values, usual, stale }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const colors = {
    curve: theme.palette.text.secondary,
    hot: theme.palette.error.main,
    escText: theme.palette.status.danger.fg,
    shade: isDark ? "rgba(219,47,45,0.13)" : "rgba(219,47,45,0.07)",
    muted: theme.palette.text.secondary,
    text: theme.palette.text.primary,
    recLine: theme.palette.text.tertiary,
  };
  const scale = usual / SAMPLE.base;
  const floor = 0.7 * usual;
  const entry = entryLevel(values, usual);
  const recovery = recoveryLevel(values, usual);
  // Fixed log scale relative to usual so the curve keeps its place between
  // presets; the top only grows when the Escalate line would leave the chart.
  const ymax = Math.max(7.8 * usual, entry * 1.15);
  const X = (m) =>
    ((Math.max(SAMPLE.from, Math.min(SAMPLE.to, m)) - SAMPLE.from) /
      (SAMPLE.to - SAMPLE.from)) *
    PW;
  const Y = (v) =>
    PLOT_BOTTOM -
    (Math.log(Math.max(v, floor) / floor) / Math.log(ymax / floor)) *
      (PLOT_BOTTOM - PLOT_TOP - LANE);

  let curve = "";
  for (let m = SAMPLE.from; m <= SAMPLE.to; m++)
    curve += `${m === SAMPLE.from ? "M" : "L"}${X(m).toFixed(1)} ${Y(
      SAMPLE_COUNTS[m] * scale,
    ).toFixed(1)}`;

  const spans = simulate(values, usual);
  const bands = spans.map((span) => ({
    start: span.start,
    end: span.end ?? SAMPLE.to,
  }));
  const first = spans[0];
  const yU = Y(usual);
  const yE = Y(entry);
  const yR = Y(recovery);
  const labels = stackLabels(
    [
      {
        y: yE,
        name: "Escalate",
        color: colors.escText,
        value: escalateAt(values, usual),
      },
      {
        y: yR,
        name: "Recover",
        color: colors.muted,
        value: recoverAt(values, usual),
      },
      { y: yU, name: "Usual", color: colors.muted, value: usual },
    ],
    20,
    8,
    PLOT_BOTTOM,
  );
  const holds = first
    ? [
        {
          from: first.holdFrom,
          to: first.start,
          min: values.entry_confirmation_minutes,
        },
        ...(first.end != null
          ? [
              {
                from: first.recoverFrom,
                to: first.end,
                min: values.recovery_confirmation_minutes,
              },
            ]
          : []),
      ]
    : [];
  const caption = previewCaption(values, usual);
  const bandRect = (band) => ({
    x: X(band.start),
    y: PLOT_TOP,
    width: Math.max(0, X(band.end) - X(band.start)),
    height: PLOT_BOTTOM - PLOT_TOP,
  });
  const textProps = {
    fontFamily: theme.typography.fontFamily,
    fontSize: 12,
    dominantBaseline: "central",
    style: { fontVariantNumeric: "tabular-nums" },
  };

  return (
    <Box sx={{ opacity: stale ? 0.4 : 1, transition: "opacity 0.2s" }}>
      <Box
        component="svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={caption}
        sx={{
          display: "block",
          width: "100%",
          height: "auto",
          overflow: "visible",
        }}
      >
        <defs>
          <clipPath id="escalation-preview-plot">
            <rect x="0" y="0" width={PW} height={H} />
          </clipPath>
          <clipPath id="escalation-preview-hot">
            {bands.map((band) => (
              <rect key={band.start} {...bandRect(band)} />
            ))}
          </clipPath>
        </defs>
        {bands.map((band) => (
          <React.Fragment key={band.start}>
            <rect {...bandRect(band)} fill={colors.shade} />
            {X(band.end) - X(band.start) > textWidth("Escalating") + 12 && (
              <text
                {...textProps}
                x={X(band.start) + 6}
                y={PLOT_TOP + 10}
                fill={colors.escText}
                opacity={0.85}
              >
                Escalating
              </text>
            )}
          </React.Fragment>
        ))}
        <line
          x1="0"
          x2={PW}
          y1={yU}
          y2={yU}
          stroke={colors.muted}
          strokeWidth="1.25"
          strokeDasharray="1 3"
        />
        <line
          x1="0"
          x2={PW}
          y1={yR}
          y2={yR}
          stroke={colors.recLine}
          strokeWidth="1.25"
          strokeDasharray="4 3"
        />
        <line
          x1="0"
          x2={PW}
          y1={yE}
          y2={yE}
          stroke={colors.hot}
          strokeWidth="1.25"
          strokeDasharray="4 3"
        />
        <path
          d={curve}
          fill="none"
          stroke={colors.curve}
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          clipPath="url(#escalation-preview-plot)"
        />
        {bands.length > 0 && (
          <path
            d={curve}
            fill="none"
            stroke={colors.hot}
            strokeWidth="1.5"
            strokeLinejoin="round"
            strokeLinecap="round"
            clipPath="url(#escalation-preview-hot)"
          />
        )}
        {(bands.length ? X(bands[0].start) : PW) >
          textWidth("For review") + 12 && (
          <text {...textProps} x="0" y={PLOT_TOP + 10} fill={colors.muted}>
            For review
          </text>
        )}
        {labels.map((label) => (
          <React.Fragment key={label.name}>
            {Math.abs(label.ly - label.y) > 4 && (
              <path
                d={`M${PW + 4} ${label.y}H${PW + 7}V${label.ly}H${PW + 10}`}
                fill="none"
                stroke={colors.muted}
                opacity={0.6}
              />
            )}
            <text
              {...textProps}
              x={PW + 14}
              y={label.ly}
              fill={label.color}
              fontWeight={label.name === "Usual" ? 400 : 500}
            >
              {label.name}
            </text>
            <text
              {...textProps}
              x={W}
              y={label.ly}
              textAnchor="end"
              fill={colors.text}
              fontWeight={600}
            >
              {label.value.toLocaleString("en-US")}
            </text>
          </React.Fragment>
        ))}
        {holds.map((hold) => {
          const x0 = X(hold.from);
          const x1 = X(hold.to);
          const yb = PLOT_BOTTOM + 8;
          return (
            <React.Fragment key={hold.from}>
              <path
                d={`M${x0} ${yb - 3}V${yb + 3}M${x0} ${yb}H${x1}M${x1} ${yb - 3}V${yb + 3}`}
                fill="none"
                stroke={colors.muted}
              />
              <text
                {...textProps}
                x={(x0 + x1) / 2}
                y={yb + 14}
                textAnchor="middle"
                fill={colors.muted}
              >
                {hold.min} min
              </text>
            </React.Fragment>
          );
        })}
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
        component="p"
        sx={{ mt: 0.75 }}
      >
        Same issue as above. {caption}
      </Typography>
    </Box>
  );
}

EscalationPreviewChart.propTypes = {
  values: PropTypes.object.isRequired,
  usual: PropTypes.number.isRequired,
  stale: PropTypes.bool,
};
