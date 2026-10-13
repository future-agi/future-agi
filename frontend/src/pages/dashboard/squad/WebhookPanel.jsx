import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

function items(result, key) {
  if (!result?.available) return [];
  if (Array.isArray(result[key])) return result[key];
  if (Array.isArray(result.items)) return result.items;
  return [];
}

function label(value, fallback) {
  if (!value || typeof value !== "object") return fallback;
  return (
    value.channelId || value.id || value.eventKey || value.eventId || fallback
  );
}

function unavailable(result) {
  return result?.reason || "The remote webhook channel service is unavailable.";
}

function receiptFrom(result) {
  if (result?.receipt && typeof result.receipt === "object") {
    return (
      result.receipt.id ||
      result.receipt.operationId ||
      "Remote mutation acknowledged"
    );
  }
  const headers = result?.transport?.headers;
  return (
    headers?.["x-omega-receipt-id"] ||
    headers?.["x-omega-operation-id"] ||
    "Remote mutation acknowledged"
  );
}

const clientIds = new WeakMap();
let nextClientId = 0;
function clientKey(client) {
  let id = clientIds.get(client);
  if (id === undefined) {
    id = ++nextClientId;
    clientIds.set(client, id);
  }
  return id;
}

/**
 * Tenant webhook operations for Squad. This deliberately uses only the
 * customer-safe `/os/channels` contract and keeps signing secrets in memory
 * long enough to submit them; the browser never persists or displays them.
 */
export default function WebhookPanel({ client }) {
  // A new authenticated client is a new workspace. The identity key unmounts
  // the previous child before any of its data, drafts, or pending mutations can
  // be rendered under the new credential.
  return <WorkspaceWebhooks key={clientKey(client)} client={client} />;
}

