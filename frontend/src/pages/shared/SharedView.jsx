import React, { useState, useCallback, useMemo, useEffect } from "react";
import { useParams } from "react-router";
import { Alert, Box, CircularProgress, Typography, Card, CardContent, Stack, Tooltip } from "@mui/material";
import { Helmet } from "react-helmet-async";
import { useResolveSharedLink } from "src/api/shared-links";
import DrawerToolbar from "src/components/traceDetail/DrawerToolbar";
import TraceTreeV2 from "src/components/traceDetail/TraceTreeV2";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import SharedVoiceView from "./SharedVoiceView";
import { isVoiceCall } from "./sharedViewHelpers";
import WidgetChart from "src/sections/dashboards/WidgetChart";
import axios from "src/utils/axios";

function getSpan(entry) {
  return entry?.observation_span || entry?.observationSpan || {};
}

export default function SharedView() {
  const { token } = useParams();
  const [selectedSpanId, setSelectedSpanId] = useState(null);
  const [leftPanelWidth, setLeftPanelWidth] = useState(35);

  // Resolve token → resource metadata
  const {
    data: shared,
    isLoading: resolving,
    isError,
    error,
  } = useResolveSharedLink(token);

  const resourceType = shared?.resourceType || shared?.resource_type;
  const resourceId = shared?.resourceId || shared?.resource_id;
  const resourceData = shared?.data;

  const isTrace = resourceType === "trace";
  // Voice calls are stored server-side as traces, so the resource_type is
  // still "trace" — we dispatch on the actual payload shape (presence of a
  // conversation-type span or top-level voice fields).
  const isVoice = useMemo(
    () => isTrace && isVoiceCall(resourceData),
    [isTrace, resourceData],
  );

  // For traces, the resolve endpoint returns full span tree in data
  const spans =
    resourceData?.observationSpans || resourceData?.observation_spans;
  const summary = resourceData?.summary;

  const selectedSpanData = useMemo(() => {
    if (!selectedSpanId || !spans) return null;
    function find(entries) {
      for (const entry of entries) {
        const span = getSpan(entry);
        if (span?.id === selectedSpanId) return entry;
        if (entry.children?.length) {
          const found = find(entry.children);
          if (found) return found;
        }
      }
      return null;
    }
    return find(spans);
  }, [selectedSpanId, spans]);

  const handleSelectSpan = useCallback((spanId) => {
    setSelectedSpanId((prev) => (prev === spanId ? null : spanId));
  }, []);

  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const container = e.target.closest("[data-shared-content]");
      if (!container) return;
      const containerWidth = container.offsetWidth;
      const onMouseMove = (moveEvent) => {
        const diff = moveEvent.clientX - startX;
        setLeftPanelWidth(
          Math.min(
            70,
            Math.max(20, startWidth + (diff / containerWidth) * 100),
          ),
        );
      };
      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
      };
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    },
    [leftPanelWidth],
  );

  const isLoading = resolving;

  // Error states
  if (isError) {
    const status = error?.response?.status;
    const msg = error?.response?.data?.error;
    return (
      <>
        <Helmet>
          <title>Shared Link</title>
        </Helmet>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            bgcolor: "background.default",
          }}
        >
          <Box sx={{ textAlign: "center", maxWidth: 420, p: 4 }}>
            <Iconify
              icon={
                status === 401
                  ? "mdi:lock-outline"
                  : status === 403
                    ? "mdi:shield-lock-outline"
                    : "mdi:link-off"
              }
              width={48}
              sx={{ color: "text.disabled", mb: 2 }}
            />
            <Typography variant="h6" sx={{ mb: 1, color: "text.primary" }}>
              {status === 401 && "Sign in required"}
              {status === 403 && "Access denied"}
              {status === 410 && "Link expired"}
              {status === 404 && "Link not found"}
              {![401, 403, 404, 410].includes(status) && "Something went wrong"}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {msg ||
                "This shared link may have been revoked or is no longer available."}
            </Typography>
            {status === 401 && (
              <Typography
                component="a"
                href={`/auth/jwt/login?returnTo=${encodeURIComponent(window.location.pathname)}`}
                sx={{
                  display: "inline-block",
                  mt: 2,
                  color: "primary.main",
                  fontSize: 14,
                  fontWeight: 500,
                }}
              >
                Sign in to continue
              </Typography>
            )}
          </Box>
        </Box>
      </>
    );
  }

  return (
    <>
      <Helmet>
        <title>
          {isVoice
            ? `Shared Voice Call — ${resourceId?.substring(0, 8) || "..."}`
            : isTrace
              ? `Shared Trace — ${resourceId?.substring(0, 8) || "..."}`
              : "Shared Resource"}
        </title>
      </Helmet>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          bgcolor: "background.paper",
        }}
      >
        {/* Header bar */}
        <Box
          sx={{
            px: 2,
            py: 1.5,
            bgcolor: "background.default",
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Iconify
              icon={isVoice ? "mdi:phone-outline" : "mdi:share-variant-outline"}
              width={20}
              sx={{ color: "primary.main" }}
            />
            <Typography
              sx={{ fontSize: 14, fontWeight: 600, color: "text.primary" }}
            >
              {isVoice
                ? "Shared voice call"
                : `Shared ${resourceType || "resource"}`}
            </Typography>
            {resourceId && (
              <Typography
                sx={{
                  fontSize: 12,
                  fontFamily: "monospace",
                  color: "text.disabled",
                }}
              >
                {resourceId.substring(0, 12)}...
              </Typography>
            )}
          </Box>
          <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
            View only
          </Typography>
        </Box>

        {/* Content */}
        {isLoading ? (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={32} />
          </Box>
        ) : isVoice ? (
          /* Voice call view — transcript + audio player, read-only */
          <SharedVoiceView resourceData={resourceData} />
        ) : isTrace ? (
          /* Trace view */
          <Box
            data-shared-content
            sx={{ flex: 1, display: "flex", overflow: "hidden" }}
          >
            {/* Left: Tree */}
            <Box
              sx={{
                width: `${leftPanelWidth}%`,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                borderRight: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              <Box sx={{ flex: 1, overflow: "auto" }}>
                <TraceTreeV2
                  spans={spans}
                  selectedSpanId={selectedSpanId}
                  onSelectSpan={handleSelectSpan}
                />
              </Box>
            </Box>

            {/* Divider */}
            <Box
              onMouseDown={handleDragStart}
              sx={{
                width: 8,
                cursor: "col-resize",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                "&:hover .dots": { opacity: 1 },
              }}
            >
              <Box
                className="dots"
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "3px",
                  opacity: 0.4,
                  transition: "opacity 150ms",
                }}
              >
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Box
                    key={i}
                    sx={{
                      width: 3,
                      height: 3,
                      borderRadius: "50%",
                      bgcolor: "text.disabled",
                    }}
                  />
                ))}
              </Box>
            </Box>

            {/* Right: Span detail */}
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {selectedSpanData ? (
                <SpanDetailPane
                  entry={selectedSpanData}
                  onClose={() => setSelectedSpanId(null)}
                />
              ) : (
                <Box
                  sx={{
                    p: 3,
                    textAlign: "center",
                    color: "text.secondary",
                    mt: 8,
                  }}
                >
                  <Iconify
                    icon="mdi:cursor-default-click-outline"
                    width={40}
                    sx={{ mb: 1, opacity: 0.5 }}
                  />
                  <Typography variant="body2" fontSize={13}>
                    Select a span to view details
                  </Typography>
                  {summary && (
                    <Box
                      sx={{
                        mt: 2,
                        display: "flex",
                        justifyContent: "center",
                        gap: 3,
                      }}
                    >
                      <Typography variant="caption">
                        {summary.total_spans || summary.totalSpans} spans
                      </Typography>
                      <Typography variant="caption">
                        {formatLatency(
                          summary.total_duration_ms || summary.totalDurationMs,
                        )}
                      </Typography>
                      <Typography variant="caption">
                        {formatTokenCount(
                          summary.total_tokens || summary.totalTokens,
                        )}{" "}
                        tokens
                      </Typography>
                      <Typography variant="caption">
                        {formatCost(summary.total_cost || summary.totalCost)}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          </Box>
        ) : resourceType === "dashboard" ? (
          /* Shared dashboard view — render widgets in read-only mode */
          <SharedDashboardView sharedData={shared?.data} token={token} />
        ) : (
          /* Non-trace, non-dashboard resource — show raw data */
          <Box sx={{ flex: 1, p: 3, overflow: "auto" }}>
            <Alert severity="info" sx={{ mb: 2 }}>
              Viewing shared {resourceType}
            </Alert>
            <pre
              style={{
                fontSize: 12,
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(shared?.data, null, 2)}
            </pre>
          </Box>
        )}
      </Box>
    </>
  );
}

// ---------------------------------------------------------------------------
// SharedDashboardView — read-only widget grid for shared dashboard links
// ---------------------------------------------------------------------------

const DEFAULT_WIDGET_HEIGHT = 320;

function computeRows(widgets) {
  const rows = [];
  let currentRow = [];
  let currentWidth = 0;
  for (const w of widgets) {
    const width = w.width || 12;
    if (currentWidth + width > 12 && currentRow.length > 0) {
      rows.push(currentRow);
      currentRow = [];
      currentWidth = 0;
    }
    currentRow.push(w);
    currentWidth += width;
    if (currentWidth >= 12) {
      rows.push(currentRow);
      currentRow = [];
      currentWidth = 0;
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);
  return rows;
}

function SharedWidgetCard({ widget, globalDateRange }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    // We don't have the token here directly — it's in the parent's shared link
    // The parent fetches data and passes it down
    if (widget._queryData) {
      setData(widget._queryData);
      setLoading(false);
    }
  }, [widget._queryData]);

  const chartConfig = widget.chart_config || {};
  const chartType = chartConfig.chart_type || "line";
  const isPie = chartType === "pie";
  const isTable = chartType === "table";
  const isMetricCard = chartType === "metric";

  return (
    <Card
      sx={{
        width: `${(widget.width || 12) / 12 * 100}%`,
        height: widget.height ? `${widget.height * 80}px` : DEFAULT_WIDGET_HEIGHT,
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
      }}
    >
      <CardContent sx={{ flex: 1, display: "flex", flexDirection: "column", p: 1.5, pb: 1 }}>
        <Typography
          variant="subtitle2"
          sx={{ mb: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {widget.name}
        </Typography>
        {loading ? (
          <Box sx={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <CircularProgress size={24} />
          </Box>
        ) : error ? (
          <Box sx={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Typography variant="caption" color="error">
              Failed to load data
            </Typography>
          </Box>
        ) : isMetricCard && data?.data?.result ? (
          <Box sx={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Typography variant="h3" fontWeight={700}>
              {data.data.result.value ?? "—"}
            </Typography>
          </Box>
        ) : (
          <Box sx={{ flex: 1, minHeight: 0 }}>
            <WidgetChart widget={widget} globalDateRange={globalDateRange} />
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

function SharedDashboardView({ sharedData, token }) {
  const widgets = useMemo(
    () =>
      (sharedData?.widgets || [])
        .slice()
        .sort((a, b) => a.position - b.position),
    [sharedData?.widgets],
  );

  const rows = useMemo(() => computeRows(widgets), [widgets]);

  // Fetch query data for all widgets
  const [widgetData, setWidgetData] = useState({});

  useEffect(() => {
    const fetchAll = async () => {
      const results = {};
      for (const w of widgets) {
        try {
          const res = await axios.post(`/tracer/shared/${token}/widget-query/`, {
            widget_id: w.id,
          });
          results[w.id] = res.data;
        } catch {
          results[w.id] = null;
        }
      }
      setWidgetData(results);
    };
    if (token && widgets.length > 0) fetchAll();
  }, [token, widgets]);

  // Attach query data to widgets
  const widgetsWithData = useMemo(
    () => widgets.map((w) => ({ ...w, _queryData: widgetData[w.id] })),
    [widgets, widgetData],
  );

  if (widgets.length === 0) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 2,
        }}
      >
        <Iconify icon="mdi:chart-line" width={64} sx={{ color: "text.disabled" }} />
        <Typography variant="h6" color="text.secondary">
          No widgets yet
        </Typography>
        <Typography variant="body2" color="text.disabled">
          This shared dashboard has no widgets
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ flex: 1, overflow: "auto", p: 3 }}>
      <Typography variant="h5" sx={{ mb: 2, fontWeight: 700 }}>
        {sharedData?.name || "Untitled Dashboard"}
      </Typography>
      <Stack spacing={2}>
        {rows.map((row, rowIdx) => {
          const rowHeight =
            row.length > 1
              ? Math.max(...row.map((w) => (w.height && w.height > 50 ? w.height : DEFAULT_WIDGET_HEIGHT)))
              : undefined;

          return (
            <Box
              key={rowIdx}
              sx={{
                display: "flex",
                gap: 2,
                height: rowHeight ? `${rowHeight * 80}px` : undefined,
              }}
            >
              {row.map((widget) => (
                <SharedWidgetCard
                  key={widget.id}
                  widget={widgetsWithData.find((w) => w.id === widget.id)}
                  globalDateRange={null}
                />
              ))}
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}
