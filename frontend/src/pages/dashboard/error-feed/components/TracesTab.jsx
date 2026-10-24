import React, { useCallback, useMemo, useRef, useState } from "react";
import {
  Box,
  Drawer,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import {
  isMockClusterId,
  useErrorFeedTraces,
} from "src/api/errorFeed/mockAdapter";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import { PROJECT_SOURCE } from "src/utils/constants";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2";
import { statusColors } from "src/utils/runStatus";

const EMPTY_AGG = {
  total_traces: 0,
  avg_score: 0,
  avg_turns: 0,
  p50_latency: 0,
  p95_latency: 0,
};

const TRACES_GRID_THEME_PARAMS = {
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 4,
};

// ── Aggregate bar (no Failing / Passing) ────────────────────────────────────
function AggregateBar({ agg }) {
  const items = [
    { label: "Total traces", value: agg.total_traces.toLocaleString() },
    { label: "Avg score", value: agg.avg_score.toFixed(2) },
    { label: "Avg turns", value: agg.avg_turns.toFixed(1) },
    { label: "P50 latency", value: `${(agg.p50_latency / 1000).toFixed(1)}s` },
    { label: "P95 latency", value: `${(agg.p95_latency / 1000).toFixed(1)}s` },
  ];

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 1,
        mb: 1.5,
      }}
    >
      {items.map((item) => (
        <Box
          key={item.label}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            p: 1,
            textAlign: "center",
          }}
        >
          <Typography fontSize="16px" fontWeight={600} color="text.primary">
            {item.value}
          </Typography>
          <Typography fontSize="12px" color="text.disabled" mt={0.25}>
            {item.label}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
AggregateBar.propTypes = { agg: PropTypes.object.isRequired };

// ── Score cell renderer — full-cell fill, green ≥ 70%, red < 70% ─────────────
function scoreColors(pct, theme) {
  if (pct >= 70) {
    const c = statusColors(theme, "success");
    return { backgroundColor: c.bg, color: c.fg };
  }
  if (pct >= 50) {
    // Amber stays on the cell fill; the number reads in the theme text colour.
    return { backgroundColor: statusColors(theme, "warning").bg, color: null };
  }
  const c = statusColors(theme, "danger");
  return { backgroundColor: c.bg, color: c.fg };
}

function ScoreCellRenderer({ value }) {
  const theme = useTheme();
  if (value == null) {
    return (
      <div
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          paddingInline: 12,
          fontSize: 12,
        }}
      >
        —
      </div>
    );
  }
  const pct = Math.round(value * 100);
  const { backgroundColor, color } = scoreColors(pct, theme);
  return (
    <div
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        alignItems: "center",
        backgroundColor,
        paddingInline: 12,
        fontWeight: 500,
        fontSize: 13,
        color: color ?? theme.palette.text.primary,
      }}
    >
      {pct}%
    </div>
  );
}
ScoreCellRenderer.propTypes = { value: PropTypes.number };

// ── Traces AG Grid ────────────────────────────────────────────────────────────
function TracesGrid({ rows, onRowClick }) {
  const agTheme = useAgThemeWith(TRACES_GRID_THEME_PARAMS);
  const gridRef = useRef(null);

  const colDefs = useMemo(
    () => [
      {
        headerName: "Trace ID",
        field: "id",
        width: 155,
        sortable: true,
        cellStyle: { fontSize: "12px" },
      },
      {
        headerName: "Input",
        field: "input",
        flex: 1,
        minWidth: 220,
        sortable: true,
        cellStyle: { fontSize: "12px" },
      },
      {
        headerName: "Start Time",
        field: "timestamp",
        width: 100,
        sortable: true,
        valueFormatter: (p) =>
          p.value
            ? new Date(p.value).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })
            : "—",
        cellStyle: { fontSize: "12px" },
      },
      {
        headerName: "Duration",
        field: "latency_ms",
        width: 110,
        sortable: true,
        valueFormatter: (p) =>
          p.value != null ? `${p.value.toLocaleString()}ms` : "—",
        cellStyle: { fontSize: "12px", textAlign: "right" },
        headerClass: "ag-right-aligned-header",
      },
      {
        headerName: "Tokens",
        field: "tokens",
        width: 90,
        sortable: true,
        valueFormatter: (p) =>
          p.value != null ? p.value.toLocaleString() : "—",
        cellStyle: { fontSize: "12px", textAlign: "right" },
        headerClass: "ag-right-aligned-header",
      },
      {
        headerName: "Cost",
        field: "cost",
        width: 90,
        sortable: true,
        valueFormatter: (p) =>
          p.value != null ? `$${p.value.toFixed(4)}` : "—",
        cellStyle: { fontSize: "12px", textAlign: "right" },
        headerClass: "ag-right-aligned-header",
      },
      {
        headerName: "Score",
        field: "score",
        width: 90,
        sortable: true,
        cellRenderer: ScoreCellRenderer,
        cellStyle: { padding: 0, overflow: "hidden" },
      },
    ],
    [],
  );

  const defaultColDef = useMemo(
    () => ({
      resizable: true,
      suppressMovable: false,
    }),
    [],
  );

  return (
    <Box
      sx={{
        width: "100%",
        height: 600,
        borderRadius: "8px",
        overflow: "hidden",
        border: "1px solid",
        borderColor: "divider",
        "& .ag-root-wrapper": { borderRadius: "8px" },
      }}
    >
      <AgGridReact
        ref={gridRef}
        rowData={rows}
        columnDefs={colDefs}
        defaultColDef={defaultColDef}
        rowHeight={36}
        headerHeight={38}
        rowBuffer={10}
        suppressCellFocus
        suppressRowClickSelection
        onRowClicked={(e) => onRowClick?.(e.data?.id)}
        rowStyle={{ cursor: "pointer" }}
        theme={agTheme}
      />
    </Box>
  );
}
TracesGrid.propTypes = {
  rows: PropTypes.array.isRequired,
  onRowClick: PropTypes.func,
};

