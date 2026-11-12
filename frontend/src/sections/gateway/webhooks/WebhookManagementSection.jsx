import StatusBadge from "src/components/status-badge/StatusBadge";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import SvgColor from "src/components/svg-color";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
} from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { EnforcementNote } from "../settings/ui";
import { GatewayIcon } from "../shell/gateway-icons";
import { getErrorMessage } from "../../settings/integrations/utils";
import { formatDate, formatDateTime } from "../utils/formatters";
import CreateEditWebhookDialog from "./CreateEditWebhookDialog";
import WebhookEventLog from "./WebhookEventLog";
import {
  useCreateWebhook,
  useDeleteWebhook,
  useTestWebhook,
  useUpdateWebhook,
  useWebhookEvents,
  useWebhooks,
} from "./hooks/useWebhooks";
import {
  DELIVERY_PAGE_LIMIT,
  deliveryState,
  latestDeliveryByWebhook,
  unproducedNotes,
  webhookEvent,
} from "./webhookEvents";
import { GATEWAY_MONO } from "../utils/fonts";

const ROOT = paths.dashboard.gateway.webhooks;
const DELIVERIES = `${ROOT}/delivery`;
const ROW_HEIGHT = 56;

const STATUS_ROLE = {
  healthy: "success",
  error: "danger",
  warning: "warning",
  pending: "info",
  neutral: "neutral",
};

// AG Grid cells are block boxes; custom cells centre their content.
const Cell = ({ children }) => (
  <Box sx={{ display: "flex", alignItems: "center", height: 1, minWidth: 0 }}>
    {children}
  </Box>
);
Cell.propTypes = { children: PropTypes.node };

function NameCell({ data }) {
  return (
    <Box sx={{ lineHeight: 1.2, py: 0.75, minWidth: 0 }}>
      <Typography typography="s1" noWrap>
        {data.name}
      </Typography>
      {data.description && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          title={data.description}
        >
          {data.description}
        </Typography>
      )}
    </Box>
  );
}
NameCell.propTypes = { data: PropTypes.object };

function EndpointCell({ value }) {
  return (
    <Stack
      justifyContent="center"
      alignItems="flex-start"
      gap={0.25}
      sx={{ height: 1, minWidth: 0 }}
    >
      <Typography
        typography="s2"
        noWrap
        title={value}
        sx={{ fontFamily: GATEWAY_MONO, maxWidth: 1 }}
      >
        {value}
      </Typography>
      {/^http:\/\//i.test(value || "") && (
        <Box
          component="span"
          sx={{
            typography: "s3",
            color: "warning.main",
            border: 1,
            borderColor: "warning.main",
            borderRadius: 0.5,
            px: 0.75,
            whiteSpace: "nowrap",
          }}
        >
          Unencrypted (HTTP)
        </Box>
      )}
    </Stack>
  );
}
EndpointCell.propTypes = { value: PropTypes.string };

const EVENTS_MIN_WIDTH = 230;

// How many event chips fit on one line of a column `width` px wide, keeping
// room for the "+N" count when some do not. Widths are estimated, never
// measured, so a cell renders once: 6.5 px a character is wider than Inter
// 11 px (s3) for every event name the gateway knows (the widest,
// budget.exceeded, is 6.1), so a chip shown is never cut. Cells pad 16 px
// a side (logs/__tests__/gridFit.js), a chip 6 px and a 1 px border a side.
const CELL_PADDING_PX = 32;
const CHIP_CHAR_PX = 6.5;
const CHIP_CHROME_PX = 14;
const CHIP_GAP_PX = 4;
const MORE_PX = 24;

const chipPx = (name) => Math.ceil(name.length * CHIP_CHAR_PX) + CHIP_CHROME_PX;

function eventChipsThatFit(names, width) {
  const room = width - CELL_PADDING_PX;
  let used = 0;
  let shown = 0;
  for (const name of names) {
    const next = used + (shown ? CHIP_GAP_PX : 0) + chipPx(name);
    const rest = names.length - shown - 1;
    if (next + (rest ? CHIP_GAP_PX + MORE_PX : 0) > room) break;
    used = next;
    shown += 1;
  }
  return Math.max(shown, Math.min(names.length, 1));
}

const neverSent = (name) => webhookEvent(name)?.produced === false;
const eventWithNote = (name) =>
  neverSent(name) ? `${name} (never sent by this gateway)` : name;

