import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import {
  GatewayDetailDrawer,
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { CopyButton, KeyValueList } from "../settings/ui";
import { formatDateTime } from "../utils/formatters";
import { useRetryWebhookEvent, useWebhookEvents } from "./hooks/useWebhooks";
import {
  DELIVERY_PAGE_LIMIT,
  RETRY_NOTE,
  canMarkForRetry,
  deliveryState,
  needsRetryNote,
  webhookEvent,
} from "./webhookEvents";
import { GATEWAY_MONO } from "../utils/fonts";

// How a failed delivery is retried, beside the deliveries that failed.
function RetryNote() {
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid="webhook-retry-note"
    >
      {RETRY_NOTE}
    </Typography>
  );
}

const PAGE_LIMIT = DELIVERY_PAGE_LIMIT;
const ROW_HEIGHT = 44;

const STATUS_FILTERS = [
  { value: "all", label: "Any status" },
  { value: "delivered", label: "Delivered" },
  { value: "failed", label: "Not delivered" },
  { value: "queued", label: "Queued" },
];

function statusGroup(event) {
  const state = deliveryState(event);
  if (state.tone === "healthy") return "delivered";
  if (state.tone === "pending") return "queued";
  return "failed";
}

const eventLabel = (type) =>
  type === "test" ? "Test" : webhookEvent(type)?.label || type;

const Cell = ({ children }) => (
  <Box sx={{ display: "flex", alignItems: "center", height: 1, minWidth: 0 }}>
    {children}
  </Box>
);
Cell.propTypes = { children: PropTypes.node };

function StatusCell({ data }) {
  const state = deliveryState(data);
  return (
    <Cell>
      <GatewayStatusChip tone={state.tone} label={state.label} />
    </Cell>
  );
}
StatusCell.propTypes = { data: PropTypes.object };

function ErrorCell({ value }) {
  return (
    <Cell>
      <Typography
        typography="s2"
        color={value ? "error.main" : "text.disabled"}
        noWrap
        title={value || undefined}
      >
        {value || "—"}
      </Typography>
    </Cell>
  );
}
ErrorCell.propTypes = { value: PropTypes.string };

const COLUMNS = [
  {
    field: "created_at",
    headerName: "Time",
    minWidth: 170,
    flex: 0.9,
    sort: "desc",
    valueFormatter: ({ value }) => formatDateTime(value),
  },
  {
    field: "event_type",
    headerName: "Event",
    minWidth: 170,
    flex: 0.9,
    valueFormatter: ({ value }) => eventLabel(value),
  },
  {
    field: "webhook_name",
    headerName: "Webhook",
    minWidth: 150,
    flex: 0.8,
  },
  {
    field: "status",
    headerName: "Status",
    minWidth: 230,
    flex: 1.1,
    valueGetter: ({ data }) => deliveryState(data).label,
    cellRenderer: StatusCell,
  },
  {
    field: "attempts",
    headerName: "Attempts",
    minWidth: 144,
    flex: 0.5,
  },
  {
    field: "last_response_code",
    headerName: "Response",
    minWidth: 150,
    flex: 0.5,
    valueFormatter: ({ value }) => (value == null ? "—" : String(value)),
  },
  {
    field: "last_error",
    headerName: "Error",
    minWidth: 200,
    flex: 1.3,
    cellRenderer: ErrorCell,
  },
];

function DeliveryDrawer({ delivery, onClose, onRetry, retrying, canEdit }) {
  if (!delivery) return null;
  const state = deliveryState(delivery);
  const requestId = delivery.payload?.request_id;
  const failed = state.tone === "error" || state.awaitingRetry;
  return (
    <GatewayDetailDrawer
      open
      onClose={onClose}
      title={eventLabel(delivery.event_type)}
      subtitle={`${delivery.webhook_name || "Webhook"} · ${formatDateTime(delivery.created_at)}`}
      actions={
        canMarkForRetry(delivery) ? (
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => onRetry(delivery)}
              disabled={!canEdit || retrying}
            >
              Retry now
            </Button>
          </GatewayAdminOnly>
        ) : null
      }
    >
      <Stack spacing={2.5}>
        {failed && <RetryNote />}
        <KeyValueList
          rows={[
            {
              label: "Status",
              value: (
                <GatewayStatusChip tone={state.tone} label={state.label} />
              ),
            },
            { label: "Event", value: delivery.event_type },
            { label: "Attempts", value: String(delivery.attempts ?? 0) },
            {
              label: "Response",
              value:
                delivery.last_response_code == null
                  ? "No response"
                  : String(delivery.last_response_code),
            },
            {
              label: "Last attempt",
              value: delivery.last_attempt_at
                ? formatDateTime(delivery.last_attempt_at)
                : "Not attempted",
            },
            {
              label: "Error",
              value: delivery.last_error,
              hidden: !delivery.last_error,
            },
            {
              label: "Request",
              hidden: !requestId,
              value: (
                <Stack direction="row" alignItems="center" gap={1}>
                  <Typography typography="s1" sx={{ fontFamily: GATEWAY_MONO }}>
                    {requestId}
                  </Typography>
                  {requestId && <CopyButton text={requestId} />}
                </Stack>
              ),
            },
          ]}
        />
        <Box>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{ mb: 1 }}
          >
            <Typography typography="s1" fontWeight={500}>
              Payload
            </Typography>
            <CopyButton
              text={JSON.stringify(delivery.payload ?? {}, null, 2)}
              label="Copy payload"
            />
          </Stack>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              borderRadius: 1,
              border: 1,
              borderColor: "divider",
              bgcolor: "background.neutral",
              typography: "s2",
              fontFamily: GATEWAY_MONO,
              overflowX: "auto",
              whiteSpace: "pre",
            }}
          >
            {JSON.stringify(delivery.payload ?? {}, null, 2)}
          </Box>
        </Box>
      </Stack>
    </GatewayDetailDrawer>
  );
}

