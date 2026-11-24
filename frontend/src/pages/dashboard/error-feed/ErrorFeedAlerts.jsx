import EmptyState from "src/components/empty-state";
import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Alert as MuiAlert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControl,
  FormHelperText,
  IconButton,
  InputLabel,
  MenuItem,
  Pagination,
  PaginationItem,
  Select,
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
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import SvgColor from "src/components/svg-color";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useAuthContext } from "src/auth/hooks";
import { paths } from "src/routes/paths";
import { getErrorMessage } from "src/sections/settings/integrations/utils";
import {
  useIntegrationConnections,
  useSlackChannels,
} from "src/api/integrations";
import {
  useDeleteErrorFeedAlert,
  useErrorFeedAlertOptions,
  useErrorFeedAlerts,
  useSaveErrorFeedAlert,
  useTestErrorFeedAlert,
} from "src/api/errorFeed/alerts";
import { LoadingScreen } from "src/components/loading-screen";

const TRIGGERS = [
  { value: "new_issue", label: "A new issue is created" },
  { value: "severity_reached", label: "Severity reaches a level" },
  { value: "escalating", label: "Issue status changes to escalating" },
  {
    value: "occurrences_crossed",
    label: "Occurrence count crosses a threshold",
  },
];
const SEVERITIES = ["critical", "high", "medium", "low"];
const SOURCES = ["scanner", "eval"];
const COOLDOWNS = [0, 300, 900, 3600, 86400];
const ALERT_DRAFT_KEY = "error-feed-slack-alert-draft-v1";

