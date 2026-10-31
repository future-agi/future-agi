import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  IconButton,
  CircularProgress,
  Menu,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { pageBackButtonSx } from "../../styles";
import { paths } from "src/routes/paths";
import {
  useDeleteConnection,
  useIntegrationConnections,
  useSlackChannels,
  useStartSlackInstall,
} from "src/api/integrations";
import { getErrorMessage } from "../../utils";
import {
  SLACK_EVENTS,
  resetSlackRouting,
  setSlackDefaultChannel,
  setSlackRule,
  slackRulesOf,
  useIntegrations,
} from "../store";
import BrandMark from "../BrandMark";
import { StatusPill } from "../StatusPill";
import {
  SLACK_RETURN_TO_KEY,
  isSlackActive,
  openExternal,
  slackConnectionOf,
} from "../slackConnection";
import SlackMessage from "./SlackMessage";

/* What each kind of message looks like when it lands. */
const SAMPLES = {
  run_finished: {
    title: "Customer Support Line · run 4 finished",
    lines: [
      "64 of 68 scenarios passed (94%) · 3 failed · 1 not measured",
      "Agent v4 · environment v1",
    ],
    actions: ["View run"],
  },
  regression: {
    title: "Regression · Returns & refunds agent v4 is down 12 pts",
    lines: [
      "Refund scenarios dropped from 96% to 84% against v3 in Customer Support Line",
      "7 scenarios that passed on v3 now fail",
    ],
    actions: ["Compare runs", "View failures"],
  },
  release_blocker: {
    title:
      "Must-pass scenario failing · Refunds above $200 need supervisor approval",
    lines: [
      "Failed on agent v4 in Customer Support Line: the agent issued a $240 refund without escalating",
    ],
    actions: ["View scenario", "Open run"],
  },
  pr: {
    title: "Fix PR opened · acme-inc/support-voice-agent #142",
    lines: [
      "Escalate refunds above $200 before issuing them",
      "2 files · simulation 84% → 97%",
    ],
    actions: ["View pull request", "Open run"],
  },
  version: {
    title: "Support voice agent · v5 saved from a push",
    lines: [
      "a3f91c2 Hand chargebacks to a human (priya.s)",
      "Its 2 environments test it on the next run if following production; pinned ones stay put.",
    ],
    actions: ["Compare with the previous version", "Review environments"],
  },
  error_feed: {
    title: "New issue · Agent promised a delivery date it couldn't confirm",
    lines: ["23 production calls in the last hour · Customer Support Line"],
    actions: ["Open issue"],
  },
  alert: {
    title: "Alert · p95 latency above 2s",
    lines: ["Customer Support Line · last 15 minutes"],
    actions: ["View alert"],
  },
};