// The width of the cell's column, kept current as the user (or flex)
// resizes it: AG Grid does not re-render a cell for a width change.
function useColumnWidth(column, fallback) {
  const [width, setWidth] = useState(
    () => column?.getActualWidth?.() || fallback,
  );
  useEffect(() => {
    if (!column?.addEventListener) return undefined;
    const update = () => setWidth(column.getActualWidth() || fallback);
    update();
    column.addEventListener("widthChanged", update);
    return () => column.removeEventListener("widthChanged", update);
  }, [column, fallback]);
  return width;
}

function EventChip({ name, last }) {
  const produced = !neverSent(name);
  const chip = (
    <Box
      component="span"
      data-testid={`webhook-event-${name}`}
      data-produced={String(produced)}
      aria-label={produced ? name : `${name}: never sent by this gateway`}
      sx={{
        px: 0.75,
        py: 0.125,
        borderRadius: "4px",
        border: 1,
        borderColor: "divider",
        typography: "s3",
        whiteSpace: "nowrap",
        color: produced ? "text.primary" : "text.disabled",
        textDecoration: produced ? "none" : "line-through",
        // Should a font draw wider than estimated, the last chip gives way
        // so the "+N" count stays whole.
        minWidth: 0,
        flexShrink: last ? 100 : 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
    >
      {name}
    </Box>
  );
  return produced ? (
    chip
  ) : (
    <Tooltip title="Never sent by this gateway: it does not produce this event.">
      {chip}
    </Tooltip>
  );
}
EventChip.propTypes = {
  name: PropTypes.string.isRequired,
  last: PropTypes.bool,
};

// The subscribed events on one line: the chips that fit the column, then
// "+N" for the rest, whose tooltip and name list them. An event marked as
// never sent (webhookEvents.js; none is) would be muted and say so.
function EventsCell({ value, column }) {
  const names = value || [];
  const width = useColumnWidth(column, EVENTS_MIN_WIDTH);
  const count = eventChipsThatFit(names, width);
  const shown = names.slice(0, count);
  const hidden = names.slice(count);
  return (
    <Stack
      data-testid="webhook-events-cell"
      direction="row"
      flexWrap="nowrap"
      gap={0.5}
      alignItems="center"
      sx={{ height: 1, minWidth: 0, overflow: "hidden", lineHeight: 1.4 }}
    >
      {shown.map((name, index) => (
        <EventChip key={name} name={name} last={index === shown.length - 1} />
      ))}
      {hidden.length > 0 && (
        <Tooltip
          title={
            <Box component="span" sx={{ display: "block" }}>
              {hidden.map((name) => (
                <Box key={name} component="span" sx={{ display: "block" }}>
                  {eventWithNote(name)}
                </Box>
              ))}
            </Box>
          }
        >
          <Box
            component="span"
            tabIndex={0}
            data-testid="webhook-events-more"
            aria-label={`${hidden.length} more ${
              hidden.length === 1 ? "event" : "events"
            }: ${hidden.map(eventWithNote).join(", ")}`}
            sx={{
              flexShrink: 0,
              typography: "s3",
              color: "text.secondary",
              cursor: "default",
            }}
          >
            +{hidden.length}
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}
EventsCell.propTypes = {
  value: PropTypes.arrayOf(PropTypes.string),
  column: PropTypes.object,
};

function StatusCell({ data }) {
  return (
    <Cell>
      <StatusBadge
        status={data.is_active ? "Active" : "Paused"}
        label={data.is_active ? "Active" : "Paused"}
        role={data.is_active ? "success" : "neutral"}
        size="sm"
      />
    </Cell>
  );
}
StatusCell.propTypes = { data: PropTypes.object };

function LastDeliveryCell({ data, context }) {
  const last = data.lastDelivery;
  if (!last) {
    // The delivery log holds only the newest deliveries: past the cap, a
    // webhook missing from it may still have older ones.
    return (
      <Cell>
        <Typography typography="s2" color="text.disabled">
          {context?.deliveriesCapped
            ? `Not in the newest ${DELIVERY_PAGE_LIMIT}`
            : "None yet"}
        </Typography>
      </Cell>
    );
  }
  const state = deliveryState(last);
  // Why the last delivery failed, in the fixed words its log records (a
  // redirect says to save the final URL); the whole text on hover.
  const reason = last.status !== "delivered" ? last.last_error : "";
  return (
    <Stack
      justifyContent="center"
      alignItems="flex-start"
      gap={0.25}
      sx={{ height: 1, minWidth: 0 }}
    >
      <StatusBadge
        status={state.label}
        label={state.label}
        role={STATUS_ROLE[state.tone] || "neutral"}
        size="sm"
      />
      <Typography typography="s3" color="text.secondary" noWrap>
        {formatDateTime(last.last_attempt_at || last.created_at)}
      </Typography>
      {reason && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          title={reason}
          sx={{ maxWidth: 1 }}
        >
          {reason}
        </Typography>
      )}
    </Stack>
  );
}
LastDeliveryCell.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

const Glyph = ({ src }) => (
  <SvgColor src={src} aria-hidden sx={{ width: 16, height: 16 }} />
);
Glyph.propTypes = { src: PropTypes.string.isRequired };

function RowActions({ data, context }) {
  // Send test, Edit and Delete are gateway writes: Owner or Admin only.
  // Deliveries is a read.
  const allowed = Boolean(context.canEdit);
  const write = (label, icon, onClick) => {
    const button = (
      <IconButton
        size="small"
        aria-label={`${label} ${data.name}`}
        onClick={() => onClick(data)}
        disabled={!allowed}
      >
        <Glyph src={icon} />
      </IconButton>
    );
    return allowed ? (
      <Tooltip title={label}>{button}</Tooltip>
    ) : (
      <GatewayAdminOnly allowed={false}>{button}</GatewayAdminOnly>
    );
  };
  return (
    <Stack direction="row" gap={0.5} alignItems="center" sx={{ height: 1 }}>
      <GatewayAdminOnly allowed={allowed}>
        <Button
          size="small"
          variant="outlined"
          onClick={() => context.onTest(data)}
          disabled={!allowed || context.testing}
          aria-label={`Send test to ${data.name}`}
          sx={{ whiteSpace: "nowrap" }}
        >
          Send test
        </Button>
      </GatewayAdminOnly>
      <Tooltip title="Deliveries">
        <IconButton
          size="small"
          aria-label={`Deliveries of ${data.name}`}
          onClick={() => context.onDeliveries(data)}
        >
          <GatewayIcon name="logs" aria-hidden />
        </IconButton>
      </Tooltip>
      {write("Edit", "/assets/icons/ic_edit.svg", context.onEdit)}
      {write("Delete", "/assets/icons/ic_delete.svg", context.onDelete)}
    </Stack>
  );
}
RowActions.propTypes = { data: PropTypes.object, context: PropTypes.object };

// The minimum widths add up to 1,188 px of the 1,189 px grid at 1440, so no
// column, the row actions included, sits off screen there.
const COLUMNS = [
  {
    field: "name",
    headerName: "Webhook",
    minWidth: 150,
    flex: 1.1,
    cellRenderer: NameCell,
  },
  {
    field: "url",
    headerName: "Endpoint",
    minWidth: 150,
    flex: 1.1,
    cellRenderer: EndpointCell,
  },
  {
    field: "events",
    headerName: "Events",
    minWidth: EVENTS_MIN_WIDTH,
    flex: 1.5,
    sortable: false,
    cellRenderer: EventsCell,
  },
  {
    field: "is_active",
    headerName: "Status",
    minWidth: 126,
    flex: 0.5,
    cellRenderer: StatusCell,
  },
  {
    field: "lastDelivery",
    headerName: "Last delivery",
    minWidth: 168,
    flex: 0.9,
    comparator: (a, b) =>
      String(a?.created_at || "").localeCompare(String(b?.created_at || "")),
    cellRenderer: LastDeliveryCell,
  },
  {
    field: "created_at",
    headerName: "Created",
    minWidth: 136,
    flex: 0.6,
    valueFormatter: ({ value }) => formatDate(value),
  },
  {
    // Holds Send test, Deliveries, Edit and Delete whole, so each shows its
    // own tooltip on hover (webhooks/__tests__/ownerOrAdminWrites.test.jsx).
    colId: "actions",
    headerName: "",
    minWidth: 228,
    flex: 0.9,
    sortable: false,
    resizable: false,
    cellRenderer: RowActions,
  },
];

function matches(webhook, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    webhook.name?.toLowerCase().includes(q) ||
    webhook.url?.toLowerCase().includes(q) ||
    (webhook.events || []).some((event) => event.includes(q))
  );
}

// Webhooks: the organization's endpoints that receive gateway events, and
// every delivery to them, with each failed delivery's retry state.
export default function WebhookManagementSection() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const onDeliveries = tab === "delivery";

  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  // Keep only the current explicit attempt, in memory rather than a toast or
  // a fabricated delivery row. A fresh send replaces the previous outcome.
  const [testAttempt, setTestAttempt] = useState(null);
  const [searchParams] = useSearchParams();
  const deliveriesOf = searchParams.get("webhook") || null;

  // Creating, editing, deleting and test-sending a webhook are gateway
  // writes: Owner or Admin only, as on Providers, API keys and Budgets.
  const { canEdit } = useGatewayAdmin();
  const { data: webhooks, isLoading, error, refetch } = useWebhooks();
  const { data: events } = useWebhookEvents();
  const createMutation = useCreateWebhook();
  const updateMutation = useUpdateWebhook();
  const deleteMutation = useDeleteWebhook();
  const testMutation = useTestWebhook();

  const latest = useMemo(() => latestDeliveryByWebhook(events), [events]);
  const rows = useMemo(
    () =>
      (webhooks || [])
        .filter((webhook) => matches(webhook, search.trim()))
        .map((webhook) => ({
          ...webhook,
          lastDelivery: latest[webhook.id] || null,
        })),
    [webhooks, search, latest],
  );
  const notes = useMemo(
    () => unproducedNotes((webhooks || []).flatMap((w) => w.events || [])),
    [webhooks],
  );

  // The Deliveries tab keeps its webhook filter in the URL (?webhook=<id>),
  // so a filtered delivery list can be shared and survives a reload.
  const goTo = useCallback(
    (deliveries, webhookId = null) =>
      navigate(
        deliveries
          ? `${DELIVERIES}${webhookId ? `?webhook=${encodeURIComponent(webhookId)}` : ""}`
          : ROOT,
        { replace: true },
      ),
    [navigate],
  );

  const save = (payload) => {
    const editing = Boolean(payload.id);
    const mutation = editing ? updateMutation : createMutation;
    mutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar(editing ? "Webhook saved" : "Webhook created", {
          variant: "success",
        });
        setDialog(null);
      },
      onError: (err) =>
        enqueueSnackbar(
          getErrorMessage(
            err,
            editing ? "Failed to save the webhook" : "Failed to create webhook",
          ),
          { variant: "error" },
        ),
    });
  };

  const remove = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        enqueueSnackbar("Webhook deleted", { variant: "success" });
        setDeleteTarget(null);
      },
      onError: () =>
        enqueueSnackbar("Failed to delete webhook", { variant: "error" }),
    });
  };

  const sendTest = (webhook) => {
    if (!canEdit || testMutation.isPending) return;
    const target = { id: webhook.id, name: webhook.name, url: webhook.url };
    setTestAttempt({ ...target, label: "Sending test…", tone: "info" });
    testMutation.mutate(webhook.id, {
      onSuccess: (result) => {
        const code = result?.status_code;
        const delivered = result?.success !== false && code >= 200 && code < 300;
        const error = typeof result?.error === "string" ? result.error : "";
        setTestAttempt({
          ...target,
          label: delivered ? "Delivered" : "Not delivered",
          tone: delivered ? "success" : "danger",
          response: code == null ? "No receiver response" : `HTTP ${code}`,
          error,
        });
        if (delivered) {
          enqueueSnackbar(`Test delivered: ${webhook.name} answered ${code}`, {
            variant: "success",
          });
        } else {
          const answer = code == null ? "nothing" : `HTTP ${code}`;
          enqueueSnackbar(
            `Test not delivered: ${webhook.name} answered ${answer}${error ? `: ${error}` : ""}`,
            { variant: "warning" },
          );
        }
      },
      onError: (err) => {
        const error = getErrorMessage(err, "The test could not be confirmed");
        // A request error may precede dispatch, or leave its outcome unknown.
        // It is not evidence of a receiver response or a completed delivery.
        setTestAttempt({
          ...target,
          label: "Test result unavailable",
          tone: "warning",
          error,
          uncertain: true,
        });
        enqueueSnackbar(error, { variant: "error" });
      },
    });
  };

  const createButton = (
    <GatewayAdminOnly allowed={canEdit}>
      <Button
        variant="contained"
        color="primary"
        onClick={() => setDialog({ webhook: null })}
        disabled={!canEdit}
      >
        Create webhook
      </Button>
    </GatewayAdminOnly>
  );

  let endpoints;
  if (isLoading) {
    endpoints = (
      <Stack spacing={1} data-testid="webhooks-loading">
        <Skeleton variant="rounded" height={32} width={320} />
        <Skeleton variant="rounded" height={160} />
      </Stack>
    );
  } else if (error) {
    endpoints = (
      <GatewayEmptyState
        icon="webhooks"
        title="Couldn't load your webhooks"
        description={error.message || "The webhooks request failed."}
        actionLabel="Retry"
        onAction={() => refetch?.()}
      />
    );
  } else {
    const none = (webhooks || []).length === 0;
    endpoints = (
      <>
        {!none && (
          <GatewayFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search name, URL or event"
          />
        )}
        <EnforcementNote ids={notes} />
        <GatewayGrid
          rowData={rows}
          columnDefs={COLUMNS}
          stateKey="gateway-webhooks"
          getRowId={(params) => params.data.id}
          rowHeight={ROW_HEIGHT}
          height={Math.max(rows.length, 1) * ROW_HEIGHT + 42}
          context={{
            canEdit,
            deliveriesCapped: (events || []).length >= DELIVERY_PAGE_LIMIT,
            testing: testMutation.isPending,
            onTest: sendTest,
            onEdit: (webhook) => setDialog({ webhook }),
            onDelete: setDeleteTarget,
            onDeliveries: (webhook) => goTo(true, webhook.id),
          }}
          emptyState={
            none
              ? {
                  icon: "webhooks",
                  title: "No webhooks yet",
                  description:
                    "Get a signed POST on your endpoint when a request errors, a guardrail fires, or any request completes.",
                  actionLabel: canEdit ? "Create webhook" : undefined,
                  onAction: canEdit
                    ? () => setDialog({ webhook: null })
                    : undefined,
                }
              : { variant: "no-results", onAction: () => setSearch("") }
          }
        />
      </>
    );
  }

  return (
    <GatewayPageFrame
      title="Webhooks"
      description="Send gateway events to your own endpoints, signed with your secret, and see their deliveries."
      actions={createButton}
    >
      <CustomTabs
        value={onDeliveries ? 1 : 0}
        onChange={(_, next) => goTo(next === 1)}
        variant="scrollable"
        scrollButtons={false}
        sx={{
          boxShadow: "none",
          borderBottom: 1,
          borderColor: "divider",
          minHeight: 40,
        }}
      >
        <Tab
          label={
            webhooks?.length ? `Endpoints (${webhooks.length})` : "Endpoints"
          }
          sx={{ minHeight: 40 }}
        />
        <Tab label="Deliveries" sx={{ minHeight: 40 }} />
      </CustomTabs>

      {testAttempt && (
        <Box
          role="status"
          aria-label="Latest webhook test"
          aria-live="polite"
          sx={{
            p: 2,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Stack
            direction={{ xs: "column", sm: "row" }}
            gap={1.5}
            alignItems={{ xs: "flex-start", sm: "center" }}
          >
            <Stack
              gap={0.5}
              sx={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}
            >
              <Typography typography="s1" fontWeight={500}>
                Test to {testAttempt.name}
              </Typography>
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ fontFamily: GATEWAY_MONO }}
              >
                {testAttempt.url}
              </Typography>
              <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
                <StatusBadge
                  status={testAttempt.label}
                  label={testAttempt.label}
                  role={testAttempt.tone}
                  size="sm"
                />
                {testAttempt.response && (
                  <Typography typography="s2">{testAttempt.response}</Typography>
                )}
              </Stack>
              {testAttempt.error && (
                <Typography typography="s2" color="error.main">
                  {testAttempt.error}
                </Typography>
              )}
              {testAttempt.uncertain && (
                <Typography typography="s2" color="text.secondary">
                  Check deliveries before sending another test.
                </Typography>
              )}
            </Stack>
            <Button
              size="small"
              variant="outlined"
              onClick={() => goTo(true, testAttempt.id)}
              sx={{ flexShrink: 0 }}
            >
              View deliveries
            </Button>
          </Stack>
        </Box>
      )}

      {onDeliveries ? (
        <WebhookEventLog
          webhooks={webhooks || []}
          webhookId={deliveriesOf}
          onWebhookChange={(id) => goTo(true, id)}
        />
      ) : (
        endpoints
      )}

      <CreateEditWebhookDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        onSubmit={save}
        webhook={dialog?.webhook || null}
        isPending={createMutation.isPending || updateMutation.isPending}
      />

      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 560 } }}
        fullWidth
      >
        <DialogTitle>Delete webhook</DialogTitle>
        <DialogContent>
          <Typography typography="s1">
            Delete <strong>{deleteTarget?.name}</strong>? Its endpoint stops
            receiving events. This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={remove}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </GatewayPageFrame>
  );
}