DeliveryDrawer.propTypes = {
  delivery: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
  retrying: PropTypes.bool,
  canEdit: PropTypes.bool,
};

// Every delivery to the organization's webhooks, newest first; a row opens
// the delivery with its payload. Failed deliveries are not retried on a
// schedule, and the page says so when there are any.
const WebhookEventLog = ({ webhooks = [], webhookId, onWebhookChange }) => {
  const {
    data: events,
    isLoading,
    error,
    refetch,
  } = useWebhookEvents(webhookId ? { webhook_id: webhookId } : undefined);
  const retryMutation = useRetryWebhookEvent();
  // Retrying a delivery sends it again: Owner or Admin only.
  const { canEdit } = useGatewayAdmin();
  const [status, setStatus] = useState("all");
  const [open, setOpen] = useState(null);

  const rows = useMemo(
    () =>
      (events || []).filter(
        (event) => status === "all" || statusGroup(event) === status,
      ),
    [events, status],
  );

  const retry = (delivery) =>
    retryMutation.mutate(delivery.id, {
      onSuccess: () => {
        enqueueSnackbar(
          "Retry started: the delivery goes out within 30 seconds",
          { variant: "success" },
        );
        setOpen(null);
      },
      onError: () =>
        enqueueSnackbar("The delivery could not be retried", {
          variant: "error",
        }),
    });

  if (error) {
    return (
      <GatewayEmptyState
        icon="webhooks"
        title="Couldn't load deliveries"
        description={error.message || "The deliveries request failed."}
        actionLabel="Retry"
        onAction={() => refetch?.()}
      />
    );
  }

  const filtered = Boolean(webhookId) || status !== "all";
  const fieldSx = {
    minWidth: 180,
    "& .MuiOutlinedInput-root": { height: 30, typography: "s2" },
  };

  return (
    <>
      <GatewayFilterBar
        actions={
          events?.length >= PAGE_LIMIT ? (
            <Typography typography="s2" color="text.secondary">
              Newest {PAGE_LIMIT} deliveries
            </Typography>
          ) : null
        }
      >
        <TextField
          select
          size="small"
          value={webhookId || "all"}
          onChange={(event) =>
            onWebhookChange?.(
              event.target.value === "all" ? null : event.target.value,
            )
          }
          inputProps={{ "aria-label": "Webhook" }}
          sx={fieldSx}
        >
          <MenuItem value="all">All webhooks</MenuItem>
          {webhooks.map((webhook) => (
            <MenuItem key={webhook.id} value={webhook.id}>
              {webhook.name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          inputProps={{ "aria-label": "Status" }}
          sx={fieldSx}
        >
          {STATUS_FILTERS.map((option) => (
            <MenuItem key={option.value} value={option.value}>
              {option.label}
            </MenuItem>
          ))}
        </TextField>
      </GatewayFilterBar>

      {needsRetryNote(rows) && <RetryNote />}

      <GatewayGrid
        rowData={isLoading ? undefined : rows}
        loading={isLoading}
        columnDefs={COLUMNS}
        stateKey="gateway-webhook-deliveries"
        getRowId={(p) => p.data.id}
        rowHeight={ROW_HEIGHT}
        onRowClicked={(event) => setOpen(event.data)}
        emptyState={
          filtered
            ? {
                variant: "no-results",
                onAction: () => {
                  setStatus("all");
                  onWebhookChange?.(null);
                },
              }
            : {
                icon: "webhooks",
                title: "No deliveries yet",
                description:
                  "A delivery appears here each time the gateway sends an event to one of your webhooks. Send test on Endpoints shows its result as a notice; it is not listed here.",
              }
        }
      />

      <DeliveryDrawer
        delivery={open}
        onClose={() => setOpen(null)}
        onRetry={retry}
        retrying={retryMutation.isPending}
        canEdit={canEdit}
      />
    </>
  );
};

WebhookEventLog.propTypes = {
  webhooks: PropTypes.array,
  webhookId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onWebhookChange: PropTypes.func,
};

export default WebhookEventLog;
