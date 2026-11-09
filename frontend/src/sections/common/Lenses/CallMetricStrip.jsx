// The Call lens metric strip (R4 lane M): per lane Task completion ✓/✗,
// Duration, Latency p50 / p90 (this call), Kept talking over the caller,
// WER and Cost per call, with the change against the baseline lane (red
// when worse). Always under it: where the latency numbers come from
// (`Estimated from the recording`, Preview; or `Reported by the provider`).
// Text-only lanes show `—` with the reason, never a latency.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Term from "src/sections/common/ux/Term";
import {
  EMPTY,
  NUM_SX,
  fmtCost,
  fmtCostDelta,
  fmtDeltaMs,
  fmtMs,
  fmtPct,
  fmtPts,
} from "src/sections/common/format/fmt";
import { fmtDuration } from "src/sections/common/Media/mediaRef";
import LaneLabel from "./LaneLabel";
import { callMetricsOf, metricChange } from "./lensModel";

const TEXT_ONLY_WHY =
  "Text only: this run played the scenario as text, so there is no audio to time, interrupt or transcribe.";

const COLS = [
  { key: "task", label: "Task completion", term: null },
  // IT-R5 G4 P5: every latency says its kind (rule 36).
  { key: "duration", label: "Call length" },
  { key: "p50", label: "Reply latency p50", term: "latencyP90" },
  { key: "p90", label: "Reply latency p90", term: "latencyP90" },
  { key: "kept", label: "Kept talking", term: "bargeIn" },
  { key: "wer", label: "WER", term: "wer" },
  { key: "cost", label: "Cost per call" },
];

const fmtValue = (key, v, m) => {
  if (v == null) return EMPTY;
  if (key === "task") return v ? "✓ Passed" : "✗ Failed";
  if (key === "duration") return fmtDuration(v);
  if (key === "p50" || key === "p90") return fmtMs(v);
  if (key === "kept") return m.bargeIns ? `${v} of ${m.bargeIns}` : String(v);
  if (key === "wer") return fmtPct(v);
  if (key === "cost") return fmtCost(v);
  return String(v);
};

const fmtDelta = (key, v, b) => {
  if (v == null || b == null) return null;
  if (key === "task") return v === b ? null : v > b ? "better" : "worse";
  if (key === "duration" || key === "p50" || key === "p90")
    return fmtDeltaMs(v - b);
  if (key === "kept") {
    const d = v - b;
    return d === 0 ? "±0" : d > 0 ? `+${d}` : `−${Math.abs(d)}`;
  }
  if (key === "wer") return fmtPts((v - b) * 100);
  if (key === "cost") return fmtCostDelta(v - b);
  return null;
};

/** `Estimated from the recording` / `Reported by the provider` per lane. */
export function SourceLabel({ lanes, calls }) {
  const sources = lanes.map((l, i) => ({
    lane: l,
    src: calls[i]?.depth === "text_only" ? null : calls[i]?.latency_source,
  }));
  const rec = sources.filter((s) => s.src === "recording");
  const prov = sources.filter((s) => s.src === "provider");
  const text = sources.filter(
    (s) => s.src == null && calls[lanes.indexOf(s.lane)],
  );
  const codes = (xs) => xs.map((s) => s.lane.label.split(" ")[0]).join(", ");
  const all = rec.length + prov.length + text.length;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      flexWrap="wrap"
      data-latency-source=""
      sx={{ typography: "s3", color: "text.secondary" }}
    >
      {rec.length ? (
        <Box component="span" data-source="recording">
          <Term
            k="preview"
            extra="Computed from the separate caller and agent tracks; not yet verified for every provider."
          >
            Estimated from the recording
          </Term>
          {rec.length < all ? ` · ${codes(rec)}` : ""}
        </Box>
      ) : null}
      {prov.length ? (
        <CustomTooltip
          show
          size="small"
          arrow
          title="This call has no recording to measure, so latency comes from the provider's own call report."
        >
          <Box component="span" data-source="provider">
            {`Reported by the provider${prov.length < all ? ` · ${codes(prov)}` : ""}`}
          </Box>
        </CustomTooltip>
      ) : null}
      {text.length ? (
        <Box component="span" data-source="text">
          {`Text only · no latency${text.length < all ? ` · ${codes(text)}` : ""}`}
        </Box>
      ) : null}
    </Stack>
  );
}

