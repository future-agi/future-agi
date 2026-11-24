import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { format } from "date-fns";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import MetricStrip from "src/components/metric-strip";
import StatusBadge from "src/components/status-badge";
import { useErrorFeedTraces } from "src/api/errorFeed/error-feed";
import { useSharedIssue } from "../sharedIssueContext";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import { PROJECT_SOURCE } from "src/utils/constants";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { copyToClipboard } from "src/utils/utils";

const MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

const EMPTY_AGG = {
  total_traces: 0,
  avg_score: null,
  avg_turns: null,
  p50_latency: 0,
  p95_latency: 0,
};

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms >= 60_000) {
    const totalSec = Math.round(ms / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function formatStarted(iso) {
  if (!iso) return "—";
  try {
    return format(new Date(iso), "MMM d, HH:mm");
  } catch {
    return "—";
  }
}

export default function TracesTab({ error }) {
  const clusterId = error?.cluster_id;
  const projectId = error?.project_id;
  const shared = useSharedIssue();
  const fetched = useErrorFeedTraces(shared ? null : clusterId, { limit: 200 });
  const data = shared ? shared.traces : fetched.data;
  const isLoading = shared ? false : fetched.isLoading;
  const isError = shared ? false : fetched.isError;
  const copyTimerRef = useRef(null);

  const [drawerTraceId, setDrawerTraceId] = useState(null);
  const [drawerInitialSpanId, setDrawerInitialSpanId] = useState(null);
  const [showUsageCols, setShowUsageCols] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [query, setQuery] = useState("");

  useEffect(
    () => () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    },
    [],
  );

  const { data: projectDetail } = useGetProjectDetails(
    projectId,
    !!projectId && !shared,
  );
  const isVoiceProject = projectDetail?.source === PROJECT_SOURCE.SIMULATOR;
  const { data: voiceCallData, isFetching: voiceLoading } = useVoiceCallDetail(
    drawerTraceId,
    { enabled: isVoiceProject && !!drawerTraceId, projectId },
  );
  const voiceCovered = useDrawerCovered(isVoiceProject && !!drawerTraceId);

  const agg = data?.aggregates ?? EMPTY_AGG;
  const allRows = useMemo(() => data?.traces ?? [], [data]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allRows;
    return allRows.filter((r) => {
      const hay = `${r.id || ""} ${r.input || ""} ${r.why_linked || ""} ${r.origin_alias || ""} ${r.symptom_alias || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [allRows, query]);
  const total = data?.total ?? allRows.length;

  const openRow = useCallback((row) => {
    setDrawerTraceId(row.id);
    setDrawerInitialSpanId(row.origin_span_id || null);
  }, []);

  const traceIndex = rows.findIndex((r) => r.id === drawerTraceId);
  const handlePrev = useCallback(() => {
    if (traceIndex > 0) openRow(rows[traceIndex - 1]);
  }, [traceIndex, rows, openRow]);
  const handleNext = useCallback(() => {
    if (traceIndex < rows.length - 1) openRow(rows[traceIndex + 1]);
  }, [traceIndex, rows, openRow]);

  const handleCopyTraceId = async (e, id) => {
    e.stopPropagation();
    await copyToClipboard(id);
    setCopiedId(id);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(
      () => setCopiedId((cur) => (cur === id ? null : cur)),
      1400,
    );
  };

  if (isLoading && !data) {
    return <ContentSkeleton variant="rows" rows={5} label="Loading linked traces" />;
  }

  if (isError && !data) {
    return (
      <ErrorState
        title="Couldn't load linked traces"
        detail="Check your connection and retry."
        onRetry={() => fetched.refetch?.()}
        sx={{ py: 4 }}
      />
    );
  }

  const metricItems = [
    { label: "Total traces", value: (agg.total_traces ?? total).toLocaleString() },
    {
      label: "Avg score",
      value: agg.avg_score != null ? Number(agg.avg_score).toFixed(2) : "—",
    },
    ...(isVoiceProject && agg.avg_turns != null
      ? [{ label: "Avg turns", value: Number(agg.avg_turns).toFixed(1) }]
      : []),
    { label: "P50 duration", value: formatDuration(agg.p50_latency) },
    { label: "P95 duration", value: formatDuration(agg.p95_latency) },
  ];

  return (
    <Stack gap={2.5}>
      <MetricStrip items={metricItems} />

      <Stack gap={1.5}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ xs: "stretch", sm: "center" }}
          justifyContent="space-between"
          gap={1.25}
        >
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s1" fontWeight={600} color="text.primary">
              {total.toLocaleString()} linked {total === 1 ? "trace" : "traces"}
            </Typography>
            {query.trim() && (
              <Typography typography="s3" color="text.secondary">
                · {rows.length} matching
              </Typography>
            )}
          </Stack>
          <Stack direction="row" alignItems="center" gap={1}>
            <FormSearchField
              searchQuery={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search traces or conversations"
              sx={{ minWidth: { xs: 180, sm: 250 } }}
            />
            <Button
              size="small"
              variant="outlined"
              onClick={() => setShowUsageCols((v) => !v)}
              sx={{
                borderColor: "divider",
                color: "text.secondary",
                whiteSpace: "nowrap",
              }}
            >
              {showUsageCols ? "Hide tokens & cost" : "Tokens & cost"}
            </Button>
          </Stack>
        </Stack>

        {rows.length === 0 ? (
          <EmptyState
            icon="solar:file-search-linear"
            title={
              query.trim()
                ? `No linked traces match "${query.trim()}"`
                : "No linked traces yet"
            }
            description={
              query.trim()
                ? "Clear your search to see all linked traces."
                : "Traces matching this issue will appear here."
            }
            action={
              query.trim() ? (
                <Button size="small" variant="outlined" onClick={() => setQuery("")}>
                  Clear search
                </Button>
              ) : null
            }
            sx={{ py: 4 }}
          />
        ) : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small" sx={{ minWidth: 680 }}>
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell sx={{ width: 82, whiteSpace: "nowrap" }}>Status</TableCell>
                  <TableCell sx={{ minWidth: 185 }}>User request</TableCell>
                  <TableCell sx={{ minWidth: 165 }}>Why linked</TableCell>
                  <TableCell sx={{ width: 88, whiteSpace: "nowrap" }}>Path</TableCell>
                  <TableCell sx={{ width: 96, whiteSpace: "nowrap" }}>Started</TableCell>
                  <TableCell align="right" sx={{ width: 72, whiteSpace: "nowrap" }}>
                    Duration
                  </TableCell>
                  {showUsageCols && (
                    <>
                      <TableCell align="right" sx={{ width: 84, whiteSpace: "nowrap" }}>
                        Tokens
                      </TableCell>
                      <TableCell align="right" sx={{ width: 76, whiteSpace: "nowrap" }}>
                        Cost
                      </TableCell>
                    </>
                  )}
                  <TableCell sx={{ width: 94, whiteSpace: "nowrap" }}>Trace ID</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => {
                  const statusStr = String(row.status || "").toLowerCase();
                  const isPass = statusStr === "pass" || statusStr === "ok" || statusStr === "passed";
                  const pathText = row.origin_alias
                    ? `${row.origin_alias}${
                        row.symptom_alias && row.symptom_alias !== row.origin_alias
                          ? ` → ${row.symptom_alias}`
                          : ""
                      }`
                    : "—";
                  return (
                  <TableRow
                    key={row.id}
                    hover
                    onClick={() => openRow(row)}
                    sx={{ cursor: "pointer", height: ROW_HEIGHT.twoLine }}
                  >
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <StatusBadge
                        role={isPass ? "success" : "danger"}
                        label={isPass ? "Passed" : "Failed"}
                        size="sm"
                      />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 340, py: 1 }}>
                      <Tooltip title={row.input || ""} arrow placement="top-start">
                        <Typography
                          typography="s1"
                          fontWeight={500}
                          color="text.primary"
                          sx={{
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                            lineHeight: 1.45,
                          }}
                        >
                          {row.input || "—"}
                          {row.is_representative ? " · Example" : ""}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 300, py: 1 }}>
                      <Tooltip title={row.why_linked || ""} arrow placement="top-start">
                        <Typography
                          typography="s3"
                          color="text.secondary"
                          sx={{
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                            lineHeight: 1.45,
                          }}
                        >
                          {row.why_linked || "Matched cluster signature"}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography
                        typography="s1"
                        color="text.secondary"
                        sx={{ fontFamily: MONO_FONT }}
                      >
                        {pathText}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography
                        typography="s1"
                        color="text.secondary"
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        {formatStarted(row.timestamp)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                      <Typography
                        typography="s1"
                        color="text.primary"
                        sx={{ fontFamily: MONO_FONT, fontVariantNumeric: "tabular-nums" }}
                      >
                        {formatDuration(row.latency_ms)}
                      </Typography>
                    </TableCell>
                    {showUsageCols && (
                      <>
                        <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                          <Typography
                            typography="s1"
                            color="text.secondary"
                            sx={{ fontFamily: MONO_FONT, fontVariantNumeric: "tabular-nums" }}
                          >
                            {row.tokens != null ? row.tokens.toLocaleString() : "—"}
                          </Typography>
                        </TableCell>
                        <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                          <Typography
                            typography="s1"
                            color="text.secondary"
                            sx={{ fontFamily: MONO_FONT, fontVariantNumeric: "tabular-nums" }}
                          >
                            {row.cost != null ? `$${Number(row.cost).toFixed(4)}` : "—"}
                          </Typography>
                        </TableCell>
                      </>
                    )}
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Stack direction="row" alignItems="center" gap={0.25}>
                        <Typography typography="s1" color="text.secondary" sx={{ fontFamily: MONO_FONT }}>
                          {String(row.id || "").slice(0, 8)}
                        </Typography>
                        <Tooltip title={copiedId === row.id ? "Copied" : "Copy trace ID"} arrow>
                          <IconButton
                            size="small"
                            aria-label="Copy trace ID"
                            onClick={(e) => handleCopyTraceId(e, row.id)}
                            sx={{ p: 0.35, color: "text.secondary" }}
                          >
                            <Iconify
                              icon={copiedId === row.id ? "solar:check-linear" : "solar:copy-linear"}
                              width={14}
                            />
                          </IconButton>
                        </Tooltip>
                      </Stack>
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        )}
      </Stack>

      {isVoiceProject ? (
        <Drawer
          anchor="right"
          variant="persistent"
          open={!!drawerTraceId}
          onClose={() => setDrawerTraceId(null)}
          sx={coveredDrawerSx(voiceCovered)}
          PaperProps={{
            sx: {
              width: "auto",
              height: "100vh",
              position: "fixed",
              right: 0,
              borderRadius: 0,
              bgcolor: "background.paper",
              display: "flex",
              flexDirection: "column",
              borderLeft: "1px solid",
              borderColor: "divider",
              transition: "none",
            },
          }}
          ModalProps={{
            BackdropProps: { style: { backgroundColor: "transparent" } },
          }}
        >
          {drawerTraceId && (
            <VoiceDetailDrawerV2
              data={
                voiceCallData
                  ? { ...voiceCallData, project_id: projectId, module: "project" }
                  : { trace_id: drawerTraceId, project_id: projectId }
              }
              onClose={() => setDrawerTraceId(null)}
              onPrev={handlePrev}
              onNext={handleNext}
              hasPrev={traceIndex > 0}
              hasNext={traceIndex < rows.length - 1}
              isLoading={voiceLoading}
            />
          )}
        </Drawer>
      ) : (
        <TraceDetailDrawerV2
          traceId={drawerTraceId}
          initialSpanId={drawerInitialSpanId}
          open={!!drawerTraceId}
          onClose={() => setDrawerTraceId(null)}
          projectId={projectId}
          clusterId={clusterId}
          defaultCompressed={false}
          onPrev={handlePrev}
          onNext={handleNext}
          hasPrev={traceIndex > 0}
          hasNext={traceIndex < rows.length - 1}
          readOnly={!!shared}
          sharedToken={shared?.token ?? null}
        />
      )}
    </Stack>
  );
}

TracesTab.propTypes = {
  error: PropTypes.object,
};
