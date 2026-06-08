import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const CHANNEL_LABELS = {
  email: "Email",
  slack: "Slack",
  webhook: "Webhook",
};

const CHANNEL_ICONS = {
  email: "mdi:email-outline",
  slack: "mdi:slack",
  webhook: "mdi:webhook",
};

const FAMILY_ORDER = [
  "product_onboarding",
  "daily_quality_digest",
  "usage_budget",
  "gateway_alert",
  "observe_monitor",
  "eval_quality_alert",
  "workspace_admin",
];

const EXTERNAL_CHANNEL_TYPES = {
  slack_webhook: "slack",
  webhook: "webhook",
};

const EXTERNAL_CHANNEL_FAMILIES = new Set([
  "product_onboarding",
  "daily_quality_digest",
  "usage_budget",
  "gateway_alert",
  "observe_monitor",
  "eval_quality_alert",
]);

const STATUS_COLORS = {
  eligible: "info",
  suppressed: "warning",
  sent: "success",
  failed: "error",
  clicked: "secondary",
  completed: "success",
};

function decisionKey(family, channel) {
  return `${family}:${channel}`;
}

function preferencePayload({ family, channel, enabled, scope }) {
  return {
    preferences: [
      {
        scope,
        family,
        channel,
        enabled,
      },
    ],
  };
}

function preferenceScopeForFamily(family, canManageWorkspace) {
  if (family.user_controllable) return "user_workspace";
  if (family.workspace_controllable && canManageWorkspace) return "workspace";
  return "user_workspace";
}

function toggleDisabledForFamily(family, canManageWorkspace, isPending) {
  if (isPending) return true;
  if (family.user_controllable) return false;
  return !(family.workspace_controllable && canManageWorkspace);
}

function configuredExternalChannels(channels) {
  return Array.from(
    new Set(
      channels
        .filter((channel) => channel.is_active !== false)
        .map((channel) => EXTERNAL_CHANNEL_TYPES[channel.type])
        .filter(Boolean),
    ),
  );
}

function channelsForFamily(family, externalChannels) {
  const channels = new Set(family.default_channels || []);
  if (EXTERNAL_CHANNEL_FAMILIES.has(family.id)) {
    externalChannels.forEach((channel) => channels.add(channel));
  }
  return Array.from(channels);
}