SourceLabel.propTypes = {
  lanes: PropTypes.array.isRequired,
  calls: PropTypes.array.isRequired,
};

/**
 * IT-R2 G4 P6 · the metrics in ONE row (`compact`): each column's label over
 * every lane's value (a run-colour dot each; better green, worse red
 * against the baseline). The full table is one click away (`onDetails`).
 */
function CompactMetrics({ lanes, calls, metrics, base, onDetails }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={1.5}
      data-call-metrics="compact"
      sx={{ minWidth: 0 }}
    >
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          display: "grid",
          gridTemplateColumns: `repeat(${COLS.length}, minmax(0, auto))`,
          justifyContent: "space-between",
          columnGap: 1.5,
        }}
      >
        {COLS.map((c) => (
          <Stack key={c.key} sx={{ minWidth: 0 }}>
            <Typography typography="s3" color="text.secondary" noWrap>
              {c.term ? <Term k={c.term}>{c.label}</Term> : c.label}
            </Typography>
            <Stack direction="row" gap={0.75} sx={{ minWidth: 0 }}>
              {lanes.map((lane, i) => {
                const m = metrics[i];
                const v = m[c.key];
                const change =
                  i === 0 ? null : metricChange(c.key, v, base[c.key]);
                const textOnly = calls[i]?.depth === "text_only";
                const tip = `${lane.label}: ${
                  v == null
                    ? textOnly && c.key !== "task" && c.key !== "cost"
                      ? "text only, not measured"
                      : "not measured"
                    : fmtValue(c.key, v, m)
                }${i && fmtDelta(c.key, v, base[c.key]) ? ` (${fmtDelta(c.key, v, base[c.key])} vs ${lanes[0].label})` : ""}`;
                return (
                  <CustomTooltip key={lane.runId} show title={tip} size="small">
                    <Stack
                      direction="row"
                      alignItems="center"
                      gap={0.375}
                      data-metric={`${lane.runId}:${c.key}`}
                      sx={{ minWidth: 0 }}
                    >
                      <Box
                        sx={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          bgcolor: lane.color ?? "text.disabled",
                          flexShrink: 0,
                        }}
                      />
                      <Typography
                        typography="s2"
                        noWrap
                        color={
                          v == null
                            ? "text.disabled"
                            : c.key === "task"
                              ? v
                                ? "green.700"
                                : "red.700"
                              : change === "worse"
                                ? "red.700"
                                : change === "better"
                                  ? "green.700"
                                  : "text.primary"
                        }
                        sx={NUM_SX}
                      >
                        {c.key === "task" && v != null
                          ? v
                            ? "✓"
                            : "✗"
                          : fmtValue(c.key, v, m)}
                        {/* IT-R5 G4 P5: `700 ms (−140 ms)` on a timing. */}
                        {i &&
                        lanes.length === 2 &&
                        ["p50", "duration"].includes(c.key) &&
                        fmtDelta(c.key, v, base[c.key])
                          ? ` (${fmtDelta(c.key, v, base[c.key])})`
                          : ""}
                      </Typography>
                    </Stack>
                  </CustomTooltip>
                );
              })}
            </Stack>
          </Stack>
        ))}
      </Box>
      {onDetails ? (
        <Box
          component="button"
          type="button"
          onClick={onDetails}
          data-metrics-details=""
          sx={{
            all: "unset",
            cursor: "pointer",
            flexShrink: 0,
            typography: "s3",
            color: "primary.main",
            alignSelf: "center",
            "&:hover": { textDecoration: "underline" },
            "&:focus-visible": { outline: "2px solid" },
          }}
        >
          Table ▾
        </Box>
      ) : null}
    </Stack>
  );
}
CompactMetrics.propTypes = {
  lanes: PropTypes.array.isRequired,
  calls: PropTypes.array.isRequired,
  metrics: PropTypes.array.isRequired,
  base: PropTypes.object,
  onDetails: PropTypes.func,
};

