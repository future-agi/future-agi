import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Alert as MuiAlert,
  Autocomplete,
  Box,
  Button,
  Card,
  Chip,
  CircularProgress,
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
import SvgColor from "src/components/svg-color";
import { getErrorMessage } from "src/sections/settings/integrations/utils";
import { useIntegrationConnections, useSlackChannels } from "src/api/integrations";
import {
  useDeleteErrorFeedAlert,
  useErrorFeedAlertOptions,
  useErrorFeedAlerts,
  useSaveErrorFeedAlert,
  useTestErrorFeedAlert,
} from "src/api/errorFeed/alerts";

const TRIGGERS = [
  { value: "new_issue", label: "A new issue is created" },
  { value: "severity_reached", label: "Severity reaches a level" },
  { value: "escalating", label: "Issue status changes to escalating" },
  { value: "occurrences_crossed", label: "Occurrence count crosses a threshold" },
];
const SEVERITIES = ["low", "medium", "high", "critical"];
const SOURCES = ["scanner", "eval"];
const COOLDOWNS = [0, 300, 900, 3600, 86400];
const ALERT_DRAFT_KEY = "error-feed-slack-alert-draft-v1";

function alertErrorField(message) {
  const text = message.toLowerCase();
  if (text.includes("reconnect slack") || text.includes("slack connection") || text.includes("slack workspace")) return "connection";
  if (text.includes("channel")) return "channel";
  if (text.includes("name")) return "name";
  if (text.includes("project") || text.includes("alert options")) return "project";
  if (text.includes("occurrence") || text.includes("threshold") || text.includes("trigger does not take a value")) return "triggerValue";
  if (text.includes("trigger")) return "trigger";
  if (text.includes("unsupported error feed filter") || text.includes("unsupported filter")) return "filters";
  if (text.includes("source")) return "sources";
  if (text.includes("severit")) return "severities";
  if (text.includes("status")) return "statuses";
  if (text.includes("categor")) return "issueCategories";
  if (text.includes("group")) return "issueGroups";
  if (text.includes("cooldown")) return "cooldown";
  return "form";
}

function readReturningDraft() {
  if (!new URLSearchParams(window.location.search).has("resume_alert")) return null;
  try {
    const draft = JSON.parse(sessionStorage.getItem(ALERT_DRAFT_KEY) || "null");
    sessionStorage.removeItem(ALERT_DRAFT_KEY);
    return draft && typeof draft === "object" ? draft : null;
  } catch {
    sessionStorage.removeItem(ALERT_DRAFT_KEY);
    return null;
  }

}

const optionLabel = (value) =>
  String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const labels = (values, fallback = "Any") =>
  Array.isArray(values) && values.length ? values.map(optionLabel).join(", ") : fallback;

const placeholder = (value) => <Typography component="span" color="text.secondary">{value}</Typography>;

const cooldownLabel = (seconds) => {
  if (seconds === 0) return "On every matching event";
  if (seconds < 3600) return `Once per issue every ${seconds / 60} minutes`;
  if (seconds === 3600) return "Once per issue every 1 hour";
  return "Once per issue every 24 hours";
};

const toTags = (value) =>
  (Array.isArray(value) ? value : typeof value === "string" ? value.split(",") : [])
    .map((item) => item.trim())
    .filter((item, index, items) => item && items.indexOf(item) === index);

function summaryFor(rule) {
  const type = rule.trigger_type || rule.configuration?.trigger_type;
  const value = rule.trigger_value ?? rule.configuration?.trigger_value;
  const trigger = TRIGGERS.find((item) => item.value === type)?.label || type || "Issue changes";
  if (type === "severity_reached") return `${trigger}: ${value || "high"}`;
  if (type === "occurrences_crossed") return `${trigger}: ${value || 1}`;
  return trigger;
}