// ── Main TracesTab ─────────────────────────────────────────────────────────────
export default function TracesTab({ error }) {
  const clusterId = error?.cluster_id;
  /* A prototype issue's project doesn't exist on the backend — don't ask it
     for the project or its saved views (each answers 400 with a toast). */
  const projectId = isMockClusterId(clusterId) ? null : error?.project_id;
  const { data, isLoading } = useErrorFeedTraces(clusterId, { limit: 200 });
  const [drawerTraceId, setDrawerTraceId] = useState(null);
  /* Hidden while a drawer opened after it (the Fix sheet) is open. */
  const traceCovered = useDrawerCovered(!!drawerTraceId);

  // Sim/voice projects need the VAPI call drawer, not the generic trace drawer.
  const { data: projectDetail } = useGetProjectDetails(projectId, !!projectId);
  const isVoiceProject = projectDetail?.source === PROJECT_SOURCE.SIMULATOR;
  const { data: voiceCallData, isFetching: voiceLoading } = useVoiceCallDetail(
    drawerTraceId,
    isVoiceProject && !!drawerTraceId,
  );

  const agg = data?.aggregates ?? EMPTY_AGG;
  const rows = useMemo(() => data?.traces ?? [], [data]);
  const total = data?.total ?? 0;

  const traceIndex = rows.findIndex((r) => r.id === drawerTraceId);
  const handlePrev = useCallback(() => {
    if (traceIndex > 0) setDrawerTraceId(rows[traceIndex - 1].id);
  }, [traceIndex, rows]);
  const handleNext = useCallback(() => {
    if (traceIndex < rows.length - 1) setDrawerTraceId(rows[traceIndex + 1].id);
  }, [traceIndex, rows]);

  if (isLoading && !data) {
    return (
      <Stack gap={2}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(5, 1fr)",
            gap: 1,
            mb: 1.5,
          }}
        >
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton
              key={i}
              variant="rectangular"
              height={62}
              sx={{ borderRadius: "6px" }}
            />
          ))}
        </Box>
        <Skeleton
          variant="rectangular"
          height={600}
          sx={{ borderRadius: "8px" }}
        />
      </Stack>
    );
  }

  return (
    <Stack gap={2}>
      <AggregateBar agg={agg} />

      <Box>
        <TracesGrid rows={rows} onRowClick={setDrawerTraceId} />
        <Typography fontSize="12px" color="text.disabled" mt={1}>
          {isLoading
            ? "Loading traces…"
            : `Showing ${rows.length} of ${total.toLocaleString()} total traces in this issue`}
        </Typography>
      </Box>

      {isVoiceProject ? (
        // Match TraceDetailDrawerV2's overlay shape so the two drawers feel
        // identical: persistent variant (no backdrop, page stays interactive),
        // fixed right-anchored.

        <Drawer
          anchor="right"
          variant="persistent"
          open={!!drawerTraceId}
          onClose={() => setDrawerTraceId(null)}
          sx={coveredDrawerSx(traceCovered)}
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
                  ? {
                      ...voiceCallData,
                      project_id: projectId,
                      module: "project",
                    }
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
          open={!!drawerTraceId}
          onClose={() => setDrawerTraceId(null)}
          projectId={projectId}
          onPrev={handlePrev}
          onNext={handleNext}
          hasPrev={traceIndex > 0}
          hasNext={traceIndex < rows.length - 1}
        />
      )}
    </Stack>
  );
}

TracesTab.propTypes = {
  error: PropTypes.object,
};
