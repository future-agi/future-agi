import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Tooltip, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { traceSkillsEndpoint } from "src/utils/axios";
import { runtimeErrorKind } from "src/api/skills/runtime";
import {
  DIAGNOSIS,
  METRIC,
  formatCost,
  formatCount,
  formatMs,
  formatPercent,
  lowSampleTitle,
} from "src/sections/skills/runtime/runtimeCopy";
import { OBSERVE_SKILL_COPY, hasSkillFilter } from "./skillFilters";

// The Observe cohort strip (SKILL-TRACES §7.1, UX-12, lane ST9): Traces,
// Failed requests, p95 and Cost per trace for the traces that match the
// whole filter request (endpoint 11, compiled by the shared filter compiler),
// so a regression is visible inside Observe. Shown only when the request has
// at least one SKILL item. Fewer than 30 traces reads as a low sample: the
// numbers stay, in the secondary colour, with no comparison.

const COPY = OBSERVE_SKILL_COPY.cohort;
const LOW_SAMPLE = 30;

const finite = (value) => (Number.isFinite(Number(value)) && value !== null ? Number(value) : null);

/** Validate the endpoint-11 response into display numbers. */
export function mapCohortSummary(value) {
  if (!value || typeof value !== "object") return null;
  const traces = finite(value.traces);
  if (traces === null) return null;
  const failed = value.failed_requests || {};
  return {
    traces,
    failedCount: finite(failed.count),
    failedRate: finite(failed.rate),
    p95: finite(value.latency_ms?.p95),
    costPerTrace: finite(value.cost?.per_trace_mean),
    incomplete: finite(value.incomplete) ?? 0,
    lowSample: traces < LOW_SAMPLE,
  };
}

/** Endpoint 11. `filters` is the full API filter list the trace list sends. */
export function useSkillCohortSummary({ projectId, filters, from, to, enabled = true }) {
  const active = Boolean(enabled && projectId && from && to && hasSkillFilter(filters));
  const filtersKey = useMemo(() => JSON.stringify(filters || []), [filters]);
  return useQuery({
    queryKey: ["observe", "skill-cohort", projectId, filtersKey, from, to],
    queryFn: async ({ signal }) => {
      const url = traceSkillsEndpoint();
      const response = await axios.post(
        url,
        { projection: "cohort", project_id: projectId, filters: JSON.parse(filtersKey), from, to },
        { signal },
      );
      return mapCohortSummary(response?.data?.result?.summary);
    },
    enabled: active,
    retry: false,
    staleTime: 30 * 1000,
    meta: { errorHandled: true },
  });
}

function Metric({ label, value, secondary, title }) {
  const body = (
    <Stack spacing={0.25} sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="s1"
        sx={{ fontWeight: 600, color: secondary ? "text.secondary" : "text.primary", overflowWrap: "anywhere" }}
      >
        {value}
      </Typography>
    </Stack>
  );
  return title ? <Tooltip title={title}>{body}</Tooltip> : body;
}

Metric.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  secondary: PropTypes.bool,
  title: PropTypes.string,
};

/** The four numbers as display strings (pure). */
export function cohortMetrics(summary) {
  if (!summary) return [];
  const failed = summary.failedCount === null
    ? "—"
    : `${formatCount(summary.failedCount)}${summary.failedRate === null ? "" : ` · ${formatPercent(summary.failedRate)}`}`;
  return [
    { key: "traces", label: METRIC.traces, value: formatCount(summary.traces) },
    { key: "failed", label: METRIC.failedRequests, value: failed },
    { key: "p95", label: METRIC.p95, value: formatMs(summary.p95) },
    { key: "cost", label: METRIC.costPerTrace, value: formatCost(summary.costPerTrace) },
  ];
}

const gridSx = {
  display: "grid",
  gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(4, minmax(0, max-content))" },
  columnGap: 4,
  rowGap: 1,
};

export default function SkillCohortStrip({ projectId, filters, from, to, enabled = true }) {
  const query = useSkillCohortSummary({ projectId, filters, from, to, enabled });
  if (!enabled || !hasSkillFilter(filters) || !projectId) return null;
  const wrapperSx = {
    px: 2,
    py: 1,
    borderBottom: "1px solid",
    borderColor: "divider",
    bgcolor: "background.paper",
  };
  if (query.isError) {
    const kind = runtimeErrorKind(query.error);
    return (
      <Box role="status" sx={wrapperSx}>
        <Typography variant="caption" color="text.secondary">
          {kind === "slow" ? DIAGNOSIS.readBudget : COPY.error}
        </Typography>
      </Box>
    );
  }
  if (query.isPending && query.fetchStatus !== "idle") {
    return (
      <Box role="status" aria-label={COPY.loading} sx={{ ...wrapperSx, ...gridSx }}>
        {[0, 1, 2, 3].map((index) => (
          <Stack key={index} spacing={0.25}>
            <Skeleton variant="text" width={72} />
            <Skeleton variant="text" width={56} />
          </Stack>
        ))}
      </Box>
    );
  }
  const summary = query.data;
  if (!summary) return null;
  const metrics = cohortMetrics(summary);
  return (
    <Box role="region" aria-label={COPY.title} sx={wrapperSx}>
      <Box sx={gridSx}>
        {metrics.map((metric) => (
          <Metric
            key={metric.key}
            label={metric.label}
            value={metric.value}
            secondary={summary.lowSample}
            title={summary.lowSample ? lowSampleTitle(summary.traces) : undefined}
          />
        ))}
      </Box>
      {summary.incomplete > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
          {DIAGNOSIS.rootMissing(summary.incomplete)}
        </Typography>
      )}
    </Box>
  );
}

SkillCohortStrip.propTypes = {
  projectId: PropTypes.string,
  filters: PropTypes.array,
  from: PropTypes.string,
  to: PropTypes.string,
  enabled: PropTypes.bool,
};