function AlertRuleForm({ rule, draft, onClose }) {
  const navigate = useNavigate();
  const edit = Boolean(rule);
  const config = rule?.configuration || rule || {};
  const connectionsQuery = useIntegrationConnections();
  const allConnections = connectionsQuery.data || [];
  const slackConnections = allConnections.filter((connection) => connection.platform === "slack");
  const optionQuery = useErrorFeedAlertOptions();
  const projects = optionQuery.data?.projects || [];
  const [name, setName] = useState(draft?.name ?? rule?.name ?? "");
  const [projectId, setProjectId] = useState(draft?.projectId ?? config.project_id ?? rule?.project_id ?? "");
  const [trigger, setTrigger] = useState(draft?.trigger ?? config.trigger_type ?? "new_issue");
  const [triggerValue, setTriggerValue] = useState(draft?.triggerValue ?? config.trigger_value ?? "high");
  const [sources, setSources] = useState(draft?.sources ?? config.filters?.sources ?? []);
  const [severities, setSeverities] = useState(draft?.severities ?? config.filters?.severities ?? []);
  const [statuses, setStatuses] = useState(draft?.statuses ?? config.filters?.statuses ?? []);
  const [issueCategories, setIssueCategories] = useState(() => toTags(draft?.issueCategories ?? config.filters?.issue_categories));
  const [issueGroups, setIssueGroups] = useState(() => toTags(draft?.issueGroups ?? config.filters?.issue_groups));
  const [connectionId, setConnectionId] = useState(config.slack_connection_id || rule?.slack_connection_id || "");
  const [channelId, setChannelId] = useState(config.slack_channel_id || rule?.slack_channel_id || "");
  const [channelSearch, setChannelSearch] = useState("");
  const [cooldown, setCooldown] = useState(draft?.cooldown ?? config.cooldown_seconds ?? 3600);
  const [fieldErrors, setFieldErrors] = useState({});
  const channelsQuery = useSlackChannels(connectionId);
  const { hasNextPage, isFetchingNextPage, isError: channelsError, fetchNextPage } = channelsQuery;
  const channels = channelsQuery.data?.channels || [];
  const selectedChannel = channels.find((channel) => String(channel.id) === String(channelId))
    || (channelId ? { id: channelId, name: config.slack_channel_name || rule?.slack_channel_name || "Previously selected channel" } : null);
  const save = useSaveErrorFeedAlert({ showErrorToast: false });

  const clearFieldError = (field) => setFieldErrors((previous) => ({ ...previous, [field]: undefined, form: undefined }));
  const showFieldError = (field, message) => setFieldErrors({ [field]: message });

  useEffect(() => {
    if (!connectionId || (!channelSearch.trim() && channels.length > 0)) return;
    if (hasNextPage && !isFetchingNextPage && !channelsError) {
      fetchNextPage();
    }
  }, [connectionId, channelSearch, channels.length, hasNextPage, isFetchingNextPage, channelsError, fetchNextPage]);

  const loadChannelsOnScroll = (event) => {
    const menu = event.currentTarget;
    const nearBottom = menu.scrollHeight - menu.scrollTop - menu.clientHeight <= 48;
    if (nearBottom && channelsQuery.hasNextPage && !channelsQuery.isFetchingNextPage) {
      channelsQuery.fetchNextPage();
    }
  };

  const onTriggerChange = (value) => {
    setTrigger(value);
    setTriggerValue(value === "occurrences_crossed" ? 5 : value === "severity_reached" ? "high" : "");
    clearFieldError("trigger");
    clearFieldError("triggerValue");
  };
  const setMulti = (setter, field) => (event) => {
    setter(event.target.value);
    clearFieldError(field);
  };


  const connectSlack = () => {
    sessionStorage.setItem(ALERT_DRAFT_KEY, JSON.stringify({
      name, projectId, trigger, triggerValue, sources, severities, statuses,
      issueCategories, issueGroups, cooldown,
    }));
    const returnTo = encodeURIComponent("/dashboard/error-feed/alerts");
    navigate(`/dashboard/settings/integrations?return_to=${returnTo}&platform=slack`);
  };

  const submit = () => {
    setFieldErrors({});
    if (!name.trim()) return showFieldError("name", "Enter a name for this rule.");
    if (optionQuery.isLoading || optionQuery.isError) return showFieldError("project", "Could not load alert options. Try again before saving.");
    if (connectionsQuery.isLoading || connectionsQuery.isError) return showFieldError("connection", "Could not load Slack connections. Try again before saving.");
    if (trigger === "occurrences_crossed" && (!Number.isInteger(Number(triggerValue)) || Number(triggerValue) < 1)) return showFieldError("triggerValue", "Occurrence threshold must be a positive whole number.");
    if (!connectionId) return showFieldError("connection", "Choose a Slack workspace.");
    if (!channelId) return showFieldError("channel", "Choose a Slack channel.");
    if (channelsQuery.isLoading || channelsQuery.isError) return showFieldError("channel", "Could not verify the Slack channel. Try loading channels again.");
    if (!channels.some((channel) => String(channel.id) === String(channelId))) return showFieldError("channel", "Choose an available Slack channel.");
    const body = {
      name: name.trim(),
      enabled: rule?.enabled ?? true,
      project_id: projectId || null,
      trigger_type: trigger,
      ...(trigger === "severity_reached" || trigger === "occurrences_crossed" ? { trigger_value: triggerValue } : {}),
      filters: {
        sources,
        severities,
        statuses,
        issue_categories: issueCategories,
        issue_groups: issueGroups,
      },
      slack_connection_id: connectionId,
      slack_channel_id: channelId,
      cooldown_seconds: Number(cooldown),
    };
    save.mutate({ ...(rule?.id ? { id: rule.id } : {}), ...body }, {
      onSuccess: onClose,
      onError: (error) => {
        const message = getErrorMessage(error, "Could not save this alert rule.");
        showFieldError(alertErrorField(message), message);
      },
    });
  };

  return (
    <Box sx={{ width: "100%", height: "100%", boxSizing: "border-box", display: "flex", flexDirection: "column", overflow: "hidden", bgcolor: "background.paper" }}>
      <Box sx={{ p: { xs: 2, sm: 2.5 }, borderBottom: 1, borderColor: "divider", flexShrink: 0 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={1}>
          <Box sx={{ minWidth: 0 }}>
            <Typography typography="m2" fontWeight="fontWeightSemiBold">{edit ? "Edit Error Feed alert" : "Create Error Feed alert"}</Typography>
            <Typography typography="s2" color="text.secondary">Choose when an issue should send a message to Slack.</Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close" sx={{ flexShrink: 0 }}><Iconify icon="mingcute:close-line" /></IconButton>
        </Stack>
      </Box>
      <Box sx={{ p: { xs: 2, sm: 2.5 }, flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
        <Stack spacing={2.25} sx={{ minWidth: 0 }}>
          {(optionQuery.isLoading || connectionsQuery.isLoading) && <Typography typography="s2" color="text.secondary">Loading alert settings…</Typography>}
          <TextField size="small" label="Rule name" placeholder="For example, Critical production issues" InputLabelProps={{ shrink: true }} value={name} onChange={(e) => { setName(e.target.value); clearFieldError("name"); }} error={Boolean(fieldErrors.name)} helperText={fieldErrors.name} fullWidth />
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.project || optionQuery.isError)}>
            <InputLabel id="project-label" shrink>Project</InputLabel>
            <Select labelId="project-label" label="Project" displayEmpty value={projectId} onChange={(e) => { setProjectId(e.target.value); clearFieldError("project"); }} renderValue={(value) => value ? projects.find((project) => String(project.value || project.id) === String(value))?.label || "Selected project" : placeholder("All projects in this workspace")}><MenuItem value="">All projects in this workspace</MenuItem>{projects.map((project) => <MenuItem key={project.value || project.id} value={project.value || project.id}>{project.label || project.name}</MenuItem>)}</Select>
            {(fieldErrors.project || optionQuery.isError) && <FormHelperText>{fieldErrors.project || "Could not load alert options."}{optionQuery.isError && <Button size="small" onClick={() => optionQuery.refetch?.()}>Retry</Button>}</FormHelperText>}
          </FormControl>
          <SectionTitle step="1" title="When" subtitle="Choose the issue event that starts this rule." />
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.trigger)}>
            <InputLabel id="when-label">Issue event</InputLabel>
            <Select labelId="when-label" label="Issue event" value={trigger} onChange={(e) => onTriggerChange(e.target.value)}>
              {TRIGGERS.map((item) => <MenuItem key={item.value} value={item.value}>{item.label}</MenuItem>)}
            </Select>
            {fieldErrors.trigger && <FormHelperText>{fieldErrors.trigger}</FormHelperText>}
          </FormControl>
          {trigger === "severity_reached" && (
            <FormControl fullWidth size="small" error={Boolean(fieldErrors.triggerValue)}><InputLabel id="severity-trigger-label">Severity</InputLabel><Select labelId="severity-trigger-label" label="Severity" value={triggerValue} onChange={(e) => { setTriggerValue(e.target.value); clearFieldError("triggerValue"); }}>{SEVERITIES.map((value) => <MenuItem key={value} value={value}>{optionLabel(value)}</MenuItem>)}</Select>{fieldErrors.triggerValue && <FormHelperText>{fieldErrors.triggerValue}</FormHelperText>}</FormControl>
          )}
          {trigger === "occurrences_crossed" && <TextField size="small" label="Occurrence threshold" placeholder="For example, 5" InputLabelProps={{ shrink: true }} type="number" inputProps={{ min: 1 }} value={triggerValue} onChange={(e) => { setTriggerValue(e.target.value); clearFieldError("triggerValue"); }} error={Boolean(fieldErrors.triggerValue)} helperText={fieldErrors.triggerValue} />}

          <SectionTitle step="2" title="If" subtitle="Limit the rule to matching issues. Each selected value is an OR; fields combine with AND." />
          {fieldErrors.filters && <FormHelperText error>{fieldErrors.filters}</FormHelperText>}
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.sources)}><InputLabel id="source-label" shrink>Sources</InputLabel><Select multiple labelId="source-label" label="Sources" displayEmpty value={sources} onChange={setMulti(setSources, "sources")} renderValue={(values) => values.length ? labels(values) : placeholder("Any source")}>{SOURCES.map((value) => <MenuItem key={value} value={value}>{optionLabel(value)}</MenuItem>)}</Select>{fieldErrors.sources && <FormHelperText>{fieldErrors.sources}</FormHelperText>}</FormControl>
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.severities)}><InputLabel id="severity-filter-label" shrink>Severity filter</InputLabel><Select multiple labelId="severity-filter-label" label="Severity filter" displayEmpty value={severities} onChange={setMulti(setSeverities, "severities")} renderValue={(values) => values.length ? labels(values) : placeholder("Any severity")}>{SEVERITIES.map((value) => <MenuItem key={value} value={value}>{optionLabel(value)}</MenuItem>)}</Select>{fieldErrors.severities && <FormHelperText>{fieldErrors.severities}</FormHelperText>}</FormControl>
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.statuses)}><InputLabel id="status-filter-label" shrink>Status</InputLabel><Select multiple labelId="status-filter-label" label="Status" displayEmpty value={statuses} onChange={setMulti(setStatuses, "statuses")} renderValue={(values) => values.length ? labels(values) : placeholder("Any status")}>{["escalating", "for_review", "acknowledged", "resolved"].map((value) => <MenuItem key={value} value={value}>{optionLabel(value)}</MenuItem>)}</Select>{fieldErrors.statuses && <FormHelperText>{fieldErrors.statuses}</FormHelperText>}</FormControl>
          <TagFilter label="Issue categories" example="Timeout" value={issueCategories} onChange={(value) => { setIssueCategories(value); clearFieldError("issueCategories"); }} error={fieldErrors.issueCategories} />
          <TagFilter label="Issue groups" example="Tool Failures" value={issueGroups} onChange={(value) => { setIssueGroups(value); clearFieldError("issueGroups"); }} error={fieldErrors.issueGroups} />

          <SectionTitle step="3" title="Then" subtitle="Send a notification to one channel in a connected Slack workspace." />
          {connectionsQuery.isLoading ? null : connectionsQuery.isError ? (
            <FormControl fullWidth size="small" error>
              <InputLabel id="workspace-label" shrink>Slack workspace</InputLabel>
              <Select labelId="workspace-label" label="Slack workspace" displayEmpty value="" disabled renderValue={() => placeholder("Select a Slack workspace")} />
              <FormHelperText>Could not load Slack connections. <Button size="small" onClick={() => connectionsQuery.refetch?.()}>Retry</Button></FormHelperText>
            </FormControl>
          ) : slackConnections.length === 0 ? (
            <MuiAlert severity="info" variant="outlined" sx={{ "& .MuiAlert-message": { width: "100%", minWidth: 0 } }}>
              <Stack spacing={1} alignItems="flex-start">
                <Typography typography="s2">Connect Slack to choose a destination. Your draft will be saved while you connect.</Typography>
                <Button color="primary" size="small" variant="outlined" onClick={connectSlack}>Connect Slack</Button>
                {fieldErrors.connection && <FormHelperText error>{fieldErrors.connection}</FormHelperText>}
              </Stack>
            </MuiAlert>
          ) : (
            <>
              <FormControl fullWidth size="small" error={Boolean(fieldErrors.connection)}><InputLabel id="workspace-label" shrink>Slack workspace</InputLabel><Select labelId="workspace-label" label="Slack workspace" displayEmpty value={connectionId} onChange={(e) => { setConnectionId(e.target.value); setChannelId(""); setChannelSearch(""); clearFieldError("connection"); clearFieldError("channel"); }} renderValue={(value) => { const connection = slackConnections.find((item) => String(item.id) === String(value)); return value ? connection?.display_name || connection?.name || "Previously selected workspace" : placeholder("Select a Slack workspace"); }}>{slackConnections.map((connection) => <MenuItem key={connection.id} value={connection.id}>{connection.display_name || connection.name || "Slack workspace"}</MenuItem>)}</Select>{fieldErrors.connection && <FormHelperText>{fieldErrors.connection}</FormHelperText>}</FormControl>
              <Autocomplete
                size="small"
                fullWidth
                autoHighlight
                openOnFocus
                disabled={!connectionId || channelsQuery.isLoading}
                options={channels}
                value={selectedChannel}
                onChange={(_, channel) => { setChannelId(channel?.id || ""); clearFieldError("channel"); }}
                onInputChange={(_, value, reason) => {
                  if (reason === "input") setChannelSearch(value);
                  if (reason === "clear") setChannelSearch("");
                }}
                onClose={() => setChannelSearch("")}
                getOptionLabel={(channel) => `#${channel.name}`}
                isOptionEqualToValue={(option, value) => String(option.id) === String(value.id)}
                loading={channelsQuery.isLoading || channelsQuery.isFetchingNextPage}
                loadingText="Loading channels…"
                noOptionsText={channelsQuery.hasNextPage ? "Searching more channels…" : "No channels found"}
                ListboxProps={{ sx: { maxHeight: 320, overflowY: "auto" }, onScroll: loadChannelsOnScroll }}
                renderOption={(props, channel) => <li {...props} key={channel.id}>#{channel.name}{channel.is_private ? " · private" : ""}</li>}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Channel"
                    error={Boolean(fieldErrors.channel || channelsQuery.isError)}
                    helperText={fieldErrors.channel || (channelsQuery.isError ? <>Could not load channels. <Button size="small" onClick={() => channelsQuery.refetch?.()}>Retry</Button></> : undefined)}
                    placeholder={connectionId ? (channelsQuery.isLoading ? "Loading channels…" : "Search channels") : "Select a Slack workspace first"}
                    InputLabelProps={{ shrink: true }}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: <>{(channelsQuery.isLoading || channelsQuery.isFetchingNextPage) && <CircularProgress size={16} />}{params.InputProps.endAdornment}</>,
                    }}
                  />
                )}
              />
              {!channelsQuery.isLoading && !channelsQuery.isError && connectionId && channels.length === 0 && <FormHelperText>No channels are available to this Slack app. Invite it to a channel, then reload.</FormHelperText>}
            </>
          )}
          <FormControl fullWidth size="small" error={Boolean(fieldErrors.cooldown)}><InputLabel id="cooldown-label">Notify at most</InputLabel><Select labelId="cooldown-label" label="Notify at most" value={cooldown} onChange={(e) => { setCooldown(e.target.value); clearFieldError("cooldown"); }}>{COOLDOWNS.map((seconds) => <MenuItem key={seconds} value={seconds}>{cooldownLabel(seconds)}</MenuItem>)}</Select>{fieldErrors.cooldown && <FormHelperText>{fieldErrors.cooldown}</FormHelperText>}</FormControl>
          <Card variant="outlined" sx={{ p: 1.5, bgcolor: "background.neutral", overflowWrap: "anywhere" }}>
            <Typography typography="s2" color="text.secondary">Preview</Typography>
            <Typography typography="s1" color="text.primary" mt={0.5}>When {TRIGGERS.find((item) => item.value === trigger)?.label.toLowerCase()}{trigger === "severity_reached" ? `: ${triggerValue}` : trigger === "occurrences_crossed" ? `: ${triggerValue}` : ""}{projectId ? ` in ${projects.find((item) => String(item.value || item.id) === String(projectId))?.label || "selected project"}` : " in any project"}, post to #{channels.find((item) => String(item.id) === String(channelId))?.name || "selected channel"}.</Typography>
          </Card>
        </Stack>
      </Box>
      <Box sx={{ p: { xs: 1.5, sm: 2 }, borderTop: 1, borderColor: "divider", flexShrink: 0, bgcolor: "background.paper" }}>
        {fieldErrors.form && <FormHelperText error sx={{ mb: 1 }}>{fieldErrors.form}</FormHelperText>}
        <Stack direction="row" justifyContent="flex-end" flexWrap="wrap" gap={1}><Button size="small" onClick={onClose}>Cancel</Button><LoadingButton size="small" variant="contained" onClick={submit} loading={save.isPending} disabled={optionQuery.isLoading || connectionsQuery.isLoading || channelsQuery.isLoading}>{edit ? "Save changes" : "Create alert"}</LoadingButton></Stack>
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
      renderTags={(selected, getTagProps) => selected.map((item, index) => <Chip {...getTagProps({ index })} key={item} label={item} size="small" sx={{ maxWidth: "calc(100% - 8px)", bgcolor: "action.selected", color: "text.primary", "& .MuiChip-deleteIcon": { color: "text.secondary" } }} />)}
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
  return <Box pt={0.75}><Stack direction="row" spacing={1} alignItems="center"><Chip label={step} size="small" color="primary" /><Typography typography="s1" fontWeight="fontWeightSemiBold">{title}</Typography></Stack><Typography typography="s2" color="text.secondary" mt={0.5}>{subtitle}</Typography></Box>;
}

