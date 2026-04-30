import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { fmtMs } from "src/utils/utils";

const PIPELINE_STAGES = [
  { key: "endpointingLatencyAverage", label: "Endpointing" },
  { key: "transcriberLatencyAverage", label: "Transcriber" },
  { key: "modelLatencyAverage", label: "LLM" },
  { key: "voiceLatencyAverage", label: "Voice" },
];

const BreakdownTooltip = ({ data, totalMs }) => {
  const stages = PIPELINE_STAGES.filter(
    (s) => data?.[s.key] != null && Number.isFinite(Number(data[s.key])),
  ).map((s) => ({ ...s, value: Number(data[s.key]) }));

  if (!stages.length && !totalMs) return null;

  return (
    <Box sx={{ p: 0.5, minWidth: 180 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1 }}>
        Latency pipeline
      </Typography>
      {stages.map((s) => (
        <Box
          key={s.key}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            py: 0.1,
            gap: 2,
          }}
        >
          <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
            {s.label}
          </Typography>
          <Typography
            sx={{
              fontSize: 12,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmtMs(s.value)}
          </Typography>
        </Box>
      ))}
      {stages.length > 0 && (
        <Box sx={{ borderTop: "1px solid rgba(255,255,255,0.15)", my: 0.75 }} />
      )}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          py: 0.1,
          gap: 2,
        }}
      >
        <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
          Avg total
        </Typography>
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {fmtMs(totalMs)}
        </Typography>
      </Box>
    </Box>
  );
};

BreakdownTooltip.propTypes = {
  data: PropTypes.object,
  totalMs: PropTypes.number,
};

const VoiceLatencyCell = (params) => {
  const data = params?.data;
  const totalMs =
    Number(data?.avg_agent_latency_ms) || Number(data?.turnLatencyAverage) || 0;

  const hasStages = PIPELINE_STAGES.some(
    (s) => data?.[s.key] != null && Number.isFinite(Number(data[s.key])),
  );

  if (!totalMs && !hasStages) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.disabled", px: 2 }}
      >
        -
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        px: 2,
        height: "100%",
      }}
    >
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {fmtMs(totalMs, { forceMs: true })}
      </Typography>
      {hasStages && (
        <Tooltip
          title={<BreakdownTooltip data={data} totalMs={totalMs} />}
          arrow
          placement="bottom"
          slotProps={{
            tooltip: { sx: { maxWidth: 280, bgcolor: "grey.900", p: 1 } },
          }}
        >
          <Box
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            sx={{
              display: "flex",
              cursor: "default",
              opacity: 0.4,
              "&:hover": { opacity: 1 },
            }}
          >
            <Iconify
              icon="mdi:information-outline"
              width={14}
              color="text.secondary"
            />
          </Box>
        </Tooltip>
      )}
    </Box>
  );
};

VoiceLatencyCell.propTypes = { data: PropTypes.object };

export default React.memo(VoiceLatencyCell);
