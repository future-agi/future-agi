import StatusBadge from "src/components/status-badge/StatusBadge";
import React, { useState } from "react";
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
} from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { CopyButton, KeyValueList } from "../settings/ui";
import { formatDateTime } from "../utils/formatters";
import { useRetryWebhookEvent, useWebhookEvents } from "./hooks/useWebhooks";
import {
  DELIVERY_PAGE_LIMIT,
  LIST_NOT_RETRIED_NOTE,
  RETRY_NOTE,
  WEBHOOK_EVENTS,
  canMarkForRetry,
  deliveryNote,
  deliveryState,
  needsNotRetriedNote,
  needsRetryNote,
  webhookEvent,
} from "./webhookEvents";
import { GATEWAY_MONO } from "../utils/fonts";

const STATUS_ROLE = {
  healthy: "success",
  error: "danger",
  warning: "warning",
  pending: "info",
  neutral: "neutral",
};

// Beside the deliveries it is about: how a delivery will be retried ("retry"),
// or that a Failed or Gave up one will not be ("not-retried").
function DeliveryNote({ kind, text }) {
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid={`webhook-${kind}-note`}
    >
      {text}
    </Typography>
  );
}
DeliveryNote.propTypes = {
  kind: PropTypes.oneOf(["retry", "not-retried"]).isRequired,
  text: PropTypes.string.isRequired,
};

const PAGE_LIMIT = DELIVERY_PAGE_LIMIT;
const ROW_HEIGHT = 44;

const STATUS_FILTERS = [
  { value: "all", label: "Any status" },
  { value: "delivered", label: "Delivered" },
  { value: "pending", label: "Pending (queued or awaiting retry)" },
  { value: "failed", label: "Failed" },
  { value: "dead_letter", label: "Gave up (dead letter)" },
];

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
      <StatusBadge
        status={state.label}
        label={state.label}
        role={STATUS_ROLE[state.tone] || "neutral"}
        size="sm"
      />
    </Cell>
  );
}
StatusCell.propTypes = { data: PropTypes.object };

function deliveryError(delivery) {
  return (
    delivery.last_error ||
    (delivery.last_response_code >= 300
      ? `HTTP ${delivery.last_response_code}`
      : "")
  );
}

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
    cellStyle: { fontVariantNumeric: "tabular-nums" },
    minWidth: 144,
    flex: 0.5,
  },
  {
    field: "last_response_code",
    headerName: "Response",
    cellStyle: { fontVariantNumeric: "tabular-nums" },
    minWidth: 150,
    flex: 0.5,
    valueFormatter: ({ value }) => (value == null ? "—" : String(value)),
  },
  {
    field: "last_error",
    headerName: "Error",
    minWidth: 200,
    flex: 1.3,
    valueGetter: ({ data }) => deliveryError(data),
    cellRenderer: ErrorCell,
  },
];

function DeliveryDrawer({ delivery, onClose, onRetry, retrying, canEdit }) {
  if (!delivery) return null;
  const state = deliveryState(delivery);
  const requestId = delivery.payload?.request_id;
  const error = deliveryError(delivery);
  const note = deliveryNote(delivery);
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
        {note && <DeliveryNote kind={note.kind} text={note.text} />}
        <KeyValueList
          rows={[
            {
              label: "Status",
              value: (
                <StatusBadge
                  status={state.label}
                  label={state.label}
                  role={STATUS_ROLE[state.tone] || "neutral"}
                  size="sm"
                />
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
              value: error,
              hidden: !error,
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

// Filter at the source before the API's newest-100 cap. Pending includes
// queued deliveries and failed attempts awaiting their scheduled retry.
const WebhookEventLog = ({ webhooks = [], webhookId, onWebhookChange }) => {
  const [status, setStatus] = useState("all");
  const [eventType, setEventType] = useState("all");
  const params = {
    ...(webhookId ? { webhook_id: webhookId } : {}),
    ...(status !== "all" ? { status } : {}),
    ...(eventType !== "all" ? { event_type: eventType } : {}),
  };
  const {
    data: events,
    isLoading,
    error,
    refetch,
  } = useWebhookEvents(Object.keys(params).length ? params : undefined);
  const retryMutation = useRetryWebhookEvent();
  // Retrying a delivery sends it again: Owner or Admin only.
  const { canEdit } = useGatewayAdmin();
  const [selectedId, setSelectedId] = useState(null);
  const selectedDelivery = events?.find((event) => event.id === selectedId);

  const rows = events || [];

  const retry = (delivery) =>
    retryMutation.mutate(delivery.id, {
      onSuccess: () => {
        enqueueSnackbar(
          "Retry started: the delivery goes out within 30 seconds",
          { variant: "success" },
        );
        setSelectedId(null);
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

  const filtered = Boolean(webhookId) || status !== "all" || eventType !== "all";
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
              Newest {PAGE_LIMIT} matching deliveries
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
        <TextField select size="small" value={eventType} onChange={(event) => setEventType(event.target.value)} inputProps={{ "aria-label": "Event type" }} sx={fieldSx}>
          <MenuItem value="all">Any event type</MenuItem>
          {[...WEBHOOK_EVENTS, { value: "test", label: "Test" }].map((option) => (
            <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>
          ))}
        </TextField>
      </GatewayFilterBar>
      <Typography typography="s2" color="text.secondary">
        Filters apply before the newest-{PAGE_LIMIT} limit. Pending includes failed attempts awaiting retry; Failed and Gave up are separate delivery states.
      </Typography>

      {needsRetryNote(rows) && <DeliveryNote kind="retry" text={RETRY_NOTE} />}
      {needsNotRetriedNote(rows) && (
        <DeliveryNote kind="not-retried" text={LIST_NOT_RETRIED_NOTE} />
      )}

      <GatewayGrid
        rowData={isLoading ? undefined : rows}
        loading={isLoading}
        columnDefs={COLUMNS}
        stateKey="gateway-webhook-deliveries"
        getRowId={(p) => p.data.id}
        rowHeight={ROW_HEIGHT}
        onRowClicked={(event) => setSelectedId(event.data.id)}
        emptyState={
          filtered
            ? {
                variant: "no-results",
                onAction: () => {
                  setStatus("all");
                  setEventType("all");
                  onWebhookChange?.(null);
                },
              }
            : {
                icon: "webhooks",
                title: "No deliveries yet",
                description:
                  "Each row tracks one event for one of your webhooks, and retries update the same row. Recorded Send test results appear here too. Tests rejected before sending may not appear.",
              }
        }
      />

      {selectedId != null && !selectedDelivery && (
        <GatewayDetailDrawer open onClose={() => setSelectedId(null)} title="Delivery details">
          <GatewayEmptyState
            icon="webhooks"
            title={isLoading ? "Loading deliveries…" : "Delivery outside this view"}
            description="This delivery is no longer in the loaded deliveries. It may be outside the current filters or newest-100 subset; its current status is unknown here."
            actionLabel="Refresh deliveries"
            onAction={() => refetch?.()}
          />
        </GatewayDetailDrawer>
      )}
      <DeliveryDrawer
        delivery={selectedDelivery}
        onClose={() => setSelectedId(null)}
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
