import React, { useCallback, useMemo, useRef, useState } from "react";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { AgGridReact } from "ag-grid-react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import { useErrorFeedTraces } from "src/api/errorFeed/error-feed";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";

const EMPTY_AGG = {
  totalTraces: 0,
  avgScore: 0,
  avgTurns: 0,
  p50Latency: 0,
  p95Latency: 0,
};

// ── Aggregate bar (no Failing / Passing) ────────────────────────────────────
function AggregateBar({ agg }) {
  const items = [
    { label: "Total traces", value: agg.totalTraces.toLocaleString() },
    { label: "Avg score", value: agg.avgScore.toFixed(2) },
    { label: "Avg turns", value: agg.avgTurns.toFixed(1) },
    { label: "P50 latency", value: `${(agg.p50Latency / 1000).toFixed(1)}s` },
    { label: "P95 latency", value: `${(agg.p95Latency / 1000).toFixed(1)}s` },
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
          <Typography fontSize="17px" fontWeight={700} color="text.primary">
            {item.value}
          </Typography>
          <Typography fontSize="10px" color="text.disabled" mt={0.25}>
            {item.label}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
AggregateBar.propTypes = { agg: PropTypes.object.isRequired };

// ── Score cell renderer — full-cell fill, green ≥ 70%, red < 70% ─────────────
function scoreColors(pct) {
  if (pct >= 70)
    return { backgroundColor: "rgba(90,206,109,0.12)", color: "#3a9e50" };
  if (pct >= 50)
    return { backgroundColor: "rgba(245,166,35,0.12)", color: "#c47d00" };
  return { backgroundColor: "rgba(219,47,45,0.10)", color: "#c0392b" };
}

function ScoreCellRenderer({ value }) {
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
  const { backgroundColor, color } = scoreColors(pct);
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
        color,
      }}
    >
      {pct}%
    </div>
  );
}
ScoreCellRenderer.propTypes = { value: PropTypes.number };

// ── Traces AG Grid ────────────────────────────────────────────────────────────
function TracesGrid({ rows, onRowClick }) {
  const agTheme = useAgTheme();
  const gridRef = useRef(null);

  const colDefs = useMemo(
    () => [
      {
        headerName: "Trace ID",
        field: "id",
        width: 155,
        sortable: true,
        cellStyle: { fontSize: "11px" },
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
        cellStyle: { fontSize: "11px" },
      },
      {
        headerName: "Duration",
        field: "latencyMs",
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
        rowHeight={40}
        headerHeight={38}
        rowBuffer={10}
        suppressCellFocus
        suppressRowClickSelection
        onRowClicked={(e) => onRowClick?.(e.data?.id)}
        rowStyle={{ cursor: "pointer" }}
        theme={agTheme.withParams({
          wrapperBorder: { width: 0 },
          wrapperBorderRadius: 4,
        })}
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
  const clusterId = error?.clusterId;
  const projectId = error?.projectId;
  const { data, isLoading } = useErrorFeedTraces(clusterId, { limit: 200 });
  const [drawerTraceId, setDrawerTraceId] = useState(null);

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
        <Typography fontSize="11px" color="text.disabled" mt={1}>
          {isLoading
            ? "Loading traces…"
            : `Showing ${rows.length} of ${total.toLocaleString()} total traces in this cluster`}
        </Typography>
      </Box>

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
    </Stack>
  );
}

TracesTab.propTypes = {
  error: PropTypes.object,
};
