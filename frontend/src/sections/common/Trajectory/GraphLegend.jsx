// Graph legend: what the graph can claim, in plain words.
// One run: the quality line (`Basic graph · parent → child only: CrewAI
// spans don't carry node ids we have verified`) + the capture of harness
// runs. Two runs: `● Only in A · ● Only in B · ○ In both · ✕ Failed here ·
// ↻ Retried` in the runs' colours, then each run's quality.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Term from "src/sections/common/ux/Term";
import Truncate from "src/sections/common/ux/Truncate";
import CustomTooltip from "src/components/tooltip";

const WHY_TEXT = {
  parent_child: "Why basic?",
  model_calls: "Why only model calls?",
  unknown: "Why unknown?",
};

const QUALITY_ICON = {
  flow: "●",
  full: "●",
  parent_child: "◐",
  model_calls: "○",
  unknown: "?",
};

function Swatch({ color, dashed, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ whiteSpace: "nowrap" }}
    >
      <Box
        component="span"
        sx={{
          width: 10,
          height: 10,
          borderRadius: "3px",
          border: "2px solid",
          borderStyle: dashed ? "dashed" : "solid",
          borderColor: color ?? "divider",
          opacity: dashed ? 0.7 : 1,
          flexShrink: 0,
        }}
      />
      <Typography typography="s3" color="text.secondary" component="span">
        {children}
      </Typography>
    </Stack>
  );
}
Swatch.propTypes = {
  color: PropTypes.string,
  dashed: PropTypes.bool,
  children: PropTypes.node,
};

/** IT-R5 G4 P1: the node numbers' key, inline in every graph legend. */
export const VISITS_KEY = "×N = visits";
export function VisitsKey() {
  return (
    <Box
      component="span"
      data-testid="graph-visits-key"
      title="A step the run visited more than once shows ×N (its visits); arrows going back up are loops."
      sx={{ flexShrink: 0, whiteSpace: "nowrap", color: "text.disabled" }}
    >
      · {VISITS_KEY}
    </Box>
  );
}

/** IT-R2 G4 P5: what a basic / step-flow graph is, in one plain line. */
export const STEPS_IN_ORDER =
  "Steps in order — this run declares no agent graph.";

/** IT-R3 G4 P4: a harness run's graph is built from its tool calls. */
export const BUILT_FROM_TOOL_CALLS =
  "Built from tool calls (no declared graph)";

