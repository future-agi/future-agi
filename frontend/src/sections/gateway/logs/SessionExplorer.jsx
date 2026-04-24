import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Card,
  Stack,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TablePagination,
  ToggleButton,
  ToggleButtonGroup,
  CircularProgress,
  Skeleton,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import useSessions from "./hooks/useSessions";
import { formatCost } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE_OPTIONS = [10, 25, 50];

const SORT_OPTIONS = [
  { value: "newest", label: "Newest" },
  { value: "most_requests", label: "Most Requests" },
  { value: "highest_cost", label: "Highest Cost" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(iso) {
  if (!iso) return "N/A";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return "N/A";
  }
}

function getStatusChipColor(code) {
  if (code >= 200 && code < 300) return "success";
  if (code >= 400 && code < 500) return "warning";
  if (code >= 500 && code < 600) return "error";
  return "default";
}

function getLatencyColor(ms) {
  if (ms < 500) return "success.main";
  if (ms <= 2000) return "warning.main";
  return "error.main";
}

// ---------------------------------------------------------------------------
// Expanded session detail (lazy-loaded requests)
// ---------------------------------------------------------------------------

SessionDetail.propTypes = {
  sessionId: PropTypes.string.isRequired,
  onRequestClick: PropTypes.func.isRequired,
};

function SessionDetail({ sessionId, onRequestClick }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agentcc-session-detail", sessionId],
    queryFn: async () => {
      const res = await axiosInstance.get(
        endpoints.gateway.requestLogSessionDetail(sessionId),
      );
      return res.data;
    },
    enabled: Boolean(sessionId),
    staleTime: 60000,
  });

  const requests = data?.result?.results ?? data?.results ?? [];

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" py={3}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (requests.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" py={2} px={1}>
        No requests found for this session.
      </Typography>
    );
  }

  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell sx={{ fontWeight: 600 }}>Timestamp</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>Request ID</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>Model</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>Latency</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>Cost</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {requests.map((req) => (
          <TableRow
            key={req.id}
            hover
            sx={{ cursor: "pointer" }}
            onClick={() => onRequestClick(req.id)}
          >
            <TableCell sx={{ whiteSpace: "nowrap" }}>
              {formatTimestamp(req.startedAt)}
            </TableCell>
            <TableCell>
              <Tooltip title={req.requestId || req.id} placement="top" arrow>
                <Typography variant="body2" noWrap sx={{ maxWidth: 120 }}>
                  {(req.requestId || req.id || "").slice(0, 12)}...
                </Typography>
              </Tooltip>
            </TableCell>
            <TableCell>{req.model || "-"}</TableCell>
            <TableCell>
              <Chip
                label={req.status_code ?? "-"}
                size="small"
                variant="outlined"
                color={getStatusChipColor(req.status_code)}
              />
            </TableCell>
            <TableCell>
              <Typography
                variant="body2"
                sx={{
                  color:
                    req.latencyMs != null
                      ? getLatencyColor(req.latencyMs)
                      : undefined,
                }}
              >
                {req.latencyMs != null ? `${req.latencyMs}ms` : "-"}
              </Typography>
            </TableCell>
            <TableCell>{formatCost(req.cost)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const SessionExplorer = ({ filters, onRequestClick }) => {
  const [sortBy, setSortBy] = useState("newest");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [expandedSession, setExpandedSession] = useState(null);

  // Map the UI sort value to API ordering param
  const orderingMap = {
    newest: "-last_request_at",
    most_requests: "-request_count",
    highest_cost: "-total_cost",
  };

  const { data, isLoading } = useSessions({
    ...filters,
    page: String(page),
    pageSize: String(pageSize),
    sessionSort: orderingMap[sortBy],
  });

  const sessions = data?.result?.results ?? data?.results ?? [];
  const totalCount = data?.result?.count ?? data?.count ?? 0;

  // --- Handlers -------------------------------------------------------------
  const handleSortChange = useCallback((_event, newValue) => {
    if (newValue) {
      setSortBy(newValue);
      setPage(1);
    }
  }, []);

  const handleAccordionChange = useCallback(
    (sessionId) => (_event, isExpanded) => {
      setExpandedSession(isExpanded ? sessionId : null);
    },
    [],
  );

  const handlePageChange = useCallback((_event, newPage) => {
    setPage(newPage + 1);
  }, []);

  const handleRowsPerPageChange = useCallback((event) => {
    setPageSize(parseInt(event.target.value, 10));
    setPage(1);
  }, []);

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <Card>
      {/* ---- Sort controls ---- */}
      <Stack
        direction="row"
        spacing={2}
        p={2}
        borderBottom={1}
        borderColor="divider"
        alignItems="center"
      >
        <Typography variant="body2" color="text.secondary">
          Sort by:
        </Typography>
        <ToggleButtonGroup
          value={sortBy}
          exclusive
          onChange={handleSortChange}
          size="small"
        >
          {SORT_OPTIONS.map((opt) => (
            <ToggleButton key={opt.value} value={opt.value}>
              {opt.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      {/* ---- Loading skeletons ---- */}
      {isLoading && (
        <Stack spacing={1} p={2}>
          {Array.from({ length: 5 }).map((_, idx) => (
            <Box key={idx}>
              <Skeleton
                variant="rectangular"
                height={56}
                sx={{ borderRadius: 1 }}
              />
            </Box>
          ))}
        </Stack>
      )}

      {/* ---- Session list ---- */}
      {!isLoading && sessions.length > 0 && (
        <Box>
          {sessions.map((session) => (
            <Accordion
              key={session.session_id}
              expanded={expandedSession === session.session_id}
              onChange={handleAccordionChange(session.session_id)}
              disableGutters
              sx={{
                "&:before": { display: "none" },
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <AccordionSummary
                expandIcon={<Iconify icon="mdi:chevron-down" width={20} />}
              >
                <Stack
                  direction="row"
                  spacing={2}
                  alignItems="center"
                  width="100%"
                  sx={{ overflow: "hidden", pr: 1 }}
                >
                  <Tooltip title={session.session_id} placement="top" arrow>
                    <Typography
                      variant="subtitle2"
                      noWrap
                      sx={{ minWidth: 140 }}
                    >
                      {(session.session_id || "").slice(0, 16)}
                      {(session.session_id || "").length > 16 ? "..." : ""}
                    </Typography>
                  </Tooltip>

                  <Chip
                    label={`${session.request_count} requests`}
                    size="small"
                    variant="outlined"
                  />

                  <Typography
                    variant="body2"
                    color="text.secondary"
                    noWrap
                    sx={{ maxWidth: 200 }}
                  >
                    {(session.models || []).join(", ") || "-"}
                  </Typography>

                  <Typography variant="body2" noWrap>
                    {session.total_tokens?.toLocaleString() ?? 0} tokens
                  </Typography>

                  <Typography variant="body2" noWrap>
                    {formatCost(session.total_cost)}
                  </Typography>

                  <Typography
                    variant="caption"
                    color="text.secondary"
                    noWrap
                    sx={{ ml: "auto" }}
                  >
                    {formatTimestamp(session.firstRequestAt)} -{" "}
                    {formatTimestamp(session.lastRequestAt)}
                  </Typography>

                  {session.errorCount > 0 && (
                    <Chip
                      label={`${session.errorCount} errors`}
                      size="small"
                      color="error"
                      variant="outlined"
                    />
                  )}
                </Stack>
              </AccordionSummary>

              <AccordionDetails sx={{ p: 0 }}>
                {expandedSession === session.session_id && (
                  <SessionDetail
                    sessionId={session.session_id}
                    onRequestClick={onRequestClick}
                  />
                )}
              </AccordionDetails>
            </Accordion>
          ))}
        </Box>
      )}

      {/* ---- Empty state ---- */}
      {!isLoading && sessions.length === 0 && (
        <Stack alignItems="center" spacing={1.5} py={6}>
          <Iconify
            icon="mdi:group"
            width={48}
            sx={{ color: "text.disabled" }}
          />
          <Typography variant="h6" color="text.secondary">
            No sessions found
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            textAlign="center"
            maxWidth={400}
          >
            Requests with session IDs appear here. Check that your gateway is
            configured to capture session IDs.
          </Typography>
        </Stack>
      )}

      {/* ---- Pagination ---- */}
      <TablePagination
        component="div"
        count={totalCount}
        page={page - 1}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZE_OPTIONS}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
      />
    </Card>
  );
};

SessionExplorer.propTypes = {
  filters: PropTypes.object.isRequired,
  onRequestClick: PropTypes.func.isRequired,
};

export default SessionExplorer;
