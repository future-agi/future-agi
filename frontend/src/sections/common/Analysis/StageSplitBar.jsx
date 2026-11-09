// R4 lane X (MUST 6): where a voice turn's time goes — STT · LLM · TTS p50
// as one stacked bar in tints of the run's colour; values in the tooltip.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import { fmtLatency } from "./voiceSummaryModel";

const STAGES = [
  ["stt", "STT", 0.9],
  ["llm", "LLM", 0.6],
  ["tts", "TTS", 0.3],
];

/**
 * @param {{stages: {stt, llm, tts}, color: string, scaleMs: number, label?: string}} props
 *   `scaleMs` = the longest total among the runs drawn together (shared axis)
 */
export default function StageSplitBar({ stages, color, scaleMs, label }) {
  if (!stages) return null;
  const total = STAGES.reduce((s, [k]) => s + (stages[k] ?? 0), 0);
  const tip = STAGES.map(
    ([k, name]) => `${name} ${fmtLatency(stages[k])}`,
  ).join(" · ");
  return (
    <CustomTooltip
      show
      size="small"
      title={`${label ? `${label} · ` : ""}${tip} (p50 of every agent turn)`}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
        <Box
          role="img"
          aria-label={`${label ?? "Run"}: ${tip}`}
          data-testid="stage-split-bar"
          sx={{
            display: "flex",
            height: 14,
            width: `${Math.max(8, Math.round((total / (scaleMs || total || 1)) * 100))}%`,
            minWidth: 40,
            maxWidth: 360,
            borderRadius: 0.5,
            overflow: "hidden",
          }}
        >
          {STAGES.map(([k, name, a]) => (
            <Box
              key={k}
              sx={{
                flex: `${stages[k] ?? 0} 0 0`,
                bgcolor: alpha(color, a),
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                overflow: "hidden",
              }}
            >
              <Typography
                typography="s3"
                sx={{
                  color: a > 0.5 ? "common.white" : "text.primary",
                  fontSize: 10,
                  lineHeight: 1,
                }}
                noWrap
              >
                {name}
              </Typography>
            </Box>
          ))}
        </Box>
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {fmtLatency(total)}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

StageSplitBar.propTypes = {
  stages: PropTypes.object,
  color: PropTypes.string,
  scaleMs: PropTypes.number,
  label: PropTypes.string,
};