/** `Full graph · from your Agent Playground graph` with a Term on the label. */
export function QualityLine({ quality, prefix, capture, sx }) {
  if (!quality) return null;
  // IT-R2 G4 P5: a basic or step-flow graph reads `Steps in order — this
  // run declares no agent graph.` (the provider's reason in the tooltip).
  if (quality.flow || quality.quality === "parent_child")
    return (
      <CustomTooltip
        show
        size="small"
        type="black"
        title={`${quality.detail ?? quality.reason ?? ""}${capture ? ` · ${capture}` : ""}`}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          component="div"
          data-graph-quality={quality.quality}
          data-testid="graph-steps-in-order"
          tabIndex={0}
          sx={{
            display: "flex",
            alignItems: "baseline",
            gap: 0.5,
            minWidth: 0,
            ...sx,
          }}
        >
          {prefix ? (
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary", flexShrink: 0 }}
            >
              {prefix}
            </Box>
          ) : null}
          <Box component="span" aria-hidden sx={{ flexShrink: 0 }}>
            {QUALITY_ICON.parent_child}
          </Box>
          <Truncate
            text={
              quality.flowKind === "harness"
                ? BUILT_FROM_TOOL_CALLS
                : STEPS_IN_ORDER
            }
            sx={{ minWidth: 0 }}
          />
          <VisitsKey />
        </Typography>
      </CustomTooltip>
    );
  return (
    <Typography
      typography="s3"
      color="text.secondary"
      component="div"
      data-graph-quality={quality.quality}
      sx={{
        display: "flex",
        alignItems: "baseline",
        gap: 0.5,
        minWidth: 0,
        ...sx,
      }}
    >
      {prefix ? (
        <Box
          component="span"
          sx={{ fontWeight: 600, color: "text.primary", flexShrink: 0 }}
        >
          {prefix}
        </Box>
      ) : null}
      <Box component="span" aria-hidden sx={{ flexShrink: 0 }}>
        {QUALITY_ICON[quality.flow ? "flow" : quality.quality] ?? ""}
      </Box>
      <Term k="graphQuality">
        <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
          {quality.label}
        </Box>
      </Term>
      {quality.quality === "full" || quality.flow ? (
        <Truncate
          text={`· ${quality.reason}${capture ? ` · ${capture}` : ""}`}
          sx={{ minWidth: 0 }}
        />
      ) : null}
      {quality.quality !== "full" ? (
        // The provider's reason sits behind `Why basic?` (IT-R1 G4).
        <CustomTooltip
          show
          size="small"
          type="black"
          title={`${quality.detail ?? quality.reason}${capture ? ` · ${capture}` : ""}`}
        >
          <Box
            component="span"
            tabIndex={0}
            data-testid="graph-why-basic"
            sx={{
              flexShrink: 0,
              color: "text.secondary",
              textDecoration: "underline dotted",
              cursor: "help",
              whiteSpace: "nowrap",
            }}
          >
            {quality.flow ? "Why?" : WHY_TEXT[quality.quality] ?? "Why?"}
          </Box>
        </CustomTooltip>
      ) : null}
    </Typography>
  );
}
QualityLine.propTypes = {
  quality: PropTypes.object,
  prefix: PropTypes.node,
  capture: PropTypes.string,
  sx: PropTypes.object,
};

/**
 * @param {{quality?: object, capture?: string,
 *   diff?: {labels: {a, b}, colors: {a, b}, failed?: boolean, retried?: boolean}}} props
 */
export default function GraphLegend({
  quality,
  capture,
  diff,
  compareQuality,
  sx,
}) {
  if (!diff) return <QualityLine quality={quality} capture={capture} sx={sx} />;
  return (
    <Stack gap={0.25} sx={{ minWidth: 0, ...sx }} data-testid="graph-legend">
      <Stack
        direction="row"
        alignItems="center"
        gap={1.25}
        flexWrap="wrap"
        rowGap={0.25}
      >
        <Swatch color={diff.colors.a} dashed>
          Only in {diff.labels.a}
        </Swatch>
        <Swatch color={diff.colors.b}>Only in {diff.labels.b}</Swatch>
        <Swatch>In both</Swatch>
        {diff.failed ? (
          <Typography
            typography="s3"
            color="error.main"
            component="span"
            sx={{ whiteSpace: "nowrap" }}
          >
            ✕ Failed here
          </Typography>
        ) : null}
        {diff.retried ? (
          <Typography
            typography="s3"
            color="text.secondary"
            component="span"
            sx={{ whiteSpace: "nowrap" }}
          >
            ↻ Retried
          </Typography>
        ) : null}
        <Typography typography="s3" color="text.secondary" component="span">
          <VisitsKey />
        </Typography>
        <Term k="graphDiff">
          <Typography typography="s3" color="text.disabled" component="span">
            How to read this
          </Typography>
        </Term>
      </Stack>
      {quality && compareQuality ? (
        <Stack direction="row" gap={2} sx={{ minWidth: 0 }}>
          <QualityLine
            quality={quality}
            prefix={diff.labels.a}
            sx={{ flex: 1 }}
          />
          <QualityLine
            quality={compareQuality}
            prefix={diff.labels.b}
            sx={{ flex: 1 }}
          />
        </Stack>
      ) : null}
    </Stack>
  );
}
GraphLegend.propTypes = {
  quality: PropTypes.object,
  compareQuality: PropTypes.object,
  capture: PropTypes.string,
  diff: PropTypes.shape({
    labels: PropTypes.object,
    colors: PropTypes.object,
    failed: PropTypes.bool,
    retried: PropTypes.bool,
  }),
  sx: PropTypes.object,
};