function titleize(value) {
  return String(value || "")
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDateTime(value) {
  if (!value) return "Not sent";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not sent";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function channelUpdatePayload(channel, overrides = {}) {
  const payload = {
    id: channel.id,
    scope: channel.scope || "workspace",
    type: channel.type,
    display_name: overrides.display_name ?? channel.display_name,
    is_active:
      overrides.is_active ??
      (channel.is_active === undefined || channel.is_active),
  };
  if (overrides.config) payload.config = overrides.config;
  if (channel.metadata && Object.keys(channel.metadata).length > 0) {
    payload.metadata = channel.metadata;
  }
  return { channels: [payload] };
}

export default function NotificationSettingsPage() {
  const queryClient = useQueryClient();
  const [slackName, setSlackName] = useState("");
  const [slackWebhook, setSlackWebhook] = useState("");
  const [webhookName, setWebhookName] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [editingChannelId, setEditingChannelId] = useState(null);
  const [editChannelName, setEditChannelName] = useState("");
  const [editChannelUrl, setEditChannelUrl] = useState("");
  const [editChannelSecret, setEditChannelSecret] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => axios.get(endpoints.settings.notifications),
    select: (res) => res.data?.result,
  });

  const patchMutation = useMutation({
    mutationFn: (payload) =>
      axios.patch(endpoints.settings.notifications, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
    },
    onError: () =>
      enqueueSnackbar("Failed to update notification settings", {
        variant: "error",
      }),
  });

  const testMutation = useMutation({
    mutationFn: (channelId) =>
      axios.post(endpoints.settings.notificationChannelTest(channelId), {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
      enqueueSnackbar("Channel test recorded", { variant: "success" });
    },
    onError: () => enqueueSnackbar("Channel test failed", { variant: "error" }),
  });

  const decisions = useMemo(() => {
    const map = new Map();
    (data?.decisions || []).forEach((decision) => {
      map.set(decisionKey(decision.family, decision.channel), decision);
    });
    return map;
  }, [data]);

  const families = useMemo(() => {
    const byId = new Map(
      (data?.families || []).map((family) => [family.id, family]),
    );
    return FAMILY_ORDER.map((id) => byId.get(id)).filter(Boolean);
  }, [data]);

  const channels = useMemo(() => data?.channels || [], [data?.channels]);
  const deliveryLogs = useMemo(
    () => data?.delivery_logs || [],
    [data?.delivery_logs],
  );
  const familyLabels = useMemo(() => {
    const labels = new Map();
    (data?.families || []).forEach((family) => {
      labels.set(family.id, family.label);
    });
    return labels;
  }, [data?.families]);
  const canManageWorkspace = Boolean(data?.can_manage_workspace);
  const externalChannels = useMemo(
    () => configuredExternalChannels(channels),
    [channels],
  );

  const handleToggle = (family, channel, checked) => {
    patchMutation.mutate(
      preferencePayload({
        family: family.id,
        channel,
        enabled: checked,
        scope: preferenceScopeForFamily(family, canManageWorkspace),
      }),
    );
  };

  const handleAddSlack = () => {
    patchMutation.mutate(
      {
        channels: [
          {
            scope: "workspace",
            type: "slack_webhook",
            display_name: slackName || "Workspace Slack alerts",
            config: { webhook_url: slackWebhook },
            is_active: true,
          },
        ],
      },
      {
        onSuccess: () => {
          setSlackName("");
          setSlackWebhook("");
          enqueueSnackbar("Slack channel saved", { variant: "success" });
          queryClient.invalidateQueries({
            queryKey: ["notification-preferences"],
          });
        },
      },
    );
  };

  const handleAddWebhook = () => {
    patchMutation.mutate(
      {
        channels: [
          {
            scope: "workspace",
            type: "webhook",
            display_name: webhookName || "Workspace webhook alerts",
            config: {
              url: webhookUrl,
              ...(webhookSecret ? { secret: webhookSecret } : {}),
            },
            is_active: true,
          },
        ],
      },
      {
        onSuccess: () => {
          setWebhookName("");
          setWebhookUrl("");
          setWebhookSecret("");
          enqueueSnackbar("Webhook channel saved", { variant: "success" });
          queryClient.invalidateQueries({
            queryKey: ["notification-preferences"],
          });
        },
      },
    );
  };

  const handleToggleChannel = (channel, checked) => {
    patchMutation.mutate(
      channelUpdatePayload(channel, { is_active: checked }),
      {
        onSuccess: () => {
          enqueueSnackbar(checked ? "Channel enabled" : "Channel disabled", {
            variant: "success",
          });
        },
      },
    );
  };

  const handleEditChannel = (channel) => {
    setEditingChannelId(channel.id);
    setEditChannelName(channel.display_name || "");
    setEditChannelUrl("");
    setEditChannelSecret("");
  };

  const handleCancelEditChannel = () => {
    setEditingChannelId(null);
    setEditChannelName("");
    setEditChannelUrl("");
    setEditChannelSecret("");
  };

  const handleSaveChannel = (channel) => {
    const config = {};
    if (editChannelUrl && channel.type === "slack_webhook") {
      config.webhook_url = editChannelUrl;
    }
    if (editChannelUrl && channel.type === "webhook") {
      config.url = editChannelUrl;
      if (editChannelSecret) config.secret = editChannelSecret;
    }

    patchMutation.mutate(
      channelUpdatePayload(channel, {
        display_name: editChannelName.trim() || channel.display_name,
        ...(Object.keys(config).length > 0 ? { config } : {}),
      }),
      {
        onSuccess: () => {
          handleCancelEditChannel();
          enqueueSnackbar("Channel updated", { variant: "success" });
        },
      },
    );
  };

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error">Notification settings could not be loaded.</Alert>
    );
  }

  return (
    <Box sx={{ maxWidth: 1120, mx: "auto", py: 1 }}>
      <Stack spacing={3}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", md: "center" }}
          spacing={2}
        >
          <Box>
            <Typography variant="h4">Notifications</Typography>
            <Typography variant="body2" color="text.secondary">
              Manage onboarding, quality, usage, and workspace alert routing.
            </Typography>
          </Box>
          <Chip
            icon={<Iconify icon="mdi:shield-check-outline" width={18} />}
            label={canManageWorkspace ? "Workspace admin" : "Personal settings"}
            variant="outlined"
          />
        </Stack>

        <Paper variant="outlined" sx={{ borderRadius: 1 }}>
          <Stack spacing={0} divider={<Divider />}>
            {families.map((family) => (
              <Box
                key={family.id}
                data-testid={`notification-family-${family.id}`}
                sx={{ p: 2.25 }}
              >
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  spacing={2}
                  justifyContent="space-between"
                >
                  <Box sx={{ minWidth: 260 }}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="subtitle2">
                        {family.label}
                      </Typography>
                      {!family.non_critical && (
                        <Chip
                          label="Operational"
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      {family.description}
                    </Typography>
                  </Box>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={2}
                    flexWrap="wrap"
                  >
                    {channelsForFamily(family, externalChannels).map(
                      (channel) => {
                        const decision = decisions.get(
                          decisionKey(family.id, channel),
                        );
                        const checked = decision
                          ? decision.allowed !== false
                          : false;
                        const isOptional =
                          !family.default_channels.includes(channel);
                        return (
                          <FormControlLabel
                            key={channel}
                            control={
                              <Switch
                                checked={checked}
                                onChange={(event) =>
                                  handleToggle(
                                    family,
                                    channel,
                                    event.target.checked,
                                  )
                                }
                                disabled={toggleDisabledForFamily(
                                  family,
                                  canManageWorkspace,
                                  patchMutation.isPending,
                                )}
                                inputProps={{
                                  "aria-label": `${family.label} ${CHANNEL_LABELS[channel]}`,
                                }}
                              />
                            }
                            label={
                              <Stack
                                direction="row"
                                spacing={0.75}
                                alignItems="center"
                              >
                                <Iconify
                                  icon={CHANNEL_ICONS[channel]}
                                  width={18}
                                />
                                <Typography variant="body2">
                                  {CHANNEL_LABELS[channel]}
                                </Typography>
                                {isOptional && (
                                  <Chip
                                    label="Opt-in"
                                    size="small"
                                    variant="outlined"
                                  />
                                )}
                              </Stack>
                            }
                          />
                        );
                      },
                    )}
                  </Stack>
                </Stack>
              </Box>
            ))}
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ p: 2.25, borderRadius: 1 }}>
          <Stack spacing={2}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              justifyContent="space-between"
              spacing={2}
            >
              <Box>
                <Typography variant="subtitle1">Workspace channels</Typography>
                <Typography variant="body2" color="text.secondary">
                  Add opt-in Slack or webhook routing for onboarding nudges and
                  operational alerts.
                </Typography>
              </Box>
              {!canManageWorkspace && (
                <Chip label="Read only" size="small" variant="outlined" />
              )}
            </Stack>

            {channels.length > 0 && (
              <Stack spacing={1}>
                {channels.map((channel) => (
                  <Box
                    key={channel.id}
                    sx={{
                      p: 1.5,
                      border: (theme) => `1px solid ${theme.palette.divider}`,
                      borderRadius: 1,
                    }}
                  >
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      justifyContent="space-between"
                      spacing={1.5}
                      alignItems={{ xs: "flex-start", sm: "center" }}
                    >
                      <Stack direction="row" spacing={1.25} alignItems="center">
                        <Iconify
                          icon={
                            channel.type === "slack_webhook"
                              ? "mdi:slack"
                              : "mdi:webhook"
                          }
                          width={20}
                        />
                        <Box>
                          <Typography variant="body2" fontWeight={600}>
                            {channel.display_name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {channel.target_identifier || channel.type}
                          </Typography>
                        </Box>
                      </Stack>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Chip
                          label={channel.is_active ? "Active" : "Disabled"}
                          color={channel.is_active ? "success" : "default"}
                          size="small"
                          variant="outlined"
                        />
                        <Switch
                          size="small"
                          checked={
                            channel.is_active === undefined || channel.is_active
                          }
                          disabled={
                            !canManageWorkspace || patchMutation.isPending
                          }
                          onChange={(event) =>
                            handleToggleChannel(channel, event.target.checked)
                          }
                          inputProps={{
                            "aria-label": `${channel.display_name} active`,
                          }}
                        />
                        <IconButton
                          size="small"
                          disabled={!canManageWorkspace}
                          onClick={() => handleEditChannel(channel)}
                          title="Edit channel"
                          aria-label={`Edit ${channel.display_name}`}
                        >
                          <Iconify icon="mdi:pencil-outline" width={18} />
                        </IconButton>
                        <IconButton
                          size="small"
                          disabled={
                            !canManageWorkspace || testMutation.isPending
                          }
                          onClick={() => testMutation.mutate(channel.id)}
                          title="Test channel"
                          aria-label={`Test ${channel.display_name}`}
                        >
                          <Iconify icon="mdi:send-outline" width={18} />
                        </IconButton>
                      </Stack>
                    </Stack>
                    {editingChannelId === channel.id && (
                      <Stack
                        direction={{ xs: "column", md: "row" }}
                        spacing={1.5}
                        sx={{ mt: 1.5 }}
                      >
                        <TextField
                          label="Channel display name"
                          value={editChannelName}
                          onChange={(event) =>
                            setEditChannelName(event.target.value)
                          }
                          size="small"
                          fullWidth
                        />
                        <TextField
                          label={
                            channel.type === "slack_webhook"
                              ? "New Slack webhook URL"
                              : "New webhook URL"
                          }
                          value={editChannelUrl}
                          onChange={(event) =>
                            setEditChannelUrl(event.target.value)
                          }
                          helperText="Leave blank to keep the current endpoint."
                          size="small"
                          fullWidth
                          type="password"
                        />
                        {channel.type === "webhook" && (
                          <TextField
                            label="New webhook token"
                            value={editChannelSecret}
                            onChange={(event) =>
                              setEditChannelSecret(event.target.value)
                            }
                            helperText="Requires a new webhook URL."
                            size="small"
                            fullWidth
                            type="password"
                          />
                        )}
                        <Stack direction="row" spacing={1}>
                          <Button
                            variant="contained"
                            onClick={() => handleSaveChannel(channel)}
                            disabled={
                              patchMutation.isPending ||
                              !editChannelName.trim() ||
                              Boolean(editChannelSecret && !editChannelUrl)
                            }
                          >
                            Save
                          </Button>
                          <Button
                            variant="text"
                            onClick={handleCancelEditChannel}
                          >
                            Cancel
                          </Button>
                        </Stack>
                      </Stack>
                    )}
                  </Box>
                ))}
              </Stack>
            )}

            <Stack spacing={1.5}>
              <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
                <TextField
                  label="Slack channel name"
                  value={slackName}
                  onChange={(event) => setSlackName(event.target.value)}
                  size="small"
                  fullWidth
                  disabled={!canManageWorkspace}
                />
                <TextField
                  label="Slack webhook URL"
                  value={slackWebhook}
                  onChange={(event) => setSlackWebhook(event.target.value)}
                  size="small"
                  fullWidth
                  disabled={!canManageWorkspace}
                  type="password"
                />
                <Button
                  variant="contained"
                  startIcon={<Iconify icon="mdi:plus" />}
                  onClick={handleAddSlack}
                  disabled={
                    !canManageWorkspace ||
                    !slackWebhook ||
                    patchMutation.isPending
                  }
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Add Slack
                </Button>
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
                <TextField
                  label="Webhook name"
                  value={webhookName}
                  onChange={(event) => setWebhookName(event.target.value)}
                  size="small"
                  fullWidth
                  disabled={!canManageWorkspace}
                />
                <TextField
                  label="Webhook URL"
                  value={webhookUrl}
                  onChange={(event) => setWebhookUrl(event.target.value)}
                  size="small"
                  fullWidth
                  disabled={!canManageWorkspace}
                  type="password"
                />
                <TextField
                  label="Webhook token"
                  value={webhookSecret}
                  onChange={(event) => setWebhookSecret(event.target.value)}
                  size="small"
                  fullWidth
                  disabled={!canManageWorkspace}
                  type="password"
                />
                <Button
                  variant="outlined"
                  startIcon={<Iconify icon="mdi:plus" />}
                  onClick={handleAddWebhook}
                  disabled={
                    !canManageWorkspace ||
                    !webhookUrl ||
                    patchMutation.isPending
                  }
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Add Webhook
                </Button>
              </Stack>
            </Stack>
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ p: 2.25, borderRadius: 1 }}>
          <Stack spacing={2}>
            <Box>
              <Typography variant="subtitle1">Delivery log</Typography>
              <Typography variant="body2" color="text.secondary">
                Recent notification sends, suppressions, tests, and failures.
              </Typography>
            </Box>

            {deliveryLogs.length === 0 ? (
              <Alert severity="info">No delivery events yet.</Alert>
            ) : (
              <TableContainer>
                <Table size="small" aria-label="Notification delivery log">
                  <TableHead>
                    <TableRow>
                      <TableCell>Event</TableCell>
                      <TableCell>Channel</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Recipient</TableCell>
                      <TableCell align="right">Time</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {deliveryLogs.map((log) => (
                      <TableRow key={log.id}>
                        <TableCell>
                          <Stack spacing={0.25}>
                            <Typography variant="body2" fontWeight={600}>
                              {familyLabels.get(log.family) ||
                                titleize(log.family)}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {log.notification_key || log.source_type}
                              {log.stage ? ` - ${log.stage}` : ""}
                            </Typography>
                          </Stack>
                        </TableCell>
                        <TableCell>{CHANNEL_LABELS[log.channel]}</TableCell>
                        <TableCell>
                          <Stack
                            direction="row"
                            spacing={0.75}
                            alignItems="center"
                          >
                            <Chip
                              label={titleize(log.status)}
                              color={STATUS_COLORS[log.status] || "default"}
                              size="small"
                              variant="outlined"
                            />
                            {log.suppressed_reason && (
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {titleize(log.suppressed_reason)}
                              </Typography>
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          {log.recipient_identifier_masked ||
                            log.recipient_type ||
                            "Workspace"}
                        </TableCell>
                        <TableCell align="right">
                          {formatDateTime(log.sent_at || log.created_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Stack>
        </Paper>
      </Stack>
    </Box>
  );
}