export default function CallMetricStrip({
  lanes,
  calls,
  labelWidth = 200,
  compact = false,
  onToggle,
}) {
  const metrics = lanes.map((l, i) =>
    callMetricsOf(calls[i], { passed: l.passed }),
  );
  const base = metrics[0];
  if (compact)
    return (
      <CompactMetrics
        lanes={lanes}
        calls={calls}
        metrics={metrics}
        base={base}
        onDetails={onToggle}
      />
    );
  const grid = `${labelWidth}px repeat(${COLS.length}, minmax(0, 1fr))`;
  return (
    <Stack gap={0.5} data-call-metrics="">
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: grid,
          columnGap: 1,
          rowGap: 0.25,
          alignItems: "center",
        }}
      >
        <Box />
        {COLS.map((c) => (
          <Typography
            key={c.key}
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ textAlign: "right" }}
          >
            {c.term ? <Term k={c.term}>{c.label}</Term> : c.label}
          </Typography>
        ))}
        {lanes.map((lane, i) => {
          const m = metrics[i];
          const textOnly = calls[i]?.depth === "text_only";
          return (
            <React.Fragment key={lane.runId}>
              <LaneLabel lane={lane} dense />
              {COLS.map((c) => {
                const v = m[c.key];
                const change =
                  i === 0 ? null : metricChange(c.key, v, base[c.key]);
                const delta = i === 0 ? null : fmtDelta(c.key, v, base[c.key]);
                const why =
                  v == null
                    ? textOnly && c.key !== "task" && c.key !== "cost"
                      ? TEXT_ONLY_WHY
                      : c.key === "task"
                        ? "No Task completion score on this row."
                        : "Not measured on this call."
                    : null;
                return (
                  <CustomTooltip
                    key={c.key}
                    show={Boolean(why)}
                    title={why}
                    size="small"
                    arrow
                  >
                    <Stack
                      alignItems="flex-end"
                      data-metric={`${lane.runId}:${c.key}`}
                      sx={{ minWidth: 0 }}
                    >
                      <Typography
                        typography="s2"
                        color={
                          c.key === "task" && v != null
                            ? v
                              ? "green.700"
                              : "red.700"
                            : v == null
                              ? "text.disabled"
                              : "text.primary"
                        }
                        noWrap
                        sx={NUM_SX}
                      >
                        {fmtValue(c.key, v, m)}
                      </Typography>
                      {delta ? (
                        <Typography
                          typography="s3"
                          noWrap
                          color={
                            change === "worse"
                              ? "red.700"
                              : change === "better"
                                ? "green.700"
                                : "text.disabled"
                          }
                          sx={NUM_SX}
                          data-change={change ?? "none"}
                        >
                          {delta}
                        </Typography>
                      ) : null}
                    </Stack>
                  </CustomTooltip>
                );
              })}
            </React.Fragment>
          );
        })}
      </Box>
      <Stack direction="row" alignItems="center" gap={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <SourceLabel lanes={lanes} calls={calls} />
        </Box>
        {onToggle ? (
          <Box
            component="button"
            type="button"
            onClick={onToggle}
            data-metrics-details="open"
            sx={{
              all: "unset",
              cursor: "pointer",
              typography: "s3",
              color: "primary.main",
              "&:hover": { textDecoration: "underline" },
              "&:focus-visible": { outline: "2px solid" },
            }}
          >
            One row ▴
          </Box>
        ) : null}
      </Stack>
    </Stack>
  );
}

CallMetricStrip.propTypes = {
  lanes: PropTypes.array.isRequired,
  calls: PropTypes.array.isRequired,
  labelWidth: PropTypes.number,
  compact: PropTypes.bool,
  onToggle: PropTypes.func,
};
