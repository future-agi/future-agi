import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Chip, Stack, Typography } from "@mui/material";
import {
  REASON_LABELS,
  STATE_LABELS,
  formatMetricValue,
} from "./acousticMetricsLabels";

const METRICS = [
  ["average_pitch_hz", "Average pitch (Hz)"],
  ["estimated_snr_db", "Estimated SNR (dB)"],
  ["voice_quality_index", "Voice quality index (0–5)"],
];

function metricText(entry) {
  if (entry?.state === "available") return formatMetricValue(entry);
  if (entry?.state === "pending") return STATE_LABELS.pending;
  const failed = entry?.state === "failed";
  const fallback = failed ? "Analysis failed" : "Unavailable";
  const reason = Object.hasOwn(REASON_LABELS, entry?.reason)
    ? REASON_LABELS[entry.reason]
    : fallback;
  return `${failed ? STATE_LABELS.failed : STATE_LABELS.unavailable} — ${reason}${entry?.reason ? ` (${entry.reason})` : ""}`;
}

function Disclosure({ label, children }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  return (
    <Box>
      <ButtonBase
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls={id}
        sx={{
          py: 1,
          px: 0.5,
          gap: 1,
          fontSize: 12,
          borderRadius: "4px",
          color: "text.secondary",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 2,
          },
        }}
      >
        <Box component="span" aria-hidden="true">
          {expanded ? "▾" : "▸"}
        </Box>
        {label}
      </ButtonBase>
      <Box id={id} hidden={!expanded}>
        {expanded && children}
      </Box>
    </Box>
  );
}
Disclosure.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

export default function AcousticMetricsSection({ data, isStale = false }) {
  const titleId = useId();
  if (data === undefined) return null;
  const unsupported =
    data?.state === "unsupported" ||
    (data?.schema_version != null && data.schema_version !== 1);
  const state = isStale ? "stale" : data?.state;
  const source = data?.source;
  const metrics = data?.metrics;
  const chipSx = {
    height: "auto",
    minHeight: 22,
    borderRadius: "4px",
    fontSize: 11,
    "& .MuiChip-label": { whiteSpace: "normal", py: 0.5 },
  };
  const details = [
    ["Capture origin", source?.capture_origin],
    ["Transport", source?.transport],
    ["Provider", source?.provider],
    ["Target role", source?.target_role ?? data?.target_role],
    ["Mapping version", source?.mapping_version],
    ["Original sample rate (Hz)", source?.original_sample_rate_hz],
    ["Original channels", source?.original_channel_count],
    ["Target samples", source?.target_sample_count],
    ["Input duration (s)", source?.input_duration_seconds],
    ["Preprocessing version", source?.preprocessing_version],
    ["Pipeline version", data?.pipeline_version],
    ["Pitch algorithm version", metrics?.average_pitch_hz?.algorithm_version],
    ["SNR estimator version", metrics?.estimated_snr_db?.algorithm_version],
    ["VQI algorithm version", metrics?.voice_quality_index?.algorithm_version],
    ["Model version", metrics?.voice_quality_index?.model_version],
    ["Calibration", metrics?.voice_quality_index?.calibration_id],
    ["Scale", metrics?.voice_quality_index?.scale_id],
    ["Generation", data?.generation],
    ["Computed at", data?.computed_at],
  ];

  return (
    <Box
      component="section"
      role="region"
      aria-labelledby={titleId}
      aria-live="polite"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        p: 2,
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        flexWrap="wrap"
      >
        <Typography
          id={titleId}
          component="h3"
          sx={{ fontSize: 13, fontWeight: 600 }}
        >
          Acoustic metrics · Tested agent
        </Typography>
        <Chip
          label={STATE_LABELS[state] || STATE_LABELS.unavailable}
          size="small"
          sx={chipSx}
          color={
            state === "complete"
              ? "success"
              : state === "failed"
                ? "error"
                : "default"
          }
        />
      </Stack>
      {!unsupported && (
        <Typography
          sx={{
            mt: 0.5,
            fontSize: 11,
            color: "text.secondary",
            overflowWrap: "anywhere",
          }}
        >
          {source?.capture_origin ?? "—"} · {data?.pipeline_version ?? "—"}
        </Typography>
      )}
      <Box role="list" sx={{ mt: 1 }}>
        {unsupported ? (
          <Typography role="listitem" sx={{ py: 1, fontSize: 12 }}>
            Unsupported metrics version
          </Typography>
        ) : (
          METRICS.map(([key, label]) => (
            <Box
              key={key}
              role="listitem"
              sx={{
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Stack
                direction="row"
                alignItems="baseline"
                justifyContent="space-between"
                gap={1}
                flexWrap="wrap"
              >
                <Typography sx={{ fontSize: 12 }}>{label}</Typography>
                <Typography
                  sx={{
                    fontSize: 12,
                    fontWeight: 500,
                    overflowWrap: "anywhere",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {metricText(metrics?.[key])}
                </Typography>
              </Stack>
              {key === "estimated_snr_db" && (
                <Stack direction="row" gap={1} flexWrap="wrap">
                  {metrics?.[key]?.flags?.noise_floor_floored === true && (
                    <Chip
                      label="Noise floor floored"
                      size="small"
                      sx={{ ...chipSx, mt: 1 }}
                    />
                  )}
                  {metrics?.[key]?.flags?.value_capped === true && (
                    <Chip
                      label="Capped at 100 dB"
                      size="small"
                      sx={{ ...chipSx, mt: 1 }}
                    />
                  )}
                </Stack>
              )}
              {key === "voice_quality_index" && (
                <Box sx={{ mt: 0.5, color: "text.secondary" }}>
                  <Typography sx={{ fontSize: 11 }}>
                    Estimated perceptual quality, normalized 0–5
                  </Typography>
                  <Typography sx={{ fontSize: 11 }}>
                    Not MOS or a naturalness certification
                  </Typography>
                </Box>
              )}
            </Box>
          ))
        )}
      </Box>
      {!unsupported && (
        <>
          <Disclosure label="Capture & version details">
            <Box
              component="dl"
              sx={{
                m: 0,
                py: 1,
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                gap: 1,
                fontSize: 11,
                overflowWrap: "anywhere",
              }}
            >
              {details.map(([label, value]) => (
                <React.Fragment key={label}>
                  <Box component="dt" sx={{ color: "text.secondary" }}>
                    {label}
                  </Box>
                  <Box component="dd" sx={{ m: 0 }}>
                    {value ?? "—"}
                  </Box>
                </React.Fragment>
              ))}
            </Box>
          </Disclosure>
          <Disclosure label="Measurement limits">
            <Stack gap={1} sx={{ py: 1, color: "text.secondary" }}>
              <Typography sx={{ fontSize: 12 }}>
                Pitch is not a quality ranking.
              </Typography>
              <Typography sx={{ fontSize: 12 }}>
                SNR is an energy-contrast estimate that needs pauses.
              </Typography>
              <Typography sx={{ fontSize: 12 }}>
                VQI is a normalized perceptual prediction, not MOS or a
                naturalness certification.
              </Typography>
            </Stack>
          </Disclosure>
        </>
      )}
    </Box>
  );
}

AcousticMetricsSection.propTypes = {
  data: PropTypes.object,
  isStale: PropTypes.bool,
};
