import React from "react";
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Button,
  Skeleton,
  Alert,
  Stack,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useWebhookEvents, useRetryWebhookEvent } from "./hooks/useWebhooks";
import { enqueueSnackbar } from "notistack";
import { formatDateTime as formatDate } from "../utils/formatters";

const STATUS_COLORS = {
  pending: "warning",
  delivered: "success",
  failed: "error",
  dead_letter: "default",
};

const WebhookEventLog = ({ webhookId }) => {
  const params = webhookId ? { webhook_id: webhookId } : {};
  const { data: events, isLoading, error } = useWebhookEvents(params);
  const retryMutation = useRetryWebhookEvent();

  const handleRetry = (eventId) => {
    retryMutation.mutate(eventId, {
      onSuccess: () =>
        enqueueSnackbar("Event queued for retry", { variant: "success" }),
      onError: () =>
        enqueueSnackbar("Failed to retry event", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box>
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} width="100%" height={40} sx={{ mb: 0.5 }} />
        ))}
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error">
        Failed to load webhook events: {error.message}
      </Alert>
    );
  }

  if (!events?.length) {
    return (
      <Box display="flex" flexDirection="column" alignItems="center" py={6}>
        <Iconify
          icon="mdi:inbox-outline"
          width={48}
          sx={{ color: "text.disabled", mb: 2 }}
        />
        <Typography variant="body1" color="text.secondary">
          No delivery events yet
        </Typography>
      </Box>
    );
  }

  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Event Type</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Attempts</TableCell>
            <TableCell>Response Code</TableCell>
            <TableCell>Last Attempt</TableCell>
            <TableCell>Error</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {events.map((evt) => (
            <TableRow key={evt.id} hover>
              <TableCell>
                <Typography variant="body2" fontWeight={500}>
                  {evt.event_type}
                </Typography>
              </TableCell>
              <TableCell>
                <Chip
                  label={evt.status}
                  color={STATUS_COLORS[evt.status] || "default"}
                  size="small"
                />
              </TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  {evt.attempts}/{evt.max_attempts ?? evt.maxAttempts}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  {evt.last_response_code ?? evt.lastResponseCode ?? "\u2014"}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  {formatDate(evt.last_attempt_at ?? evt.lastAttemptAt)}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography
                  variant="body2"
                  color="error.main"
                  noWrap
                  sx={{ maxWidth: 200 }}
                  title={evt.last_error ?? evt.lastError}
                >
                  {evt.last_error || evt.lastError || "\u2014"}
                </Typography>
              </TableCell>
              <TableCell align="right">
                {(evt.status === "failed" || evt.status === "dead_letter") && (
                  <Button
                    size="small"
                    startIcon={<Iconify icon="mdi:refresh" width={16} />}
                    onClick={() => handleRetry(evt.id)}
                    disabled={retryMutation.isPending}
                  >
                    Retry
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1 }}>
        <Typography variant="body2" color="text.secondary">
          Showing {events.length} event{events.length !== 1 ? "s" : ""}
        </Typography>
      </Stack>
    </TableContainer>
  );
};

WebhookEventLog.propTypes = {
  webhookId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default WebhookEventLog;
