import React, { useState, useMemo } from "react";
import {
  Box,
  Typography,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Skeleton,
  InputAdornment,
  IconButton,
} from "@mui/material";
import Section from "src/components/section";
import { Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import ListToolbar from "src/components/list-toolbar";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useGatewayContext } from "../context/useGatewayContext";

import { useSessions, useCloseSession } from "./hooks/useSessions";
import SessionDetailDrawer from "./SessionDetailDrawer";
import { formatDateTime as formatDate } from "../utils/formatters";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
];

const SessionExplorerSection = () => {
  const [statusFilter, setStatusFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState(null);

  const { gatewayId: _gatewayId } = useGatewayContext();
  const params = {};
  if (statusFilter) params.status = statusFilter;
  if (searchQuery.trim()) params.search = searchQuery;
  const { data: sessions, isLoading, error, refetch } = useSessions(params);
  const closeMutation = useCloseSession();

  const filteredSessions = useMemo(() => {
    if (!sessions) return [];
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter(
      (s) =>
        s.session_id?.toLowerCase().includes(q) ||
        s.name?.toLowerCase().includes(q),
    );
  }, [sessions, searchQuery]);

  const handleClose = (id) => {
    closeMutation.mutate(id, {
      onSuccess: () =>
        enqueueSnackbar("Session closed", { variant: "success" }),
      onError: () =>
        enqueueSnackbar("Failed to close session", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={200} height={40} />
        </Stack>
        <Section variant="panel">
          {[...Array(5)].map((_, i) => (
            <Stack
              key={i}
              direction="row"
              spacing={2}
              sx={{
                px: 2,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Skeleton width="20%" height={20} />
              <Skeleton width="25%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="20%" height={20} />
            </Stack>
          ))}
        </Section>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load sessions: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        sx={{ mb: 1.5 }}
        icon={GATEWAY_ICONS.sessions}
        title="Sessions"
        subtitle="Browse and manage active conversation sessions"
      />

      <ListToolbar>
        <TextField
          placeholder="Search by session ID or name..."
          size="small"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{ width: 280 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifier-linear" width={16} />
              </InputAdornment>
            ),
          }}
        />
        <ToggleButtonGroup
          value={statusFilter}
          exclusive
          onChange={(_, val) => val !== null && setStatusFilter(val)}
          size="small"
        >
          {STATUS_FILTERS.map((f) => (
            <ToggleButton key={f.value} value={f.value}>
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </ListToolbar>

      {filteredSessions.length === 0 ? (
        <Box display="flex" flexDirection="column" alignItems="center" py={8}>
          <Iconify
            icon="solar:graph-new-linear"
            width={48}
            sx={{ color: "text.disabled", mb: 2 }}
          />
          <Typography variant="h6" color="text.secondary">
            {sessions?.length === 0
              ? "No sessions yet"
              : "No sessions match your filters"}
          </Typography>
          <Typography variant="body2" color="text.secondary" mt={1}>
            Sessions are automatically created when requests include a session
            ID
          </Typography>
        </Box>
      ) : (
        <>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Session ID</TableCell>
                  <TableCell>Name</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Requests</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    Actions
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredSessions.map((session) => (
                  <TableRow
                    key={session.id}
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => setSelectedSessionId(session.id)}
                  >
                    <TableCell>
                      <Tooltip title={`Click to copy: ${session.session_id}`}>
                        <Typography
                          variant="body2"
                          sx={{
                            fontFamily: "monospace",
                            cursor: "pointer",
                            "&:hover": { color: "primary.main" },
                          }}
                          fontWeight={500}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard.writeText(session.session_id);
                            enqueueSnackbar("Session ID copied", {
                              variant: "info",
                            });
                          }}
                        >
                          {session.session_id?.substring(0, 16)}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {session.name || "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        status={session.status}
                        label={session.status}
                        role={
                          session.status === "active" ? "success" : "neutral"
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {session.stats?.request_count ??
                          session.request_count ??
                          "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {formatDate(session.created_at)}
                      </Typography>
                    </TableCell>
                    <TableCell
                      sx={{ fontVariantNumeric: "tabular-nums" }}
                      align="right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {session.status === "active" && (
                        <IconButton
                          className="row-hover-action"
                          size="small"
                          title="Close session"
                          onClick={() => handleClose(session.id)}
                          disabled={closeMutation.isPending}
                        >
                          <Iconify icon="solar:stop-circle-linear" width={20} />
                        </IconButton>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          <Typography variant="body2" color="text.secondary" mt={2}>
            Showing {filteredSessions.length} session
            {filteredSessions.length !== 1 ? "s" : ""}
          </Typography>
        </>
      )}

      <SessionDetailDrawer
        sessionId={selectedSessionId}
        open={Boolean(selectedSessionId)}
        onClose={() => setSelectedSessionId(null)}
      />
    </Box>
  );
};

export default SessionExplorerSection;
