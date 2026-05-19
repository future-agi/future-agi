/* eslint-disable react/prop-types */
import React, { useState, useCallback, useMemo } from "react";
import { useParams } from "react-router";
import { Alert, Box, CircularProgress, Typography } from "@mui/material";
import { Helmet } from "react-helmet-async";
import {
  useResolveSharedLink,
  useResolveSharedWidgetData,
} from "src/api/shared-links";
import WidgetChart from "src/sections/dashboards/WidgetChart";
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

function getSpan(entry) {
  return entry?.observation_span || entry?.observationSpan || {};
}

/**
 * A single widget inside a shared dashboard. Fetches its data through the
 * public, share-token-authorized endpoint (no workspace auth) and renders it
 * with the same WidgetChart component the authenticated dashboard uses.
 */
function SharedWidget({ token, widget }) {
  const { data, isLoading, isError } = useResolveSharedWidgetData(
    token,
    widget.id,
  );

  return (
    <Box
      sx={{
        gridColumn: `span ${Math.min(12, Math.max(1, widget.width || 12))}`,
        minHeight: 220,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Typography
        sx={{ fontSize: 13, fontWeight: 600, color: "text.primary", mb: 0.5 }}
      >
        {widget.name || "Untitled widget"}
      </Typography>
      {widget.description && (
        <Typography sx={{ fontSize: 11, color: "text.disabled", mb: 1 }}>
          {widget.description}
        </Typography>
      )}
      <Box sx={{ flex: 1, minHeight: 160 }}>
        <WidgetChart
          widget={widget}
          sharedData={{ result: data?.result ?? data, isLoading, isError }}
        />
      </Box>
    </Box>
  );
}

/**
 * Read-only dashboard view for shared links. Renders the dashboard's widgets
 * with live data — each widget loads through the share-token-authorized
 * endpoint, so public recipients see real charts without an auth wall.
 */
function SharedDashboardView({ token, dashboard }) {
  const sortedWidgets = useMemo(() => {
    const widgets = dashboard?.widgets || [];
    return [...widgets].sort((a, b) => (a.position || 0) - (b.position || 0));
  }, [dashboard?.widgets]);
  const signInHref = `/auth/jwt/login?returnTo=${encodeURIComponent(
    window.location.pathname,
  )}`;

  return (
    <Box sx={{ flex: 1, p: 3, overflow: "auto" }}>
      <Box sx={{ mb: 3 }}>
        <Typography
          sx={{ fontSize: 20, fontWeight: 600, color: "text.primary", mb: 0.5 }}
        >
          {dashboard?.name || "Untitled dashboard"}
        </Typography>
        {dashboard?.description && (
          <Typography sx={{ fontSize: 13, color: "text.secondary" }}>
            {dashboard.description}
          </Typography>
        )}
      </Box>

      <Alert severity="info" sx={{ mb: 3 }}>
        Read-only view —{" "}
        <Box
          component="a"
          href={signInHref}
          sx={{
            color: "inherit",
            fontWeight: 600,
            textDecoration: "underline",
          }}
        >
          sign in
        </Box>{" "}
        to open this dashboard in your workspace.
      </Alert>

      {sortedWidgets.length === 0 ? (
        <Box sx={{ textAlign: "center", py: 6, color: "text.disabled" }}>
          <Iconify icon="mdi:view-dashboard-outline" width={48} sx={{ mb: 1 }} />
          <Typography sx={{ fontSize: 13 }}>
            No widgets in this dashboard.
          </Typography>
        </Box>
      ) : (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(12, 1fr)",
            gap: 2,
          }}
        >
          {sortedWidgets.map((w) => (
            <SharedWidget key={w.id} token={token} widget={w} />
          ))}
        </Box>
      )}
    </Box>
  );
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
          /* Dashboard view — read-only, widgets load live via share token */
          <SharedDashboardView token={token} dashboard={resourceData} />
        ) : (
          /* Other resource types — fall back to raw data dump */
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