const ALERT_PRESETS = [
  {
    id: "escalating",
    label: "Escalating issues",
    caption: "Alert when any issue begins escalating",
    name: "Escalating production issues",
    trigger: "escalating",
    triggerValue: "",
    severities: ["critical", "high"],
    cooldown: 3600,
  },
  {
    id: "high_critical",
    label: "High & Critical",
    caption: "Alert when an issue reaches High or Critical",
    name: "High & critical issues",
    trigger: "severity_reached",
    triggerValue: "high",
    severities: [],
    cooldown: 3600,
  },
  {
    id: "spike",
    label: "Volume spike (10+)",
    caption: "Alert when occurrences cross 10 events",
    name: "High-volume issue spike",
    trigger: "occurrences_crossed",
    triggerValue: 10,
    severities: [],
    cooldown: 900,
  },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function alertErrorField(attr) {
  const fields = {
    name: "name",
    project_id: "project",
    trigger_type: "trigger",
    severity_threshold: "triggerValue",
    occurrence_threshold: "triggerValue",
    slack_connection_id: "connection",
    slack_channel_id: "channel",
    email_recipients: "emails",
    cooldown_seconds: "cooldown",
    "filters.sources": "sources",
    "filters.severities": "severities",
    "filters.statuses": "statuses",
    "filters.issue_categories": "issueCategories",
    "filters.issue_groups": "issueGroups",
  };
  return fields[attr] || "form";
}

function readReturningDraft() {
  const sp = new URLSearchParams(window.location.search);
  if (sp.has("resume_alert")) {
    try {
      const draft = JSON.parse(sessionStorage.getItem(ALERT_DRAFT_KEY) || "null");
      if (draft && typeof draft === "object") return draft;
    } catch {
      return null;
    }
    return null;
  }
  if (sp.get("create") === "1") {
    return {
      projectId: sp.get("project_id") || "",
      trigger: "escalating",
    };
  }
  return null;
}

const optionLabel = (value) =>
  String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

const labels = (values, fallback = "Any") =>
  Array.isArray(values) && values.length
    ? values.map(optionLabel).join(", ")
    : fallback;

const placeholder = (value) => (
  <Typography component="span" color="text.secondary">
    {value}
  </Typography>
);

const cooldownLabel = (seconds) => {
  if (seconds === 0) return "On every matching event";
  if (seconds < 3600) return `Once per issue every ${seconds / 60} minutes`;
  if (seconds === 3600) return "Once per issue every 1 hour";
  return "Once per issue every 24 hours";
};

const toTags = (value) =>
  (Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(",")
      : []
  )
    .map((item) => item.trim())
    .filter((item, index, items) => item && items.indexOf(item) === index);

function summaryFor(rule) {
  const type = rule.trigger_type || rule.configuration?.trigger_type;
  const value =
    type === "severity_reached"
      ? rule.severity_threshold ?? rule.configuration?.severity_threshold
      : rule.occurrence_threshold ?? rule.configuration?.occurrence_threshold;
  const trigger =
    TRIGGERS.find((item) => item.value === type)?.label ||
    type ||
    "Issue changes";
  if (type === "severity_reached") return `${trigger}: ${value || "high"}`;
  if (type === "occurrences_crossed") return `${trigger}: ${value || 1}`;
  return trigger;
}

function AlertRuleForm({ rule, draft, onClose }) {
  const navigate = useNavigate();
  const auth = useAuthContext();
  const currentUserEmail = auth?.user?.email || "";
  const edit = Boolean(rule);
  const config = rule?.configuration || rule || {};
  const connectionsQuery = useIntegrationConnections();
  const allConnections = connectionsQuery.data || [];
  const slackConnections = allConnections.filter(
    (connection) => connection.platform === "slack",
  );
  const optionQuery = useErrorFeedAlertOptions();
  const projects = optionQuery.data?.projects || [];
  const [name, setName] = useState(draft?.name ?? rule?.name ?? "");
  const [nameTouched, setNameTouched] = useState(Boolean(edit || draft?.name));
  const [projectId, setProjectId] = useState(
    draft?.projectId ?? config.project_id ?? rule?.project_id ?? "",
  );
  const selectedProject = projects.find(
    (project) => String(project.value || project.id) === String(projectId),
  );
  const selectedProjectName =
    selectedProject?.label || selectedProject?.name || "Selected project";
  const initialTrigger = draft?.trigger ?? config.trigger_type ?? "new_issue";
  const [trigger, setTrigger] = useState(initialTrigger);
  const [triggerValue, setTriggerValue] = useState(
    draft?.triggerValue ??
      (initialTrigger === "occurrences_crossed"
        ? config.occurrence_threshold ?? 5
        : initialTrigger === "severity_reached"
          ? config.severity_threshold ?? "high"
          : ""),
  );
  const [sources, setSources] = useState(
    draft?.sources ?? config.filters?.sources ?? [],
  );
  const [severities, setSeverities] = useState(
    draft?.severities ?? config.filters?.severities ?? [],
  );
  const [statuses, setStatuses] = useState(
    draft?.statuses ?? config.filters?.statuses ?? [],
  );
  const [issueCategories, setIssueCategories] = useState(() =>
    toTags(draft?.issueCategories ?? config.filters?.issue_categories),
  );
  const [issueGroups, setIssueGroups] = useState(() =>
    toTags(draft?.issueGroups ?? config.filters?.issue_groups),
  );
  const [slackEnabled, setSlackEnabled] = useState(() =>
    Boolean(
      draft?.slackEnabled ??
        rule?.slack_enabled ??
        (edit ? Boolean(rule?.slack_channel_id) : true),
    ),
  );
  const [emailEnabled, setEmailEnabled] = useState(() =>
    Boolean(
      draft?.emailEnabled ??
        rule?.email_enabled ??
        ((rule?.email_recipients || []).length > 0),
    ),
  );
  const [emailRecipients, setEmailRecipients] = useState(() =>
    toTags(draft?.emailRecipients ?? rule?.email_recipients),
  );
  const [emailInput, setEmailInput] = useState("");
  const [connectionId, setConnectionId] = useState(
    rule?.slack_connection_id || "",
  );
  const effectiveConnectionId = connectionId || slackConnections[0]?.id || "";
  const [channelId, setChannelId] = useState(
    rule?.slack_channel_id || "",
  );
  const [channelSearch, setChannelSearch] = useState("");
  const [cooldown, setCooldown] = useState(
    draft?.cooldown ?? config.cooldown_seconds ?? 3600,
  );
  const [fieldErrors, setFieldErrors] = useState({});
  const channelsQuery = useSlackChannels(effectiveConnectionId);
  const {
    hasNextPage,
    isFetchingNextPage,
    isError: channelsError,
    fetchNextPage,
  } = channelsQuery;
  const channels = channelsQuery.data?.channels || [];
  const selectedChannel =
    channels.find((channel) => String(channel.id) === String(channelId)) ||
    (channelId
      ? {
          id: channelId,
          name: rule?.slack_channel_name || "Previously selected channel",
        }
      : null);
  const selectedChannelName =
    channels.find((item) => String(item.id) === String(channelId))?.name ||
    selectedChannel?.name ||
    null;
  const save = useSaveErrorFeedAlert({ showErrorToast: false });

  const clearFieldError = (field) =>
    setFieldErrors((previous) => ({
      ...previous,
      [field]: undefined,
      form: undefined,
    }));
  const showFieldError = (field, message) =>
    setFieldErrors({ [field]: message });

  useEffect(() => {
    if (!effectiveConnectionId || (!channelSearch.trim() && channels.length > 0)) return;
    if (hasNextPage && !isFetchingNextPage && !channelsError) {
      fetchNextPage();
    }
  }, [
    effectiveConnectionId,
    channelSearch,
    channels.length,
    hasNextPage,
    isFetchingNextPage,
    channelsError,
    fetchNextPage,
  ]);

  const loadChannelsOnScroll = (event) => {
    const menu = event.currentTarget;
    const nearBottom =
      menu.scrollHeight - menu.scrollTop - menu.clientHeight <= 48;
    if (
      nearBottom &&
      channelsQuery.hasNextPage &&
      !channelsQuery.isFetchingNextPage
    ) {
      channelsQuery.fetchNextPage();
    }
  };

  const applyPreset = (preset) => {
    setTrigger(preset.trigger);
    setTriggerValue(preset.triggerValue);
    setSeverities(preset.severities);
    setCooldown(preset.cooldown);
    if (!nameTouched || ALERT_PRESETS.some((p) => p.name === name)) {
      setName(preset.name);
    }
    setFieldErrors({});
  };

  const onTriggerChange = (value) => {
    setTrigger(value);
    setTriggerValue(
      value === "occurrences_crossed"
        ? 5
        : value === "severity_reached"
          ? "high"
          : "",
    );
    clearFieldError("trigger");
    clearFieldError("triggerValue");
  };
  const setMulti = (setter, field) => (event) => {
    setter(event.target.value);
    clearFieldError(field);
  };

  const connectSlack = () => {
    sessionStorage.setItem(
      ALERT_DRAFT_KEY,
      JSON.stringify({
        name,
        projectId,
        trigger,
        triggerValue,
        sources,
        severities,
        statuses,
        issueCategories,
        issueGroups,
        cooldown,
        slackEnabled,
        emailEnabled,
        emailRecipients,
      }),
    );
    const returnTo = encodeURIComponent(paths.dashboard.errorFeed.alerts);
    navigate(
      `${paths.dashboard.settings.integrations}?return_to=${returnTo}&platform=slack`,
    );
  };

  const commitEmailInput = (rawText) => {
    const extra = toTags(rawText);
    if (!extra.length) return emailRecipients;
    const merged = toTags([...emailRecipients, ...extra]);
    setEmailRecipients(merged);
    setEmailInput("");
    clearFieldError("emails");
    return merged;
  };

  const submit = () => {
    setFieldErrors({});
    if (!name.trim())
      return showFieldError("name", "Enter a name for this rule.");
    if (optionQuery.isLoading || optionQuery.isError)
      return showFieldError(
        "project",
        "Could not load alert options. Try again before saving.",
      );
    if (!slackEnabled && !emailEnabled)
      return showFieldError(
        "channels",
        "Turn on Slack or Email (or both) to choose where alerts are sent.",
      );
    if (
      trigger === "occurrences_crossed" &&
      (!Number.isInteger(Number(triggerValue)) || Number(triggerValue) < 1)
    )
      return showFieldError(
        "triggerValue",
        "Occurrence threshold must be a positive whole number.",
      );
    if (slackEnabled) {
      if (connectionsQuery.isLoading || connectionsQuery.isError)
        return showFieldError(
          "connection",
          "Could not load Slack connections. Try again before saving.",
        );
      if (!effectiveConnectionId)
        return showFieldError("connection", "Connect Slack and choose a workspace.");
      if (!channelId) return showFieldError("channel", "Choose a Slack channel.");
      if (channelsQuery.isLoading || channelsQuery.isError)
        return showFieldError(
          "channel",
          "Could not verify the Slack channel. Try loading channels again.",
        );
      if (!channels.some((channel) => String(channel.id) === String(channelId)))
        return showFieldError("channel", "Choose an available Slack channel.");
    }
    const resolvedEmails = emailEnabled ? commitEmailInput(emailInput) : [];
    if (emailEnabled) {
      if (resolvedEmails.length === 0)
        return showFieldError("emails", "Add at least one email address.");
      const badEmail = resolvedEmails.find((addr) => !EMAIL_RE.test(addr));
      if (badEmail)
        return showFieldError(
          "emails",
          `Enter a valid email address (“${badEmail}”).`,
        );
    }
    const body = {
      name: name.trim(),
      enabled: rule?.enabled ?? true,
      project_id: projectId || null,
      trigger_type: trigger,
      ...(trigger === "severity_reached"
        ? { severity_threshold: triggerValue }
        : trigger === "occurrences_crossed"
          ? { occurrence_threshold: triggerValue }
          : {}),
      filters: {
        sources,
        severities,
        statuses,
        issue_categories: issueCategories,
        issue_groups: issueGroups,
      },
      slack_enabled: slackEnabled,
      ...(slackEnabled
        ? {
            slack_connection_id: effectiveConnectionId,
            slack_channel_id: channelId,
            ...(selectedChannelName
              ? { slack_channel_name: selectedChannelName }
              : {}),
          }
        : {
            slack_connection_id: null,
            slack_channel_id: null,
            slack_channel_name: null,
          }),
      email_enabled: emailEnabled,
      email_recipients: emailEnabled ? resolvedEmails : [],
      cooldown_seconds: Number(cooldown),
    };
    save.mutate(
      { ...(rule?.id ? { id: rule.id } : {}), ...body },
      {
        onSuccess: onClose,
        onError: (error) => {
          const message = getErrorMessage(
            error,
            "Could not save this alert rule.",
          );
          showFieldError(alertErrorField(error?.response?.data?.attr), message);
        },
      },
    );
  };

  const activePresetId = ALERT_PRESETS.find(
    (p) =>
      p.trigger === trigger &&
      String(p.triggerValue) === String(triggerValue || "") &&
      p.severities.join(",") === severities.join(","),
  )?.id;

  const summaryDestinations = [
    slackEnabled ? (selectedChannelName ? `#${selectedChannelName}` : "Slack") : null,
    emailEnabled
      ? emailRecipients.length === 1
        ? emailRecipients[0]
        : emailRecipients.length > 1
          ? `${emailRecipients[0]} +${emailRecipients.length - 1} more`
          : "email"
      : null,
  ].filter(Boolean);

  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Drawer Header ── */}
      <Box
        sx={{
          px: 2.25,
          py: 1.75,
          borderBottom: 1,
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          gap={1}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography typography="m2" fontWeight="fontWeightMedium">
              {edit ? "Edit Error Feed alert" : "Create Error Feed alert"}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              Notify Slack and/or email when agent issues need attention.
            </Typography>
          </Box>
          <IconButton
            size="small"
            onClick={onClose}
            aria-label="Close"
            sx={{ flexShrink: 0 }}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Stack>
      </Box>

      {/* Scrollable form body */}
      <Box
        sx={{
          px: 2.25,
          py: 2,
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
        }}
      >
        <Stack spacing={2.25} sx={{ minWidth: 0 }}>
          {(optionQuery.isLoading || connectionsQuery.isLoading) && (
            <Typography typography="s2" color="text.secondary">
              Loading alert settings…
            </Typography>
          )}

          {/* Quick-start templates (create mode) */}
          {!edit && (
            <Stack gap={0.75}>
              <Typography typography="s3" fontWeight={600} color="text.secondary">
                Quick templates
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 0.75,
                }}
              >
                {ALERT_PRESETS.map((preset) => {
                  const active = activePresetId === preset.id;
                  return (
                    <Box
                      key={preset.id}
                      component="button"
                      type="button"
                      onClick={() => applyPreset(preset)}
                      sx={{
                        textAlign: "left",
                        p: 1,
                        borderRadius: "6px",
                        border: "1px solid",
                        borderColor: active ? "text.primary" : "divider",
                        bgcolor: active ? "action.selected" : "transparent",
                        cursor: "pointer",
                        fontFamily: "inherit",
                        transition: "border-color 120ms, background-color 120ms",
                        "&:hover": {
                          borderColor: active ? "text.primary" : "text.secondary",
                          bgcolor: active ? "action.selected" : "action.hover",
                        },
                      }}
                    >
                      <Typography
                        typography="s2"
                        fontWeight={600}
                        color="text.primary"
                        noWrap
                      >
                        {preset.label}
                      </Typography>
                      <Typography
                        typography="s3"
                        color="text.secondary"
                        sx={{
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                          mt: 0.2,
                          lineHeight: 1.35,
                        }}
                      >
                        {preset.caption}
                      </Typography>
                    </Box>
                  );
                })}
              </Box>
            </Stack>
          )}

          {/* Step 1: Destinations */}
          <Stack gap={1.25}>
            <SectionTitle
              step="1"
              title="Where to notify"
              subtitle="Send alerts to Slack, email, or both."
            />

            {/* Channel destination toggles */}
            <Stack gap={0.5}>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(2, 1fr)",
                  gap: 0.85,
                }}
              >
                {[
                  {
                    id: "slack",
                    label: "Slack",
                    hint: slackEnabled
                      ? selectedChannelName
                        ? `#${selectedChannelName}`
                        : slackConnections.length > 0
                          ? "Pick a channel"
                          : "Connect workspace"
                      : "Off",
                    icon: "simple-icons:slack",
                    active: slackEnabled,
                    onToggle: () => {
                      setSlackEnabled((v) => !v);
                      clearFieldError("channels");
                      clearFieldError("connection");
                      clearFieldError("channel");
                    },
                  },
                  {
                    id: "email",
                    label: "Email",
                    hint: emailEnabled
                      ? emailRecipients.length > 0
                        ? `${emailRecipients.length} recipient${emailRecipients.length === 1 ? "" : "s"}`
                        : "Add recipients"
                      : "Off",
                    icon: "solar:letter-linear",
                    active: emailEnabled,
                    onToggle: () => {
                      setEmailEnabled((v) => !v);
                      clearFieldError("channels");
                      clearFieldError("emails");
                    },
                  },
                ].map((dest) => (
                  <Box
                    key={dest.id}
                    component="button"
                    type="button"
                    aria-pressed={dest.active}
                    onClick={dest.onToggle}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 1,
                      px: 1.25,
                      py: 0.9,
                      borderRadius: "8px",
                      border: "1px solid",
                      borderColor: dest.active ? "text.primary" : "divider",
                      bgcolor: dest.active ? "action.selected" : "transparent",
                      color: "text.primary",
                      fontFamily: "inherit",
                      textAlign: "left",
                      cursor: "pointer",
                      transition: "border-color 120ms, background-color 120ms",
                      "&:hover": {
                        borderColor: dest.active ? "text.primary" : "text.secondary",
                        bgcolor: dest.active ? "action.selected" : "action.hover",
                      },
                    }}
                  >
                    <Stack direction="row" alignItems="center" gap={0.85} sx={{ minWidth: 0 }}>
                      <Iconify
                        icon={dest.icon}
                        width={16}
                        sx={{
                          color: dest.active ? "text.primary" : "text.secondary",
                          flexShrink: 0,
                        }}
                      />
                      <Box sx={{ minWidth: 0 }}>
                        <Typography typography="s2" fontWeight={600} color="text.primary" noWrap>
                          {dest.label}
                        </Typography>
                        <Typography typography="s3" color="text.secondary" noWrap>
                          {dest.hint}
                        </Typography>
                      </Box>
                    </Stack>
                    <Iconify
                      icon={
                        dest.active
                          ? "solar:check-circle-bold"
                          : "solar:add-circle-linear"
                      }
                      width={16}
                      sx={{
                        color: dest.active ? "text.primary" : "text.disabled",
                        flexShrink: 0,
                      }}
                    />
                  </Box>
                ))}
              </Box>
              {fieldErrors.channels && (
                <FormHelperText error>{fieldErrors.channels}</FormHelperText>
              )}
            </Stack>

            {slackEnabled && (
              <>
                {connectionsQuery.isLoading ? null : connectionsQuery.isError ? (
                  <MuiAlert severity="error" variant="outlined">
                    Could not load Slack connections.{" "}
                    <Button size="small" onClick={() => connectionsQuery.refetch?.()}>
                      Retry
                    </Button>
                  </MuiAlert>
                ) : slackConnections.length === 0 ? (
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    gap={1.5}
                    sx={{
                      p: 1.5,
                      borderRadius: "8px",
                      border: "1px solid",
                      borderColor: fieldErrors.connection
                        ? "status.danger.border"
                        : "status.warning.border",
                      bgcolor: fieldErrors.connection
                        ? "status.danger.bg"
                        : "status.warning.bg",
                    }}
                  >
                    <Stack direction="row" alignItems="flex-start" gap={1.1} sx={{ minWidth: 0 }}>
                      <Iconify
                        icon="simple-icons:slack"
                        width={16}
                        sx={{
                          color: fieldErrors.connection
                            ? "status.danger.fg"
                            : "status.warning.fg",
                          flexShrink: 0,
                          mt: 0.15,
                        }}
                      />
                      <Stack gap={0.2} sx={{ minWidth: 0 }}>
                        <Typography typography="s2" fontWeight={600} color="text.primary">
                          Slack workspace not connected
                        </Typography>
                        <Typography typography="s3" color="text.secondary">
                          {fieldErrors.connection ||
                            "Connect Slack once to pick a channel, or enable Email above."}
                        </Typography>
                      </Stack>
                    </Stack>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={connectSlack}
                      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                    >
                      Connect Slack
                    </Button>
                  </Stack>
                ) : (
                  <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
                    <FormControl
                      size="small"
                      sx={{ flex: 1, minWidth: 0 }}
                      error={Boolean(fieldErrors.connection)}
                    >
                      <InputLabel id="workspace-label" shrink>
                        Slack workspace
                      </InputLabel>
                      <Select
                        labelId="workspace-label"
                        label="Slack workspace"
                        displayEmpty
                        value={effectiveConnectionId}
                        onChange={(e) => {
                          setConnectionId(e.target.value);
                          setChannelId("");
                          setChannelSearch("");
                          clearFieldError("connection");
                          clearFieldError("channel");
                        }}
                        renderValue={(value) => {
                          const connection = slackConnections.find(
                            (item) => String(item.id) === String(value),
                          );
                          return value
                            ? connection?.display_name ||
                                connection?.name ||
                                "Previously selected workspace"
                            : placeholder("Select workspace");
                        }}
                      >
                        {slackConnections.map((connection) => (
                          <MenuItem key={connection.id} value={connection.id}>
                            {connection.display_name ||
                              connection.name ||
                              "Slack workspace"}
                          </MenuItem>
                        ))}
                      </Select>
                      {fieldErrors.connection && (
                        <FormHelperText>{fieldErrors.connection}</FormHelperText>
                      )}
                    </FormControl>

                    <Autocomplete
                      size="small"
                      sx={{ flex: 1.2, minWidth: 0 }}
                      autoHighlight
                      openOnFocus
                      disabled={!effectiveConnectionId || channelsQuery.isLoading}
                      options={channels}
                      value={selectedChannel}
                      onChange={(_, channel) => {
                        setChannelId(channel?.id || "");
                        clearFieldError("channel");
                      }}
                      onInputChange={(_, value, reason) => {
                        if (reason === "input") setChannelSearch(value);
                        if (reason === "clear") setChannelSearch("");
                      }}
                      onClose={() => setChannelSearch("")}
                      getOptionLabel={(channel) => `#${channel.name}`}
                      isOptionEqualToValue={(option, value) =>
                        String(option.id) === String(value.id)
                      }
                      loading={
                        channelsQuery.isLoading || channelsQuery.isFetchingNextPage
                      }
                      loadingText="Loading channels…"
                      noOptionsText={
                        channelsQuery.hasNextPage
                          ? "Searching more channels…"
                          : "No channels found"
                      }
                      ListboxProps={{
                        sx: { maxHeight: 280, overflowY: "auto" },
                        onScroll: loadChannelsOnScroll,
                      }}
                      renderOption={(props, channel) => (
                        <li {...props} key={channel.id}>
                          #{channel.name}
                          {channel.is_private ? " · private" : ""}
                        </li>
                      )}
                      renderInput={(params) => (
                        <TextField
                          {...params}
                          label="Channel"
                          error={Boolean(
                            fieldErrors.channel || channelsQuery.isError,
                          )}
                          helperText={
                            fieldErrors.channel ||
                            (channelsQuery.isError ? (
                              <>
                                Could not load channels.{" "}
                                <Button
                                  size="small"
                                  onClick={() => channelsQuery.refetch?.()}
                                >
                                  Retry
                                </Button>
                              </>
                            ) : undefined)
                          }
                          placeholder={
                            effectiveConnectionId
                              ? channelsQuery.isLoading
                                ? "Loading channels…"
                                : "Select #channel"
                              : "Select workspace first"
                          }
                          InputLabelProps={{ shrink: true }}
                          InputProps={{
                            ...params.InputProps,
                            endAdornment: (
                              <>
                                {(channelsQuery.isLoading ||
                                  channelsQuery.isFetchingNextPage) && (
                                  <CircularProgress size={16} />
                                )}
                                {params.InputProps.endAdornment}
                              </>
                            ),
                          }}
                        />
                      )}
                    />
                  </Stack>
                )}
              </>
            )}

            {emailEnabled && (
              <Stack gap={0.75}>
                <Autocomplete
                  multiple
                  freeSolo
                  autoSelect
                  size="small"
                  options={[]}
                  value={emailRecipients}
                  inputValue={emailInput}
                  onInputChange={(_, value, reason) => {
                    if (reason === "input") {
                      if (value.includes(",")) {
                        commitEmailInput(value);
                        return;
                      }
                      setEmailInput(value);
                      clearFieldError("emails");
                    } else if (reason === "clear") {
                      setEmailInput("");
                    }
                  }}
                  onChange={(_, nextValue) => {
                    setEmailRecipients(toTags(nextValue));
                    setEmailInput("");
                    clearFieldError("emails");
                  }}
                  renderTags={(selected, getTagProps) =>
                    selected.map((addr, index) => (
                      <Chip
                        {...getTagProps({ index })}
                        key={addr}
                        label={addr}
                        size="small"
                        sx={{
                          maxWidth: "calc(100% - 8px)",
                          bgcolor: "action.selected",
                          color: "text.primary",
                          "& .MuiChip-deleteIcon": { color: "text.secondary" },
                        }}
                      />
                    ))
                  }
                  sx={{
                    minWidth: 0,
                    "& .MuiAutocomplete-tag": { maxWidth: "calc(100% - 8px)" },
                  }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Email recipients"
                      InputLabelProps={{ shrink: true }}
                      error={Boolean(fieldErrors.emails)}
                      placeholder={
                        emailRecipients.length
                          ? "Add another email"
                          : "oncall@company.com"
                      }
                      helperText={
                        fieldErrors.emails ||
                        "Press Enter or comma to add team members or distribution lists."
                      }
                    />
                  )}
                />
                {currentUserEmail && !emailRecipients.includes(currentUserEmail) && (
                  <Stack direction="row" alignItems="center">
                    <Button
                      size="small"
                      variant="text"
                      startIcon={<Iconify icon="solar:add-linear" width={14} />}
                      onClick={() => {
                        setEmailRecipients((prev) =>
                          toTags([...prev, currentUserEmail]),
                        );
                        clearFieldError("emails");
                      }}
                      sx={{ color: "text.secondary" }}
                    >
                      Add my email ({currentUserEmail})
                    </Button>
                  </Stack>
                )}
              </Stack>
            )}
          </Stack>

          <Divider />

          {/* Step 2: Trigger & Scope */}
          <Stack gap={1.5}>
            <SectionTitle
              step="2"
              title="When to alert"
              subtitle="Choose which project, event, and severities trigger this rule."
            />

            <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
              <FormControl
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
                error={Boolean(fieldErrors.trigger)}
              >
                <InputLabel id="when-label" shrink>
                  Issue event
                </InputLabel>
                <Select
                  labelId="when-label"
                  label="Issue event"
                  value={trigger}
                  onChange={(e) => onTriggerChange(e.target.value)}
                >
                  {TRIGGERS.map((item) => (
                    <MenuItem key={item.value} value={item.value}>
                      {item.label}
                    </MenuItem>
                  ))}
                </Select>
                {fieldErrors.trigger && (
                  <FormHelperText>{fieldErrors.trigger}</FormHelperText>
                )}
              </FormControl>

              <FormControl
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
                error={Boolean(fieldErrors.project || optionQuery.isError)}
              >
                <InputLabel id="project-label" shrink>
                  Project
                </InputLabel>
                <Select
                  labelId="project-label"
                  label="Project"
                  displayEmpty
                  value={projectId}
                  onChange={(e) => {
                    setProjectId(e.target.value);
                    clearFieldError("project");
                  }}
                  renderValue={(value) =>
                    value
                      ? selectedProjectName
                      : placeholder("All projects in this workspace")
                  }
                >
                  <MenuItem value="">All projects in this workspace</MenuItem>
                  {projects.map((project) => (
                    <MenuItem
                      key={project.value || project.id}
                      value={project.value || project.id}
                    >
                      {project.label || project.name}
                    </MenuItem>
                  ))}
                </Select>
                {(fieldErrors.project || optionQuery.isError) && (
                  <FormHelperText>
                    {fieldErrors.project || "Could not load projects."}
                  </FormHelperText>
                )}
              </FormControl>
            </Stack>

            {trigger === "severity_reached" && (
              <FormControl
                fullWidth
                size="small"
                error={Boolean(fieldErrors.triggerValue)}
              >
                <InputLabel id="severity-trigger-label" shrink>
                  Severity
                </InputLabel>
                <Select
                  labelId="severity-trigger-label"
                  label="Severity"
                  value={triggerValue}
                  onChange={(e) => {
                    setTriggerValue(e.target.value);
                    clearFieldError("triggerValue");
                  }}
                >
                  {SEVERITIES.map((value) => (
                    <MenuItem key={value} value={value}>
                      {optionLabel(value)}
                    </MenuItem>
                  ))}
                </Select>
                {fieldErrors.triggerValue && (
                  <FormHelperText>{fieldErrors.triggerValue}</FormHelperText>
                )}
              </FormControl>
            )}

            {trigger === "occurrences_crossed" && (
              <TextField
                size="small"
                label="Occurrence threshold"
                placeholder="For example, 10"
                InputLabelProps={{ shrink: true }}
                type="number"
                inputProps={{ min: 1 }}
                value={triggerValue}
                onChange={(e) => {
                  setTriggerValue(
                    e.target.value === "" ? "" : Number(e.target.value),
                  );
                  clearFieldError("triggerValue");
                }}
                error={Boolean(fieldErrors.triggerValue)}
                helperText={fieldErrors.triggerValue}
              />
            )}
          </Stack>

          <Divider />

          {/* Step 3: Rule name & Frequency */}
          <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
            <TextField
              size="small"
              label="Rule name"
              placeholder="e.g., Critical production issues"
              InputLabelProps={{ shrink: true }}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameTouched(true);
                clearFieldError("name");
              }}
              error={Boolean(fieldErrors.name)}
              helperText={fieldErrors.name}
              sx={{ flex: 1.25, minWidth: 0 }}
            />
            <FormControl
              size="small"
              sx={{ flex: 1, minWidth: 0 }}
              error={Boolean(fieldErrors.cooldown)}
            >
              <InputLabel id="cooldown-label" shrink>
                Notify at most
              </InputLabel>
              <Select
                labelId="cooldown-label"
                label="Notify at most"
                value={cooldown}
                onChange={(e) => {
                  setCooldown(e.target.value);
                  clearFieldError("cooldown");
                }}
              >
                {COOLDOWNS.map((seconds) => (
                  <MenuItem key={seconds} value={seconds}>
                    {cooldownLabel(seconds)}
                  </MenuItem>
                ))}
              </Select>
              {fieldErrors.cooldown && (
                <FormHelperText>{fieldErrors.cooldown}</FormHelperText>
              )}
            </FormControl>
          </Stack>

          <Divider />

          {/* Step 4: Optional filters */}
          <Stack spacing={1.5}>
            <SectionTitle
              step="3"
              title="If the issue matches"
              subtitle="Leave blank to match any source, severity, status, category, or group."
            />
            {fieldErrors.filters && (
              <FormHelperText error>{fieldErrors.filters}</FormHelperText>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
              <FormControl
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
                error={Boolean(fieldErrors.sources)}
              >
                <InputLabel id="source-label" shrink>
                  Sources
                </InputLabel>
                <Select
                  multiple
                  labelId="source-label"
                  label="Sources"
                  displayEmpty
                  value={sources}
                  onChange={setMulti(setSources, "sources")}
                  renderValue={(values) =>
                    values.length ? labels(values) : placeholder("Any source")
                  }
                >
                  {SOURCES.map((value) => (
                    <MenuItem key={value} value={value}>
                      {optionLabel(value)}
                    </MenuItem>
                  ))}
                </Select>
                {fieldErrors.sources && (
                  <FormHelperText>{fieldErrors.sources}</FormHelperText>
                )}
              </FormControl>
              <FormControl
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
                error={Boolean(fieldErrors.severities)}
              >
                <InputLabel id="severity-filter-label" shrink>
                  Severity filter
                </InputLabel>
                <Select
                  multiple
                  labelId="severity-filter-label"
                  label="Severity filter"
                  displayEmpty
                  value={severities}
                  onChange={setMulti(setSeverities, "severities")}
                  renderValue={(values) =>
                    values.length ? labels(values) : placeholder("Any severity")
                  }
                >
                  {SEVERITIES.map((value) => (
                    <MenuItem key={value} value={value}>
                      {optionLabel(value)}
                    </MenuItem>
                  ))}
                </Select>
                {fieldErrors.severities && (
                  <FormHelperText>{fieldErrors.severities}</FormHelperText>
                )}
              </FormControl>
              <FormControl
                size="small"
                sx={{ flex: 1, minWidth: 0 }}
                error={Boolean(fieldErrors.statuses)}
              >
                <InputLabel id="status-filter-label" shrink>
                  Status
                </InputLabel>
                <Select
                  multiple
                  labelId="status-filter-label"
                  label="Status"
                  displayEmpty
                  value={statuses}
                  onChange={setMulti(setStatuses, "statuses")}
                  renderValue={(values) =>
                    values.length ? labels(values) : placeholder("Any status")
                  }
                >
                  {["escalating", "for_review", "acknowledged", "resolved"].map(
                    (value) => (
                      <MenuItem key={value} value={value}>
                        {optionLabel(value)}
                      </MenuItem>
                    ),
                  )}
                </Select>
                {fieldErrors.statuses && (
                  <FormHelperText>{fieldErrors.statuses}</FormHelperText>
                )}
              </FormControl>
            </Stack>
            <TagFilter
              label="Issue categories"
              example="Timeout"
              value={issueCategories}
              onChange={(value) => {
                setIssueCategories(value);
                clearFieldError("issueCategories");
              }}
              error={fieldErrors.issueCategories}
            />
            <TagFilter
              label="Issue groups"
              example="Tool Failures"
              value={issueGroups}
              onChange={(value) => {
                setIssueGroups(value);
                clearFieldError("issueGroups");
              }}
              error={fieldErrors.issueGroups}
            />
          </Stack>
        </Stack>
      </Box>

      {/* Pinned footer */}
      <Box
        sx={{
          px: 2.25,
          py: 1.5,
          borderTop: 1,
          borderColor: "divider",
          flexShrink: 0,
          bgcolor: "background.paper",
        }}
      >
        <Stack
          direction="row"
          alignItems="flex-start"
          gap={0.85}
          sx={{
            px: 1.25,
            py: 0.85,
            mb: 1.25,
            borderRadius: "6px",
            bgcolor: "action.hover",
          }}
        >
          <Iconify
            icon={
              slackEnabled && !emailEnabled
                ? "simple-icons:slack"
                : emailEnabled && !slackEnabled
                  ? "solar:letter-linear"
                  : "solar:bell-bing-linear"
            }
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0, mt: 0.2 }}
          />
          <Typography typography="s3" color="text.secondary" sx={{ lineHeight: 1.45 }}>
            When{" "}
            <Box component="strong" sx={{ color: "text.primary", fontWeight: 600 }}>
              {TRIGGERS.find((item) => item.value === trigger)?.label.toLowerCase()}
              {trigger === "severity_reached"
                ? ` (${optionLabel(triggerValue)})`
                : trigger === "occurrences_crossed"
                  ? ` (${triggerValue || 1}+)`
                  : ""}
            </Box>
            {severities.length > 0 ? ` [${labels(severities)}]` : ""} in{" "}
            <Box component="strong" sx={{ color: "text.primary", fontWeight: 600 }}>
              {projectId ? selectedProjectName : "all projects"}
            </Box>
            {" → notify "}
            <Box component="strong" sx={{ color: "text.primary", fontWeight: 600 }}>
              {summaryDestinations.length > 0
                ? summaryDestinations.join(" & ")
                : "no destination selected"}
            </Box>
            .
          </Typography>
        </Stack>

        {fieldErrors.form && (
          <FormHelperText error sx={{ mb: 1 }}>
            {fieldErrors.form}
          </FormHelperText>
        )}
        <Stack
          direction="row"
          justifyContent="flex-end"
          flexWrap="wrap"
          gap={1}
        >
          <Button size="small" onClick={onClose}>
            Cancel
          </Button>
          <LoadingButton
            size="small"
            variant="contained"
            onClick={submit}
            loading={save.isPending}
            disabled={
              optionQuery.isLoading ||
              (slackEnabled &&
                (connectionsQuery.isLoading || channelsQuery.isLoading))
            }
          >
            {edit ? "Save changes" : "Create alert"}
          </LoadingButton>
        </Stack>
      </Box>
    </Box>
  );
}