function WorkspaceWebhooks({ client }) {
  const [channelsResult, setChannelsResult] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [eventKey, setEventKey] = useState("");
  const [deliveries, setDeliveries] = useState(null);
  const [draft, setDraft] = useState({
    url: "",
    description: "",
    eventTypes: "",
    signingSecret: "",
  });
  const channelSequence = useRef(0);
  const detailSequence = useRef(0);
  const active = useRef(true);
  const selectedChannel = useRef(selectedId);
  selectedChannel.current = selectedId;
  const mutationPending = useRef(false);
  const [deliveryRevision, setDeliveryRevision] = useState(0);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      channelSequence.current += 1;
      detailSequence.current += 1;
    };
  }, []);

  const loadChannels = useCallback(async () => {
    const sequence = ++channelSequence.current;
    setLoading(true);
    setError("");
    try {
      const result = await client.listChannels();
      if (sequence !== channelSequence.current) return;
      setChannelsResult(result);
      if (!result.available) {
        setSelectedId("");
      } else {
        const channelRows = items(result, "channels");
        setSelectedId((current) =>
          channelRows.some((channel) => channel.channelId === current)
            ? current
            : channelRows[0]?.channelId || "",
        );
      }
    } catch (cause) {
      if (sequence === channelSequence.current) {
        setChannelsResult(null);
        setSelectedId("");
        setError(
          "The remote webhook channel service is unavailable. Refresh to try again.",
        );
      }
    } finally {
      if (sequence === channelSequence.current) setLoading(false);
    }
  }, [client]);

  const loadDetail = useCallback(
    async (channelId) => {
      const sequence = ++detailSequence.current;
      if (!channelId) {
        setDetail(null);
        setEventKey("");
        setDeliveries(null);
        setDetailLoading(false);
        return;
      }
      setDetailLoading(true);
      setError("");
      try {
        const [subscriptions, events, slo] = await Promise.all([
          client.listChannelSubscriptions(channelId),
          client.listChannelEvents(channelId, { limit: 50 }),
          client.getChannelDeliverySlo(channelId, { window: "24h" }),
        ]);
        if (
          sequence !== detailSequence.current ||
          selectedChannel.current !== channelId
        )
          return;
        setDetail({ channelId, subscriptions, events, slo });
        const eventRows = items(events, "events");
        setEventKey((current) =>
          eventRows.some((event) => (event.eventKey || event.id) === current)
            ? current
            : eventRows[0]?.eventKey || eventRows[0]?.id || "",
        );
        setDeliveryRevision((current) => current + 1);
      } catch {
        if (
          sequence === detailSequence.current &&
          selectedChannel.current === channelId
        ) {
          setDetail(null);
          setEventKey("");
          setDeliveries(null);
          setError(
            "The channel details could not be loaded. Refresh to try again.",
          );
        }
      } finally {
        if (sequence === detailSequence.current) setDetailLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    void loadChannels();
  }, [loadChannels]);
  useEffect(() => {
    setDetail(null);
    setEventKey("");
    setDeliveries(null);
    setDraft({ url: "", description: "", eventTypes: "", signingSecret: "" });
    setMessage("");
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    let current = true;
    setDeliveries(null);
    if (!selectedId || !eventKey || detail?.channelId !== selectedId) {
      return undefined;
    }
    void client
      .listChannelEventDeliveries(selectedId, eventKey, { limit: 50 })
      .then((result) => {
        if (current) setDeliveries({ channelId: selectedId, eventKey, result });
      })
      .catch(() => {
        if (current)
          setDeliveries({
            channelId: selectedId,
            eventKey,
            result: {
              available: false,
              reason:
                "Delivery history could not be loaded. Refresh to try again.",
            },
          });
      });
    return () => {
      current = false;
    };
  }, [client, eventKey, selectedId, deliveryRevision, detail?.channelId]);

  const channels = useMemo(
    () => items(channelsResult, "channels"),
    [channelsResult],
  );
  const currentDetail = detail?.channelId === selectedId ? detail : null;
  const subscriptions = items(currentDetail?.subscriptions, "subscriptions");
  const events = items(currentDetail?.events, "events");
  const currentDeliveries =
    deliveries?.channelId === selectedId && deliveries?.eventKey === eventKey
      ? deliveries.result
      : null;
  const deliveryRows = items(currentDeliveries, "deliveries");
  const sloSummary = currentDetail?.slo?.available
    ? currentDetail.slo.summary
    : null;

  const mutate = async (operation, key) => {
    if (
      mutationPending.current ||
      !selectedId ||
      detailLoading ||
      !currentDetail
    )
      return undefined;
    mutationPending.current = true;
    const channelId = selectedId;
    const isCurrent = () =>
      active.current && selectedChannel.current === channelId;
    setBusy(key);
    setError("");
    setMessage("");
    try {
      const result = await operation();
      if (!isCurrent()) return undefined;
      if (!result.available) {
        setError(unavailable(result));
        return result;
      }
      setMessage(receiptFrom(result));
      await loadDetail(channelId);
      return result;
    } catch {
      if (isCurrent())
        setError(
          "The remote webhook operation could not be confirmed. Refresh to check its status.",
        );
      return { available: false };
    } finally {
      mutationPending.current = false;
      if (active.current) setBusy("");
    }
  };

  const createEndpoint = async (event) => {
    event.preventDefault();
    if (!selectedId || !draft.url.trim()) return;
    const eventTypes = draft.eventTypes
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const input = {
      url: draft.url.trim(),
      ...(draft.description.trim()
        ? { description: draft.description.trim() }
        : {}),
      ...(eventTypes.length > 0 ? { eventTypes } : {}),
      ...(draft.signingSecret ? { signingSecret: draft.signingSecret } : {}),
    };
    const result = await mutate(
      () =>
        client.createChannelSubscription(
          selectedId,
          input,
          `squad-endpoint-${Date.now()}`,
        ),
      "create",
    );
    if (active.current && selectedChannel.current === selectedId) {
      setDraft((current) =>
        result?.available
          ? { url: "", description: "", eventTypes: "", signingSecret: "" }
          : { ...current, signingSecret: "" },
      );
    }
  };

  const deleteEndpoint = async (subscriptionId) => {
    if (
      !selectedId ||
      (typeof globalThis.confirm === "function" &&
        !globalThis.confirm("Disable this webhook endpoint?"))
    )
      return;
    await mutate(
      () =>
        client.deleteChannelSubscription(
          selectedId,
          subscriptionId,
          `squad-disable-${subscriptionId}-${Date.now()}`,
        ),
      `delete:${subscriptionId}`,
    );
  };

  const testEndpoint = (subscriptionId) =>
    mutate(
      () => client.testChannelSubscription(selectedId, subscriptionId),
      `test:${subscriptionId}`,
    );

  const redrive = (delivery) =>
    mutate(
      () =>
        client.redriveChannelDelivery(
          selectedId,
          eventKey,
          delivery.id,
          `squad-redrive-${delivery.id}-${Date.now()}`,
        ),
      `redrive:${delivery.id}`,
    );

  return (
    <Card variant="outlined" aria-label="Webhook channels">
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="h6">Webhook channels</Typography>
            <Typography variant="body2" color="text.secondary">
              Send agent events to your apps and inspect delivery history for
              this workspace.
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:refresh-linear" />}
            onClick={() => {
              void loadChannels();
              void loadDetail(selectedId);
            }}
            disabled={loading || detailLoading || Boolean(busy)}
          >
            Refresh
          </Button>
        </Stack>
        {error ? (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        ) : null}
        {message ? (
          <Alert severity="success" sx={{ mt: 2 }}>
            {message}
          </Alert>
        ) : null}
        {loading && !channelsResult ? (
          <CircularProgress size={22} sx={{ mt: 2 }} />
        ) : null}
        {channelsResult?.available === false ? (
          <Alert severity="info" sx={{ mt: 2 }}>
            {unavailable(channelsResult)}
          </Alert>
        ) : null}
        {channelsResult?.available && channels.length === 0 ? (
          <Typography color="text.secondary" sx={{ mt: 2 }}>
            No tenant webhook channels are registered.
          </Typography>
        ) : null}
        {channels.length > 0 ? (
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} md={4}>
              <List disablePadding aria-label="Webhook channel list">
                {channels.map((channel) => {
                  const id = channel.channelId;
                  return (
                    <ListItem key={id} disablePadding>
                      <ListItemButton
                        selected={id === selectedId}
                        onClick={() => setSelectedId(id)}
                      >
                        <ListItemText
                          primary={id}
                          secondary={`${channel.events ?? 0} events · ${channel.subscriptions?.active ?? 0} active endpoints`}
                        />
                      </ListItemButton>
                    </ListItem>
                  );
                })}
              </List>
            </Grid>
            <Grid item xs={12} md={8}>
              {detailLoading ? <CircularProgress size={22} /> : null}
              {currentDetail?.subscriptions?.available === false ? (
                <Alert severity="info">
                  {unavailable(currentDetail.subscriptions)}
                </Alert>
              ) : null}
              {currentDetail?.events?.available === false ? (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {unavailable(currentDetail.events)}
                </Alert>
              ) : null}
              {currentDetail?.slo?.available === false ? (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {unavailable(currentDetail.slo)}
                </Alert>
              ) : null}
              {sloSummary ? (
                <Stack
                  direction="row"
                  spacing={1}
                  flexWrap="wrap"
                  sx={{ mb: 1.5 }}
                >
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`24h timely ${sloSummary.timelyDeliveryRate == null ? "unknown" : `${Math.round(sloSummary.timelyDeliveryRate * 100)}%`}`}
                  />
                  <Chip
                    size="small"
                    color={
                      currentDetail.slo.alert?.severity === "critical"
                        ? "error"
                        : currentDetail.slo.alert?.severity === "warning"
                          ? "warning"
                          : "default"
                    }
                    label={currentDetail.slo.alert?.verdict || "SLO unknown"}
                  />
                </Stack>
              ) : null}
              <Box
                component="form"
                onSubmit={createEndpoint}
                sx={{
                  p: 1.5,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Add endpoint
                </Typography>
                <Grid container spacing={1}>
                  <Grid item xs={12} sm={7}>
                    <TextField
                      fullWidth
                      size="small"
                      required
                      label="Endpoint URL"
                      type="url"
                      value={draft.url}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          url: event.target.value,
                        }))
                      }
                      placeholder="https://example.com/omega-webhook"
                    />
                  </Grid>
                  <Grid item xs={12} sm={5}>
                    <TextField
                      fullWidth
                      size="small"
                      label="Label"
                      value={draft.description}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          description: event.target.value,
                        }))
                      }
                    />
                  </Grid>
                  <Grid item xs={12} sm={7}>
                    <TextField
                      fullWidth
                      size="small"
                      label="Event filters"
                      value={draft.eventTypes}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          eventTypes: event.target.value,
                        }))
                      }
                      placeholder="run.completed, run.failed"
                    />
                  </Grid>
                  <Grid item xs={12} sm={5}>
                    <TextField
                      fullWidth
                      size="small"
                      type="password"
                      autoComplete="new-password"
                      label="Signing secret (optional)"
                      value={draft.signingSecret}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          signingSecret: event.target.value,
                        }))
                      }
                    />
                  </Grid>
                </Grid>
                <Button
                  type="submit"
                  size="small"
                  variant="contained"
                  sx={{ mt: 1 }}
                  disabled={
                    Boolean(busy) ||
                    detailLoading ||
                    !currentDetail?.subscriptions?.available ||
                    !selectedId
                  }
                >
                  {busy === "create" ? "Creating…" : "Create endpoint"}
                </Button>
              </Box>
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2">
                Endpoints ({subscriptions.length})
              </Typography>
              {subscriptions.length === 0 ? (
                <Typography color="text.secondary" sx={{ mt: 1 }}>
                  No endpoints are configured for this channel.
                </Typography>
              ) : (
                <List dense disablePadding>
                  {subscriptions.map((subscription) => (
                    <ListItem
                      key={subscription.id}
                      divider
                      secondaryAction={
                        <Stack direction="row" spacing={0.5}>
                          <Button
                            size="small"
                            onClick={() => void testEndpoint(subscription.id)}
                            disabled={
                              Boolean(busy) ||
                              detailLoading ||
                              subscription.status !== "active"
                            }
                          >
                            {busy === `test:${subscription.id}`
                              ? "Testing…"
                              : "Test"}
                          </Button>
                          <Button
                            size="small"
                            color="error"
                            onClick={() => void deleteEndpoint(subscription.id)}
                            disabled={
                              Boolean(busy) ||
                              detailLoading ||
                              subscription.status !== "active"
                            }
                          >
                            Disable
                          </Button>
                        </Stack>
                      }
                    >
                      <ListItemText
                        primary={
                          subscription.description ||
                          subscription.url ||
                          label(subscription, subscription.id)
                        }
                        secondary={`${subscription.status || "unknown"}${subscription.hasSigningSecret ? " · signing secret configured" : ""}`}
                      />
                    </ListItem>
                  ))}
                </List>
              )}
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2">Recent events</Typography>
              {events.length === 0 ? (
                <Typography color="text.secondary" sx={{ mt: 1 }}>
                  No events recorded for this channel.
                </Typography>
              ) : (
                <List dense disablePadding>
                  {events.map((event) => {
                    const key = event.eventKey || event.id;
                    return (
                      <ListItem key={event.id || key} divider disablePadding>
                        <ListItemButton
                          selected={key === eventKey}
                          onClick={() => setEventKey(key)}
                        >
                          <ListItemText
                            primary={key}
                            secondary={`${event.at || event.createdAt || "time unavailable"}${event.taskId ? ` · task ${event.taskId}` : ""}`}
                          />
                        </ListItemButton>
                      </ListItem>
                    );
                  })}
                </List>
              )}
              {eventKey && currentDetail ? (
                <Box sx={{ mt: 1.5 }}>
                  <Typography variant="subtitle2">
                    Delivery attempts for {eventKey}
                  </Typography>
                  {!currentDeliveries ? (
                    <CircularProgress
                      size={18}
                      aria-label="Loading delivery history"
                    />
                  ) : null}
                  {currentDeliveries?.available === false ? (
                    <Alert severity="info" sx={{ mt: 1 }}>
                      {unavailable(currentDeliveries)}
                    </Alert>
                  ) : null}
                  {currentDeliveries?.available && deliveryRows.length === 0 ? (
                    <Typography color="text.secondary" sx={{ mt: 1 }}>
                      No delivery attempts recorded.
                    </Typography>
                  ) : null}
                  {deliveryRows.map((delivery) => (
                    <ListItem
                      key={delivery.id}
                      divider
                      secondaryAction={
                        delivery.status === "dead_letter" ||
                        delivery.status === "failed" ? (
                          <Button
                            size="small"
                            onClick={() => void redrive(delivery)}
                            disabled={Boolean(busy) || detailLoading}
                          >
                            {busy === `redrive:${delivery.id}`
                              ? "Redriving…"
                              : "Redrive"}
                          </Button>
                        ) : null
                      }
                    >
                      <ListItemText
                        primary={`${delivery.id} · ${delivery.targetUrl || "endpoint"}`}
                        secondary={`${delivery.status || "unknown"} · ${delivery.attempts ?? 0} attempts${delivery.statusCode ? ` · HTTP ${delivery.statusCode}` : ""}${delivery.error ? ` · ${delivery.error}` : ""}`}
                      />
                    </ListItem>
                  ))}
                </Box>
              ) : null}
            </Grid>
          </Grid>
        ) : null}
      </CardContent>
    </Card>
  );
}

WebhookPanel.propTypes = {
  client: PropTypes.object.isRequired,
};
WorkspaceWebhooks.propTypes = WebhookPanel.propTypes;
