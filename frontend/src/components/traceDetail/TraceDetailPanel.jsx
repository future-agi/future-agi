import React, { useState, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import {
  formatLatency,
  formatCost,
  formatTokenCount,
} from "src/sections/projects/LLMTracing/formatters";
import SpanTreeTimeline from "./SpanTreeTimeline";
import SpanDetailPane from "./SpanDetailPane";

const PANEL_WIDTH = "60vw";

const TraceDetailPanel = ({ traceId, open, onClose, projectId }) => {
  const [selectedSpanId, setSelectedSpanId] = useState(null);

  const { data, isLoading } = useGetTraceDetail(
    open ? traceId : null,
    projectId,
  );

  const spans = data?.observation_spans;
  const summary = data?.summary;

  // Find selected span data from the tree
  const selectedSpanData = useMemo(() => {
    if (!selectedSpanId || !spans) return null;
    function find(entries) {
      for (const entry of entries) {
        const span = entry.observation_span;
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

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          width: PANEL_WIDTH,
          height: "100vh",
          position: "fixed",
          borderRadius: 0,
          bgcolor: "background.paper",
          display: "flex",
          flexDirection: "column",
          borderLeft: "1px solid",
          borderColor: "divider",
        },
      }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      {/* Header */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            variant="body2"
            sx={{ fontSize: 13, color: "text.secondary" }}
          >
            Trace ID :
          </Typography>
          <Typography
            variant="body2"
            sx={{ fontSize: 13, fontWeight: 500, fontFamily: "monospace" }}
          >
            {traceId?.substring(0, 20)}...
          </Typography>
          <IconButton
            size="small"
            onClick={() => navigator.clipboard.writeText(traceId)}
            sx={{ p: 0.25 }}
          >
            <Iconify icon="mdi:content-copy" width={14} />
          </IconButton>
        </Stack>

        <Stack direction="row" spacing={0.5}>
          <IconButton size="small">
            <Iconify icon="mdi:printer-outline" width={18} />
          </IconButton>
          <IconButton size="small">
            <Iconify icon="mdi:download-outline" width={18} />
          </IconButton>
          <IconButton size="small">
            <Iconify icon="mdi:share-outline" width={18} />
          </IconButton>
          <IconButton size="small">
            <Iconify icon="mdi:arrow-expand" width={18} />
          </IconButton>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
      </Stack>

      {/* Tab bar + summary */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          px: 2,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify icon="mdi:link-variant" width={16} color="primary.main" />
          <Typography
            variant="body2"
            sx={{ fontSize: 13, fontWeight: 600, color: "primary.main" }}
          >
            Trace
          </Typography>
        </Stack>

        {summary && (
          <Stack direction="row" spacing={2}>
            <Typography variant="caption" color="text.secondary">
              {summary.totalSpans || summary.total_spans} spans
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {formatLatency(summary.total_duration_ms)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {formatTokenCount(summary.total_tokens)} tokens
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {formatCost(summary.total_cost)}
            </Typography>
          </Stack>
        )}
      </Stack>

      {/* Search bar */}
      <Box
        sx={{
          px: 2,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.5,
            py: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Iconify icon="mdi:magnify" width={16} color="text.disabled" />
          <Typography
            variant="body2"
            sx={{ fontSize: 13, color: "text.disabled" }}
          >
            Search
          </Typography>
        </Box>
      </Box>

      {/* Main content */}
      <Box sx={{ flex: 1, display: "flex", overflow: "hidden" }}>
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
        ) : (
          <>
            {/* Span Tree + Timeline */}
            <Box
              sx={{
                flex: selectedSpanData ? "0 0 50%" : 1,
                overflow: "auto",
                borderRight: selectedSpanData ? "1px solid" : "none",
                borderColor: "divider",
                transition: "flex 200ms ease",
              }}
            >
              <SpanTreeTimeline
                spans={spans}
                selectedSpanId={selectedSpanId}
                onSelectSpan={handleSelectSpan}
              />
            </Box>

            {/* Span Detail Pane */}
            {selectedSpanData && (
              <Box sx={{ flex: "0 0 50%", overflow: "auto" }}>
                <SpanDetailPane
                  entry={selectedSpanData}
                  projectId={projectId}
                  onClose={() => setSelectedSpanId(null)}
                />
              </Box>
            )}
          </>
        )}
      </Box>
    </Drawer>
  );
};

TraceDetailPanel.propTypes = {
  traceId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectId: PropTypes.string,
};

export default React.memo(TraceDetailPanel);