export default function ErrorFeedAlerts() {
  const navigate = useNavigate();
  const [returningDraft] = useState(readReturningDraft);
  const alertsQuery = useErrorFeedAlerts();
  const { data: rawRules = [], isLoading, isError } = alertsQuery;
  const rules = useMemo(() => rawRules.map((row) => ({ ...row, ...(row.configuration || {}) })), [rawRules]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  useEffect(() => {
    setPage((current) => Math.min(current, Math.max(0, Math.ceil(rules.length / pageSize) - 1)));
  }, [rules.length, pageSize]);
  const visibleRules = rules.slice(page * pageSize, (page + 1) * pageSize);
  const [editing, setEditing] = useState(null);
  const [createOpen, setCreateOpen] = useState(Boolean(returningDraft));
  const save = useSaveErrorFeedAlert();
  const remove = useDeleteErrorFeedAlert();
  const test = useTestErrorFeedAlert();

  return (
    <Box sx={{ display: "flex", flexDirection: "column", flex: 1, height: "100%", minHeight: 0, overflow: "hidden", bgcolor: "background.paper" }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1.5} sx={{ px: 2, pt: 2, pb: 1.5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Box sx={{ minWidth: 0 }}><Button size="small" startIcon={<Iconify icon="eva:arrow-ios-back-fill" />} onClick={() => navigate("/dashboard/error-feed")}>Error Feed</Button><Typography typography="m2" fontWeight="fontWeightSemiBold">Alert rules</Typography><Typography typography="s2" color="text.secondary">Notify a Slack channel when an issue matches your rule.</Typography></Box>
        <Button size="small" variant="contained" startIcon={<Iconify icon="octicon:plus-24" width={20} />} onClick={() => setCreateOpen(true)}>Create alert</Button>
      </Stack>
      <Box sx={{ p: 2, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      {isLoading && <Box display="flex" justifyContent="center" py={8}><CircularProgress /></Box>}
      {isError && <MuiAlert severity="error">Could not load Error Feed alert rules.</MuiAlert>}
      {!isLoading && !isError && rules.length === 0 && <Card variant="outlined" sx={{ p: 5, textAlign: "center" }}><SvgColor src="/assets/icons/navbar/ic_alert.svg" sx={{ width: 42, height: 42, color: "text.secondary", mb: 1 }} /><Typography typography="m3" fontWeight="fontWeightSemiBold">No Error Feed alerts yet</Typography><Typography typography="s2" color="text.secondary" mb={2}>Create a rule to send matching issue notifications to Slack.</Typography><Button variant="contained" onClick={() => setCreateOpen(true)}>Create alert</Button></Card>}
      {!isLoading && !isError && rules.length > 0 && (
        <Box sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
        <TableContainer sx={{ flex: 1, overflowX: "auto", overflowY: "auto", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <Table stickyHeader size="small" sx={{ minWidth: 980 }}>
            <TableHead>
              <TableRow sx={{ "& .MuiTableCell-head": { bgcolor: "background.neutral", color: "text.secondary", fontWeight: "fontWeightMedium", whiteSpace: "nowrap", py: 1.5 } }}>
                <TableCell>Rule name</TableCell>
                <TableCell>When</TableCell>
                <TableCell>Project</TableCell>
                <TableCell>Slack channel</TableCell>
                <TableCell>Last sent</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visibleRules.map((rule) => (
                <TableRow key={rule.id} hover sx={{ "& .MuiTableCell-body": { borderColor: "divider", py: 1.25 } }}>
                  <TableCell sx={{ minWidth: 180 }}>
                    <Typography typography="s1" fontWeight="fontWeightMedium">{rule.name || "Untitled alert"}</Typography>
                    {(rule.health === "failed" || rule.error || rule.failure_reason) && <Typography typography="s3" color="warning.main" sx={{ display: "block", mt: 0.5 }}>{rule.failure_reason || rule.error || "Slack connection needs attention."}</Typography>}
                  </TableCell>
                  <TableCell sx={{ minWidth: 190 }}><Typography typography="s2">{summaryFor(rule)}</Typography></TableCell>
                  <TableCell><Typography typography="s2">{rule.project_scope || (rule.project_id ? "Selected project" : "All projects")}</Typography></TableCell>
                  <TableCell><Typography typography="s2">#{rule.slack_channel_name || rule.configuration?.slack_channel_name || "Slack channel"}</Typography></TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}><Typography typography="s2">{rule.last_triggered_at ? new Date(rule.last_triggered_at).toLocaleString() : "Never"}</Typography></TableCell>
                  <TableCell>
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Switch size="small" checked={rule.enabled !== false} inputProps={{ "aria-label": `Toggle ${rule.name || "alert"}` }} onChange={(e) => save.mutate({ id: rule.id, enabled: e.target.checked })} />
                      <Typography typography="s2">{rule.enabled === false ? "Paused" : "Active"}</Typography>
                    </Stack>
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    <Stack direction="row" alignItems="center" justifyContent="flex-end" gap={0.5}>
                      <LoadingButton size="small" variant="outlined" loading={test.isPending && String(test.variables) === String(rule.id)} onClick={() => test.mutate(rule.id)} startIcon={<Iconify icon="solar:play-circle-linear" width={16} />}>Test</LoadingButton>
                      <IconButton size="small" aria-label={`Edit ${rule.name || "alert"}`} onClick={() => setEditing(rule)}><SvgColor src="/assets/icons/ic_edit.svg" sx={{ width: 18, height: 18 }} /></IconButton>
                      <IconButton size="small" aria-label={`Delete ${rule.name || "alert"}`} onClick={() => { if (window.confirm(`Delete “${rule.name || "this alert"}”?`)) remove.mutate(rule.id); }}><SvgColor src="/assets/icons/ic_delete.svg" sx={{ width: 18, height: 18 }} /></IconButton>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
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
            <Typography typography="s2" color="text.primary" fontWeight="fontWeightRegular">
              Results per page
            </Typography>
            <Select
              size="small"
              aria-label="Results per page"
              value={pageSize}
              onChange={(event) => { setPageSize(Number(event.target.value)); setPage(0); }}
              sx={{ height: 36, bgcolor: "background.paper" }}
            >
              {[10, 25, 50].map((size) => <MenuItem key={size} value={size}>{size}</MenuItem>)}
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
                  previous: () => <Box display="flex" alignItems="center" gap={0.5}><Iconify icon="octicon:chevron-left-24" width={18} height={18} sx={{ path: { strokeWidth: 1.5 } }} />Back</Box>,
                  next: () => <Box display="flex" alignItems="center" gap={0.5}>Next<Iconify icon="octicon:chevron-right-24" width={18} height={18} sx={{ path: { strokeWidth: 1.5 } }} /></Box>,
                }}
              />
            )}
          />
        </Stack>
        </Box>
      )}
      </Box>
      <Drawer anchor="right" open={createOpen || Boolean(editing)} onClose={() => { setCreateOpen(false); setEditing(null); }} PaperProps={{ sx: { width: 560, maxWidth: "100vw", overflow: "hidden" } }}>
        {(createOpen || editing) && <AlertRuleForm rule={editing} draft={editing ? null : returningDraft} onClose={() => { setCreateOpen(false); setEditing(null); }} />}
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
    slack_connection_id: PropTypes.string,
    slack_channel_id: PropTypes.string,
    slack_channel_name: PropTypes.string,
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
    issueCategories: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.string]),
    issueGroups: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.string]),
    cooldown: PropTypes.number,
  }),
  onClose: PropTypes.func.isRequired,
};
