import React from "react";
import {
  Drawer,
  Box,
  Typography,
  Stack,
  Chip,
  Divider,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Skeleton,
  Alert,
  Card,
  CardContent,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useSessionDetail, useSessionRequests } from "./hooks/useSessions";
import { GatewayMetadataJson } from "../components/GatewayMetadata";
import { hasGatewayMetadata } from "../utils/metadataDisplay";
import {
  formatDateTime as formatDate,
  formatCost,
  formatTokens,
} from "../utils/formatters";

function formatMs(val) {
  if (val == null) return "\u2014";
  return `${Number(val).toFixed(0)}ms`;
}

const SessionDetailDrawer = ({ sessionId, open, onClose }) => {
  const { data: session, isLoading: sessionLoading } =
    useSessionDetail(sessionId);
  const { data: requests, isLoading: requestsLoading } =
    useSessionRequests(sessionId);

  const stats = session?.stats || {};

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: 640 } }}
    >
      <Box sx={{ p: 3 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="h5">Session Detail</Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="mdi:close" width={24} />
          </IconButton>
        </Stack>

        {sessionLoading ? (
          <Stack spacing={1}>
            <Skeleton width="60%" height={30} />
            <Skeleton width="40%" height={24} />
            <Skeleton width="100%" height={100} />
          </Stack>
        ) : !session ? (
          <Alert severity="warning">Session not found</Alert>
        ) : (
          <>
            <Stack spacing={1} mb={3}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h6">
                  {session.name || session.session_id}
                </Typography>
                <Chip
                  label={session.status}
                  color={session.status === "active" ? "success" : "default"}
                  size="small"
                />
              </Stack>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ fontFamily: "monospace" }}
              >
                ID: {session.session_id}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Created: {formatDate(session.created_at)}
              </Typography>
            </Stack>

            <Stack direction="row" spacing={2} mb={3}>
              <Card sx={{ flex: 1 }}>
                <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                  <Typography variant="caption" color="text.secondary">
                    Requests
                  </Typography>
                  <Typography variant="h6">
                    {stats.request_count ?? 0}
                  </Typography>
                </CardContent>
              </Card>
              <Card sx={{ flex: 1 }}>
                <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                  <Typography variant="caption" color="text.secondary">
                    Total Cost
                  </Typography>
                  <Typography variant="h6">
                    {formatCost(stats.total_cost)}
                  </Typography>
                </CardContent>
              </Card>
              <Card sx={{ flex: 1 }}>
                <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                  <Typography variant="caption" color="text.secondary">
                    Tokens
                  </Typography>
                  <Typography variant="h6">
                    {formatTokens(stats.total_tokens)}
                  </Typography>
                </CardContent>
              </Card>
              <Card sx={{ flex: 1 }}>
                <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                  <Typography variant="caption" color="text.secondary">
                    Avg Latency
                  </Typography>
                  <Typography variant="h6">
                    {formatMs(stats.avg_latency_ms)}
                  </Typography>
                </CardContent>
              </Card>
            </Stack>

            {hasGatewayMetadata(session.metadata) && (
              <Box mb={3}>
                <Typography variant="subtitle2" mb={1}>
                  Metadata
                </Typography>
                <Card variant="outlined" sx={{ p: 1.5 }}>
                  <Typography
                    variant="body2"
                    component="pre"
                    sx={{
                      fontFamily: "monospace",
                      fontSize: "0.75rem",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    <GatewayMetadataJson metadata={session.metadata} />
                  </Typography>
                </Card>
              </Box>
            )}

            <Divider sx={{ mb: 2 }} />

            <Typography variant="subtitle1" mb={2}>
              Requests ({requests?.length || 0})
            </Typography>

            {requestsLoading ? (
              <Stack spacing={0.5}>
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} width="100%" height={36} />
                ))}
              </Stack>
            ) : !requests?.length ? (
              <Typography variant="body2" color="text.secondary">
                No requests in this session
              </Typography>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Model</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Latency</TableCell>
                      <TableCell>Tokens</TableCell>
                      <TableCell>Time</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {requests.map((req) => (
                      <TableRow key={req.id} hover>
                        <TableCell>
                          <Typography variant="body2" fontWeight={500}>
                            {req.model || "\u2014"}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={req.status_code || "\u2014"}
                            color={req.isError ? "error" : "success"}
                            size="small"
                          />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatMs(req.latencyMs)}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatTokens(req.total_tokens)}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatDate(req.startedAt)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </>
        )}
      </Box>
    </Drawer>
  );
};

SessionDetailDrawer.propTypes = {
  sessionId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default SessionDetailDrawer;