function TagFilter({ label, example, value, onChange, error }) {
  return (
    <Autocomplete
      multiple
      freeSolo
      autoSelect
      size="small"
      options={[]}
      value={value}
      onChange={(_, nextValue) => onChange(toTags(nextValue))}
      renderTags={(selected, getTagProps) =>
        selected.map((item, index) => (
          <Chip
            {...getTagProps({ index })}
            key={item}
            label={item}
            size="small"
            sx={{
              maxWidth: "calc(100% - 8px)",
              bgcolor: "action.selected",
              color: "text.primary",
              "& .MuiChip-deleteIcon": { color: "text.secondary" },
            }}
          />
        ))
      }
      sx={{
        minWidth: 0,
        "& .MuiAutocomplete-tag": { maxWidth: "calc(100% - 8px)" },
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          error={Boolean(error)}
          placeholder={value.length ? "Add another" : `For example, ${example}`}
          helperText={error || "Type a name and press Enter to add it."}
        />
      )}
    />
  );
}

TagFilter.propTypes = {
  label: PropTypes.string.isRequired,
  example: PropTypes.string.isRequired,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
};

function SectionTitle({ step, title, subtitle }) {
  return (
    <Box pt={0.75}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Chip label={step} size="small" />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          {title}
        </Typography>
      </Stack>
      <Typography typography="s2" color="text.secondary" mt={0.5}>
        {subtitle}
      </Typography>
    </Box>
  );
}

