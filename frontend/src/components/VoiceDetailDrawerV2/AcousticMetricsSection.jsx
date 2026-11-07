import React, { useContext, useId, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, ButtonBase, Chip, Stack, Typography } from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import {
  ACOUSTIC_METRICS,
  AcousticMetricsLoadContext,
  REASON_LABELS,
  STATE_LABELS,
  captureOriginLabel,
  formatMetricValue,
  isAcousticMetricsVisible,
  transportLabel,
} from "./acousticMetricsLabels";

function metricText(entry) {
  if (entry?.state === "available") return formatMetricValue(entry);
  if (entry?.state === "pending") return STATE_LABELS.pending;
  const failed = entry?.state === "failed";
  const fallback = failed ? "Analysis failed" : "Unavailable";
  const reason = Object.hasOwn(REASON_LABELS, entry?.reason)
    ? REASON_LABELS[entry.reason]
    : fallback;
  if (reason === fallback) return reason;
  return `${reason} — ${failed ? STATE_LABELS.failed : STATE_LABELS.unavailable}`;
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
          minHeight: 40,
          borderRadius: "4px",
          color: "text.secondary",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "border.focus",
            outlineOffset: 2,
          },
        }}
      >
        <Iconify
          icon={
            expanded
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={16}
          aria-hidden="true"
        />
        <Typography component="span" variant="s1">
          {label}
        </Typography>
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

function ComputedTime({ value }) {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })}
    </time>
  );
}
ComputedTime.propTypes = { value: PropTypes.string };