/** Settings → Integrations → Slack. */
export default function SlackIntegrationPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const store = useIntegrations();
  const { slackLog } = store;
  /* The connection is real (dev's Slack OAuth); routing and messages are kept here until the backend stores them. */
  const {
    data: connections,
    isLoading: loadingConnections,
    isError: connectionsFailed,
    refetch,
  } = useIntegrationConnections();
  const connection = slackConnectionOf(connections);
  const on = isSlackActive(connection);
  const { mutate: startInstall, isPending: starting } = useStartSlackInstall();
  const { mutate: deleteConnection, isPending: deleting } =
    useDeleteConnection();
  const {
    data: channelData,
    isLoading: loadingChannels,
    isError: channelsFailed,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useSlackChannels(on ? connection.id : null);
  /* Large workspaces page their channel list; load it all so every channel can be picked. */
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) fetchNextPage?.();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);
  /* Only the workspace's real channels; never sample names for a real workspace. */
  const channels = (channelData?.channels || []).map((c) => `#${c.name}`);
  const channelNote = loadingChannels
    ? "Loading channels…"
    : channelsFailed
      ? "Couldn't load channels"
      : channels.length
        ? ""
        : "No channels";
  const defaultChannel = channels.includes(store.slackDefaultChannel)
    ? store.slackDefaultChannel
    : channels[0] || null;
  const rules = slackRulesOf(store, defaultChannel);
  const slack = on
    ? { workspace: connection.display_name || "Slack", defaultChannel, rules }
    : null;
  /* Sample messages, shown in the channel each event goes to in this workspace. */
  const messages = (slackLog || []).map((m) => ({
    ...m,
    channel: rules[m.event] || defaultChannel || "",
  }));
  const returned = new URLSearchParams(location.search).get("slack");
  const [installError, setInstallError] = useState(
    returned === "error" ? "Could not connect Slack. Please try again." : "",
  );
  /* Keep the default in the store so the Integrations card can show it too. */
  useEffect(() => {
    if (on && defaultChannel && store.slackDefaultChannel !== defaultChannel)
      setSlackDefaultChannel(defaultChannel);
  }, [on, defaultChannel, store.slackDefaultChannel]);
  const [menu, setMenu] = useState(null);
  const [removing, setRemoving] = useState(false);
  const [preview, setPreview] = useState("regression");
  const workspaceMatch = location.pathname.match(
    /^\/dashboard\/settings\/workspace\/([^/]+)\/integrations\//,
  );
  const integrationsHome = workspaceMatch
    ? paths.dashboard.settings.workspaceIntegrations(workspaceMatch[1])
    : paths.dashboard.settings.integrations;

  const setRule = (id, channel) => setSlackRule(id, channel);
  const sample = {
    ...SAMPLES[preview],
    at: new Date().toISOString(),
    channel: rules[preview] || defaultChannel || (on ? "" : "#agent-quality"),
  };

  /* Real Slack OAuth: the backend returns Slack's authorize URL, and Slack sends the person back to
     Integrations, which forwards them here (SLACK_RETURN_TO_KEY). */
  const installSlack = (connectionId) => {
    setInstallError("");
    try {
      window.sessionStorage.setItem(SLACK_RETURN_TO_KEY, location.pathname);
    } catch {
      /* storage unavailable */
    }
    startInstall(connectionId, {
      onSuccess: (response) => {
        const url = response?.data?.result?.authorization_url;
        if (!url) {
          setInstallError(
            "Could not start Slack authorization. Please try again.",
          );
          return;
        }
        openExternal(url);
      },
      onError: (error) =>
        setInstallError(getErrorMessage(error, "Could not connect Slack.")),
    });
  };
  // A first install, or a broken connection fixed in place.
  const addToSlack = () =>
    installSlack(connection && !on ? connection.id : undefined);
  // A working connection renewed in place, as dev's manage dialog offered.
  const reconnect = () => installSlack(connection.id);
  const remove = () =>
    deleteConnection(connection.id, {
      onSuccess: () => resetSlackRouting(),
      onSettled: () => setRemoving(false),
    });

  if (loadingConnections) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        sx={{ minHeight: 320 }}
      >
        <CircularProgress size={28} />
      </Stack>
    );
  }
  if (connectionsFailed) {
    return (
      <Box sx={{ py: 4 }}>
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => refetch()}>
              Retry
            </Button>
          }
        >
          Couldn&apos;t load your Slack connection.
        </Alert>
      </Box>
    );
  }

  return (
    <Box>
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
        onClick={() => navigate(integrationsHome)}
        sx={{ mb: 2, ...pageBackButtonSx }}
      >
        Back to Integrations
      </Button>

      <Card sx={{ mb: 3, p: 3 }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <BrandMark brand="slack" size={48} />
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}
            >
              Slack
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {slack
                ? `Added to ${slack.workspace}${slack.defaultChannel ? ` · default channel ${slack.defaultChannel}` : ""}`
                : "Tell the team when quality moves, and send alerts to a channel."}
            </Typography>
            <Stack direction="row" spacing={0.75} sx={{ mt: 1 }}>
              {["Notifications", "Alert destination"].map((t) => (
                <Chip
                  key={t}
                  label={t}
                  size="small"
                  variant="outlined"
                  sx={{ height: 22, typography: "s3" }}
                />
              ))}
            </Stack>
          </Box>
          {slack ? (
            <>
              <StatusPill on />
              <IconButton
                size="small"
                aria-label="More actions"
                onClick={(e) => setMenu(e.currentTarget)}
                sx={{ color: "text.secondary" }}
              >
                <Iconify icon="solar:menu-dots-bold" />
              </IconButton>
              <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}>
                <MenuItem
                  disabled={starting}
                  onClick={() => {
                    setMenu(null);
                    reconnect();
                  }}
                  sx={{ typography: "s2" }}
                >
                  Reconnect Slack
                </MenuItem>
                <MenuItem
                  onClick={() => {
                    setMenu(null);
                    setRemoving(true);
                  }}
                  sx={{ color: "error.main", typography: "s2" }}
                >
                  Disconnect
                </MenuItem>
              </Menu>
              <ConfirmDialog
                open={removing}
                onClose={() => setRemoving(false)}
                title={`Disconnect ${slack.workspace}?`}
                content={
                  <Typography
                    sx={{ typography: "s1", color: "text.secondary" }}
                  >
                    Error Feed alerts that post to this workspace stop, and
                    Future AGI stops posting to it. The app stays in Slack until
                    a Slack admin removes it. Adding it again starts from one
                    default channel.
                  </Typography>
                }
                action={
                  <Button
                    size="small"
                    variant="contained"
                    color="error"
                    disabled={deleting}
                    onClick={remove}
                  >
                    Disconnect
                  </Button>
                }
              />
            </>
          ) : (
            <Stack alignItems="flex-end" spacing={0.5}>
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={addToSlack}
                disabled={starting}
                startIcon={<Iconify icon="logos:slack-icon" width={14} />}
              >
                Add to Slack
              </Button>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Opens Slack · you pick the workspace there
              </Typography>
            </Stack>
          )}
        </Stack>
      </Card>

      {installError && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {installError}
        </Alert>
      )}
      {slack && returned === "connected" && (
        <Alert severity="success" sx={{ mb: 3 }}>
          Slack connected.
        </Alert>
      )}

      {/* A Slack connection that isn't active: revoked, removed in Slack, or failed. */}
      {!slack && connection && (
        <Notice
          icon="solar:link-broken-linear"
          title={`Future AGI needs adding to ${connection.display_name || "Slack"} again`}
        >
          {connection.status_message ||
            "Notifications and alerts stopped. Add it again to pick up where you left off."}
        </Notice>
      )}

      {!slack && (
        <Box
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: { xs: "1fr", lg: "1fr 420px" },
            alignItems: "start",
          }}
        >
          <Card sx={{ p: 3 }}>
            <Typography sx={{ typography: "s1", fontWeight: 700, mb: 2 }}>
              What connecting Slack unlocks
            </Typography>
            <Stack spacing={2}>
              {[
                {
                  icon: "solar:bell-linear",
                  title: "Know when quality moves",
                  body: "A run finishes, a version regresses, a must-pass scenario fails: the people who care hear about it in their channel.",
                },
                {
                  icon: "solar:code-linear",
                  title: "Follow every fix",
                  body: "A fix PR opening or merging, and a new agent version, land where the team already talks. Reviews stay on GitHub.",
                },
                {
                  icon: "solar:siren-linear",
                  title: "Alerts to a channel, not a webhook URL",
                  body: "Pick a channel for any alert rule instead of pasting a webhook.",
                },
              ].map((c) => (
                <Stack key={c.title} direction="row" spacing={1.5}>
                  <Box
                    sx={{
                      width: 32,
                      height: 32,
                      borderRadius: 1,
                      flexShrink: 0,
                      display: "grid",
                      placeItems: "center",
                      bgcolor: (t) => alpha(t.palette.text.primary, 0.05),
                      color: "text.secondary",
                    }}
                  >
                    <Iconify icon={c.icon} width={16} />
                  </Box>
                  <Box>
                    <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                      {c.title}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        mt: 0.25,
                      }}
                    >
                      {c.body}
                    </Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Card>
          <Card sx={{ p: 2 }}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 700,
                color: "text.subtitle",
                textTransform: "uppercase",
                letterSpacing: 0.5,
                mb: 1,
              }}
            >
              What your team sees
            </Typography>
            <SlackMessage
              msg={{
                ...SAMPLES.regression,
                at: new Date().toISOString(),
                channel: "#agent-quality",
              }}
            />
          </Card>
        </Box>
      )}

      {slack && (
        <Box
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: { xs: "1fr", lg: "1fr 420px" },
            alignItems: "start",
          }}
        >
          <Stack spacing={3}>
            <Card>
              <Box sx={{ px: 3, pt: 2.5, pb: 1.5 }}>
                <Typography sx={{ typography: "s1", fontWeight: 700 }}>
                  Notifications
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  Choose what gets posted and where. Hover a row to preview it.
                </Typography>
              </Box>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  px: 3,
                  py: 1.25,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Default channel
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    Where an event goes unless you pick another channel for it.
                  </Typography>
                </Box>
                <Select
                  size="small"
                  value={defaultChannel || ""}
                  onChange={(ev) => setSlackDefaultChannel(ev.target.value)}
                  disabled={!channels.length}
                  displayEmpty
                  renderValue={(v) => v || channelNote}
                  inputProps={{ "aria-label": "Default channel" }}
                  sx={{ minWidth: 160, height: 30, typography: "s2" }}
                >
                  {channels.map((c) => (
                    <MenuItem key={c} value={c} sx={{ typography: "s2" }}>
                      {c}
                    </MenuItem>
                  ))}
                </Select>
              </Stack>
              {SLACK_EVENTS.map((e) => {
                const ch = rules[e.id];
                return (
                  <Stack
                    key={e.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    onMouseEnter={() => setPreview(e.id)}
                    sx={{
                      px: 3,
                      py: 1.25,
                      borderTop: "1px solid",
                      borderColor: "divider",
                      bgcolor:
                        preview === e.id ? "action.hover" : "transparent",
                    }}
                  >
                    <Switch
                      size="small"
                      checked={!!ch}
                      disabled={!channels.length}
                      onChange={(ev) =>
                        setRule(
                          e.id,
                          ev.target.checked ? slack.defaultChannel : null,
                        )
                      }
                    />
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {e.label}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.subtitle" }}
                      >
                        {e.blurb}
                      </Typography>
                    </Box>
                    <Select
                      size="small"
                      value={ch || ""}
                      disabled={!ch || !channels.length}
                      onChange={(ev) => setRule(e.id, ev.target.value)}
                      displayEmpty
                      renderValue={(v) => v || "Off"}
                      sx={{ minWidth: 160, height: 30, typography: "s2" }}
                    >
                      {channels.map((c) => (
                        <MenuItem key={c} value={c} sx={{ typography: "s2" }}>
                          {c}
                        </MenuItem>
                      ))}
                    </Select>
                  </Stack>
                );
              })}
            </Card>
          </Stack>

          <Stack
            spacing={3}
            sx={{ position: { lg: "sticky" }, top: { lg: 16 } }}
          >
            <Card sx={{ p: 2 }}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 700,
                  color: "text.subtitle",
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  mb: 1,
                }}
              >
                Preview
              </Typography>
              <SlackMessage msg={sample} />
            </Card>
            <Card>
              <Box sx={{ px: 2, pt: 2, pb: 1 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  Recent messages
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  What Future AGI posted. Each button opens what the message is
                  about.
                </Typography>
              </Box>
              {messages.length === 0 ? (
                <Typography
                  sx={{
                    px: 2,
                    pb: 2,
                    typography: "s2",
                    color: "text.subtitle",
                  }}
                >
                  Nothing posted yet. Finish a simulation run or open a fix.
                </Typography>
              ) : (
                <Stack
                  spacing={1}
                  sx={{ px: 2, pb: 2, maxHeight: 520, overflowY: "auto" }}
                >
                  {messages
                    /* Messages from when fixes could wait for a Slack OK — no longer a thing. */
                    .filter((m) => m.event !== "approval")
                    .map((m) => (
                      <SlackMessage
                        key={m.id}
                        msg={m}
                        onAction={
                          m.links && Object.keys(m.links).length
                            ? (label) => {
                                /* Buttons lead back to what the message is about. */
                                if (m.links[label]) navigate(m.links[label]);
                              }
                            : undefined
                        }
                      />
                    ))}
                </Stack>
              )}
            </Card>
          </Stack>
        </Box>
      )}
    </Box>
  );
}

function Notice({
  icon,
  iconColor = "text.secondary",
  title,
  action,
  children,
}) {
  return (
    <Card sx={{ mb: 3, px: 3, py: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Iconify
          icon={icon}
          width={18}
          sx={{ color: iconColor, flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {children}
          </Typography>
        </Box>
        {action}
      </Stack>
    </Card>
  );
}
Notice.propTypes = {
  icon: PropTypes.string,
  iconColor: PropTypes.string,
  title: PropTypes.string,
  action: PropTypes.node,
  children: PropTypes.node,
};

SlackIntegrationPage.propTypes = {};