export default function ErrorFeedAlerts() {
  const navigate = useNavigate();
  const [returningDraft] = useState(readReturningDraft);
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).has("resume_alert"))
      return;
    // Consume only after commit: a discarded render must still find the draft.
    try {
      sessionStorage.removeItem(ALERT_DRAFT_KEY);
    } catch {
      // Unavailable storage must not prevent using the already restored form.
    }
  }, [returningDraft]);
  const alertsQuery = useErrorFeedAlerts();
  const { data: rawRules = [], isLoading, isError } = alertsQuery;
  const rules = useMemo(
    () => rawRules.map((row) => ({ ...(row.configuration || {}), ...row })),
    [rawRules],
  );
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  useEffect(() => {
    setPage((current) =>
      Math.min(current, Math.max(0, Math.ceil(rules.length / pageSize) - 1)),
    );
  }, [rules.length, pageSize]);
  const visibleRules = rules.slice(page * pageSize, (page + 1) * pageSize);
  const [editing, setEditing] = useState(null);
  const [createOpen, setCreateOpen] = useState(Boolean(returningDraft));
  const save = useSaveErrorFeedAlert();
  const remove = useDeleteErrorFeedAlert();
  const test = useTestErrorFeedAlert();
  const ruleDrawerOpen = createOpen || Boolean(editing);
  const ruleDrawerCovered = useDrawerCovered(ruleDrawerOpen);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        gap={1.5}
        sx={{
          px: 2,
          pt: 2,
          pb: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Button
            size="small"
            startIcon={<Iconify icon="solar:alt-arrow-left-linear" />}
            onClick={() => navigate(paths.dashboard.errorFeed.root)}
          >
            Error Feed
          </Button>
          <Typography typography="m2" fontWeight="fontWeightSemiBold">
            Alert rules
          </Typography>
          <Typography typography="s2" color="text.secondary">
            Notify Slack or email when an issue matches your rule.
          </Typography>
        </Box>
        <Button
          size="small"
          variant="contained"
          startIcon={<Iconify icon="solar:add-linear" width={20} />}
          onClick={() => setCreateOpen(true)}
        >
          Create alert
        </Button>
      </Stack>
      <Box
        sx={{
          p: 2,
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
        }}
      >
        {isLoading && <LoadingScreen compact />}
        {isError && (
          <MuiAlert severity="error">
            Could not load Error Feed alert rules.
          </MuiAlert>
        )}
        {!isLoading && !isError && rules.length === 0 && (
          <Section variant="panel" sx={{ p: 0 }}>
            <EmptyState
              icon="solar:bell-linear"
              title="No Error Feed alerts yet"
              description="Create a rule to send matching issue notifications to Slack or email."
              action={
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() => setCreateOpen(true)}
                >
                  Create alert
                </Button>
              }
              sx={{ pt: 6, pb: 6 }}
            />
          </Section>
        )}
        {!isLoading && !isError && rules.length > 0 && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              flex: 1,
              minHeight: 0,
            }}
          >
            <TableContainer
              sx={{
                flex: 1,
                overflowX: "auto",
                overflowY: "auto",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
              }}
            >
              <Table stickyHeader size="small" sx={{ minWidth: 980 }}>
                <TableHead>
                  <TableRow
                    sx={{
                      "& .MuiTableCell-head": {
                        bgcolor: "background.neutral",
                        color: "text.secondary",
                        fontWeight: "fontWeightMedium",
                        whiteSpace: "nowrap",
                        py: 1.5,
                      },
                    }}
                  >
                    <TableCell>Rule name</TableCell>
                    <TableCell>When</TableCell>
                    <TableCell>Project</TableCell>
                    <TableCell>Destinations</TableCell>
                    <TableCell>Last sent</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {visibleRules.map((rule) => {
                    const hasSlack =
                      rule.slack_enabled !== false &&
                      Boolean(rule.slack_channel_name || rule.slack_channel_id);
                    const slackLabel =
                      rule.slack_channel_name || "Slack channel";
                    const recipients = toTags(rule.email_recipients);
                    const hasEmail =
                      rule.email_enabled !== false && recipients.length > 0;
                    const emailLabel =
                      recipients.length === 1
                        ? recipients[0]
                        : `${recipients[0]} +${recipients.length - 1}`;
                    return (
                    <TableRow
                      key={rule.id}
                      hover
                      sx={{
                        height: ROW_HEIGHT.default,
                        "& .MuiTableCell-body": {
                          borderColor: "divider",
                          py: 1,
                        },
                      }}
                    >
                      <TableCell sx={{ minWidth: 180 }}>
                        <Typography
                          typography="s1"
                          fontWeight="fontWeightMedium"
                        >
                          {rule.name || "Untitled alert"}
                        </Typography>
                        {(rule.health === "failed" ||
                          rule.error ||
                          rule.failure_reason) && (
                          <Typography
                            typography="s3"
                            color="status.warning.fg"
                            sx={{ display: "block", mt: 0.5 }}
                          >
                            {rule.failure_reason ||
                              rule.error ||
                              "Alert delivery needs attention."}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell sx={{ minWidth: 190 }}>
                        <Typography typography="s1">
                          {summaryFor(rule)}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography typography="s1">
                          {rule.project_scope ||
                            (rule.project_id
                              ? "Selected project"
                              : "All projects")}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Stack gap={0.35}>
                          {hasSlack && (
                            <Stack direction="row" alignItems="center" gap={0.55}>
                              <Iconify
                                icon="simple-icons:slack"
                                width={14}
                                sx={{ color: "text.secondary", flexShrink: 0 }}
                              />
                              <Typography typography="s2" noWrap>
                                #{slackLabel}
                              </Typography>
                            </Stack>
                          )}
                          {hasEmail && (
                            <Stack direction="row" alignItems="center" gap={0.55}>
                              <Iconify
                                icon="solar:letter-linear"
                                width={14}
                                sx={{ color: "text.secondary", flexShrink: 0 }}
                              />
                              <Typography typography="s2" noWrap>
                                {emailLabel}
                              </Typography>
                            </Stack>
                          )}
                          {!hasSlack && !hasEmail && (
                            <Typography typography="s2" color="text.secondary">
                              —
                            </Typography>
                          )}
                        </Stack>
                      </TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap" }}>
                        <Typography typography="s1">
                          {rule.last_triggered_at
                            ? new Date(rule.last_triggered_at).toLocaleString()
                            : "Never"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Stack direction="row" alignItems="center" gap={0.5}>
                          <Switch
                            size="small"
                            checked={rule.enabled !== false}
                            inputProps={{
                              "aria-label": `Toggle ${rule.name || "alert"}`,
                            }}
                            onChange={(e) =>
                              save.mutate({
                                id: rule.id,
                                enabled: e.target.checked,
                              })
                            }
                          />
                          <StatusBadge
                            status={
                              rule.enabled === false ? "paused" : "active"
                            }
                            dot={false}
                          />
                        </Stack>
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                        <Stack
                          direction="row"
                          alignItems="center"
                          justifyContent="flex-end"
                          gap={0.5}
                        >
                          <LoadingButton
                            size="small"
                            variant="outlined"
                            loading={
                              test.isPending &&
                              String(test.variables) === String(rule.id)
                            }
                            onClick={() => test.mutate(rule.id)}
                            startIcon={
                              <Iconify
                                icon="solar:play-circle-linear"
                                width={16}
                              />
                            }
                          >
                            Test
                          </LoadingButton>
                          <IconButton
                            size="small"
                            aria-label={`Edit ${rule.name || "alert"}`}
                            onClick={() => setEditing(rule)}
                          >
                            <SvgColor
                              src="/assets/icons/ic_edit.svg"
                              sx={{ width: 18, height: 18 }}
                            />
                          </IconButton>
                          <IconButton
                            size="small"
                            aria-label={`Delete ${rule.name || "alert"}`}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete “${rule.name || "this alert"}”?`,
                                )
                              )
                                remove.mutate(rule.id);
                            }}
                          >
                            <SvgColor
                              src="/assets/icons/ic_delete.svg"
                              sx={{ width: 18, height: 18 }}
                            />
                          </IconButton>
                        </Stack>
                      </TableCell>
                    </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ p: 1, py: 1.5, flexShrink: 0 }}
            >
              <Stack gap={1} direction="row" alignItems="center">
                <Typography
                  typography="s2"
                  color="text.primary"
                  fontWeight="fontWeightRegular"
                >
                  Results per page
                </Typography>
                <Select
                  size="small"
                  aria-label="Results per page"
                  value={pageSize}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(0);
                  }}
                  sx={{ height: 36, bgcolor: "background.paper" }}
                >
                  {[10, 25, 50].map((size) => (
                    <MenuItem key={size} value={size}>
                      {size}
                    </MenuItem>
                  ))}
                </Select>
              </Stack>
              <Pagination
                count={Math.ceil(rules.length / pageSize)}
                variant="outlined"
                shape="rounded"
                page={page + 1}
                color="primary"
                onChange={(_, value) => setPage(value - 1)}
                renderItem={(item) => (
                  <PaginationItem
                    {...item}
                    sx={{ borderRadius: "4px", bgcolor: "background.paper" }}
                    slots={{
                      previous: () => (
                        <Box display="flex" alignItems="center" gap={0.5}>
                          <Iconify
                            icon="solar:alt-arrow-left-linear"
                            width={18}
                            height={18}
                            sx={{ path: { strokeWidth: 1.5 } }}
                          />
                          Back
                        </Box>
                      ),
                      next: () => (
                        <Box display="flex" alignItems="center" gap={0.5}>
                          Next
                          <Iconify
                            icon="solar:alt-arrow-right-linear"
                            width={18}
                            height={18}
                            sx={{ path: { strokeWidth: 1.5 } }}
                          />
                        </Box>
                      ),
                    }}
                  />
                )}
              />
            </Stack>
          </Box>
        )}
      </Box>
      <Drawer
        anchor="right"
        open={ruleDrawerOpen}
        onClose={() => {
          setCreateOpen(false);
          setEditing(null);
        }}
        sx={coveredDrawerSx(ruleDrawerCovered)}
        PaperProps={{
          sx: { width: 560, maxWidth: "100vw", overflow: "hidden" },
        }}
      >
        {(createOpen || editing) && (
          <AlertRuleForm
            rule={editing}
            draft={editing ? null : returningDraft}
            onClose={() => {
              setCreateOpen(false);
              setEditing(null);
            }}
          />
        )}
      </Drawer>
    </Box>
  );
}

SectionTitle.propTypes = {
  step: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string.isRequired,
};

AlertRuleForm.propTypes = {
  rule: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    configuration: PropTypes.object,
    project_id: PropTypes.string,
    slack_enabled: PropTypes.bool,
    slack_connection_id: PropTypes.string,
    slack_channel_id: PropTypes.string,
    slack_channel_name: PropTypes.string,
    email_enabled: PropTypes.bool,
    email_recipients: PropTypes.oneOfType([
      PropTypes.arrayOf(PropTypes.string),
      PropTypes.string,
    ]),
    enabled: PropTypes.bool,
  }),
  draft: PropTypes.shape({
    name: PropTypes.string,
    projectId: PropTypes.string,
    trigger: PropTypes.string,
    triggerValue: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    sources: PropTypes.arrayOf(PropTypes.string),
    severities: PropTypes.arrayOf(PropTypes.string),
    statuses: PropTypes.arrayOf(PropTypes.string),
    issueCategories: PropTypes.oneOfType([
      PropTypes.arrayOf(PropTypes.string),
      PropTypes.string,
    ]),
    issueGroups: PropTypes.oneOfType([
      PropTypes.arrayOf(PropTypes.string),
      PropTypes.string,
    ]),
    cooldown: PropTypes.number,
    slackEnabled: PropTypes.bool,
    emailEnabled: PropTypes.bool,
    emailRecipients: PropTypes.oneOfType([
      PropTypes.arrayOf(PropTypes.string),
      PropTypes.string,
    ]),
  }),
  onClose: PropTypes.func.isRequired,
};