export default function AcousticMetricsSection({ data, isStale = false }) {
  const titleId = useId();
  const loadState = useContext(AcousticMetricsLoadContext);
  const initialError = data === undefined && loadState?.isError;
  const initialLoading =
    data === undefined && loadState?.isLoading && !initialError;
  if (!isAcousticMetricsVisible(data) && !initialError && !initialLoading)
    return null;
  const unsupported =
    data?.state === "unsupported" ||
    (data?.schema_version != null && data.schema_version !== 1);
  const state = isStale ? "stale" : unsupported ? "unavailable" : data?.state;
  const source = data?.source;
  const metrics = data?.metrics;
  const pitchCoverage = metrics?.average_pitch_hz?.coverage;
  const snrCoverage = metrics?.estimated_snr_db?.coverage;
  const vqi = metrics?.voice_quality_index;
  const coverage = vqi?.coverage;
  const speechPercentage =
    Number.isFinite(coverage?.covered_speech_seconds) &&
    Number.isFinite(coverage?.detected_speech_seconds) &&
    coverage.detected_speech_seconds > 0
      ? `${((100 * coverage.covered_speech_seconds) / coverage.detected_speech_seconds).toFixed(1)}%`
      : "—";
  // The third column marks technical identifiers, kept secondary and monospace.
  const details = [
    ["Capture origin", captureOriginLabel(source?.capture_origin)],
    ["Transport", transportLabel(source?.transport)],
    ["Provider", source?.provider],
    [
      "Target role",
      (source?.target_role ?? data?.target_role) === "tested_agent"
        ? "Tested agent"
        : "—",
    ],
    ["Mapping version", source?.mapping_version, true],
    ["Original sample rate (Hz)", source?.original_sample_rate_hz],
    ["Original channels", source?.original_channel_count],
    ["Target samples", source?.target_sample_count],
    ["Input duration (s)", source?.input_duration_seconds],
    ["Pitch voiced audio (s)", pitchCoverage?.voiced_seconds],
    ["Pitch analyzed audio (s)", pitchCoverage?.analyzed_seconds],
    ["SNR speech-level frames (s)", snrCoverage?.active_seconds],
    ["SNR noise-level frames (s)", snrCoverage?.inactive_seconds],
    ["SNR analyzed audio (s)", snrCoverage?.analyzed_seconds],
    ["VQI windows", coverage?.window_count],
    ["VQI excluded windows", coverage?.excluded_window_count],
    ["VQI detected speech (s)", coverage?.detected_speech_seconds],
    ["VQI covered speech (s)", coverage?.covered_speech_seconds],
    ["VQI speech coverage", speechPercentage],
    ["VQI cohort", coverage?.cohort, true],
    ["Preprocessing version", source?.preprocessing_version, true],
    ["Pipeline version", data?.pipeline_version, true],
    [
      "Pitch algorithm version",
      metrics?.average_pitch_hz?.algorithm_version,
      true,
    ],
    [
      "SNR estimator version",
      metrics?.estimated_snr_db?.algorithm_version,
      true,
    ],
    ["VQI algorithm version", vqi?.algorithm_version, true],
    ["Model version", vqi?.model_version, true],
    ["Calibration", vqi?.calibration_id, true],
    ["Scale", vqi?.scale_id, true],
    ["Generation", data?.generation],
    ["Computed at", <ComputedTime key="computed" value={data?.computed_at} />],
    ...ACOUSTIC_METRICS.filter(([key]) => metrics?.[key]?.reason).map(
      ([key, label]) => [`${label} reason code`, metrics[key].reason, true],
    ),
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
        p: 1.5,
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
          variant="m3"
          sx={{ fontWeight: 500 }}
        >
          Acoustic metrics · Tested agent
        </Typography>
        {!initialError && !initialLoading && (
          <StatusBadge
            status={state}
            label={STATE_LABELS[state] || STATE_LABELS.unavailable}
            sx={{
              height: "auto",
              "& .MuiTypography-root": { whiteSpace: "normal" },
            }}
          />
        )}
      </Stack>
      {initialError ? (
        <ErrorState
          title="Couldn't load audio metrics"
          detail="Call details could not be loaded. Audio metrics are unavailable."
          role="alert"
          sx={{ py: 2 }}
        />
      ) : initialLoading ? (
        <ContentSkeleton variant="panel" label="Loading audio metrics" />
      ) : (
        <>
          {isStale && (
            <Alert severity="warning" role="status" sx={{ mt: 1 }}>
              Couldn&apos;t refresh audio metrics. Showing the last result.
            </Alert>
          )}
          {!unsupported && (
            <Typography
              variant="s3"
              sx={{
                mt: 0.5,
                color: "text.secondary",
                overflowWrap: "anywhere",
              }}
            >
              Capture: {captureOriginLabel(source?.capture_origin)} ·{" "}
              {transportLabel(source?.transport)}
            </Typography>
          )}
          <Box role="list" sx={{ mt: 1 }}>
            {unsupported ? (
              <Typography role="listitem" variant="s1" sx={{ py: 1 }}>
                Unsupported metrics version
              </Typography>
            ) : (
              ACOUSTIC_METRICS.map(([key, label]) => (
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
                    <Typography variant="s1">{label}</Typography>
                    <Typography
                      variant="s1"
                      sx={{
                        fontWeight: 500,
                        overflowWrap: "anywhere",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {metricText(metrics?.[key])}
                    </Typography>
                  </Stack>
                  {key === "estimated_snr_db" && (
                    <Stack
                      direction="row"
                      gap={1}
                      flexWrap="wrap"
                      sx={{ "& .MuiChip-root": { mt: 1 } }}
                    >
                      {metrics?.[key]?.flags?.noise_floor_floored === true && (
                        <Chip label="Noise floor floored" size="small" />
                      )}
                      {metrics?.[key]?.flags?.value_capped === true && (
                        <Chip label="Capped at 100 dB" size="small" />
                      )}
                    </Stack>
                  )}
                  {key === "voice_quality_index" && (
                    <Box sx={{ mt: 0.5, color: "text.secondary" }}>
                      <Typography variant="s3" component="p">
                        Estimated perceptual quality, normalized 0–5
                      </Typography>
                      <Typography variant="s3" component="p">
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
                    overflowWrap: "anywhere",
                  }}
                >
                  {details.map(([label, value, technical]) => (
                    <React.Fragment key={label}>
                      <Typography
                        component="dt"
                        variant="s3"
                        color="text.secondary"
                      >
                        {label}
                      </Typography>
                      <Typography
                        component="dd"
                        variant="s3"
                        sx={{
                          m: 0,
                          fontVariantNumeric: "tabular-nums",
                          ...(technical && {
                            fontFamily: "monospace",
                            color: "text.secondary",
                          }),
                        }}
                      >
                        {value ?? "—"}
                      </Typography>
                    </React.Fragment>
                  ))}
                </Box>
              </Disclosure>
              <Disclosure label="Measurement limits">
                <Stack gap={1} sx={{ py: 1, color: "text.secondary" }}>
                  <Typography variant="s1">
                    Pitch is not a quality ranking.
                  </Typography>
                  <Typography variant="s1">
                    SNR is an energy-contrast estimate that needs pauses.
                  </Typography>
                  <Typography variant="s1">
                    VQI is a normalized perceptual prediction, not MOS or a
                    naturalness certification.
                  </Typography>
                </Stack>
              </Disclosure>
            </>
          )}
        </>
      )}
    </Box>
  );
}

AcousticMetricsSection.propTypes = {
  data: PropTypes.object,
  isStale: PropTypes.bool,
};
