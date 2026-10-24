import PropTypes from "prop-types";
import { useDemoMode } from "../agents/demoMode";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Menu,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  SLACK_CHANNELS,
  SLACK_EVENTS,
  approveSlackRequest,
  cancelSlackRequest,
  uninstallSlack,
  updateSlack,
  useIntegrations,
} from "../_mock/integrations";
import BrandMark from "./BrandMark";
import SlackInstallDialog from "./SlackInstallDialog";
import SlackMessage from "./SlackMessage";
import { StatusPill } from "./GithubIntegrationPage";
import { DemoLabel } from "../agents/AgentBits";

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
      "Failed on agent v4 in Customer Support Line — the agent issued a $240 refund without escalating",
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
      "a3f91c2 Hand chargebacks to a human — priya.s",
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
  const demo = useDemoMode();
  const navigate = useNavigate();
  const { slack, slackLog, slackRequest, slackRemoved } = useIntegrations();
  const [installOpen, setInstallOpen] = useState(false);
  const [menu, setMenu] = useState(null);
  const [removing, setRemoving] = useState(false);
  const [preview, setPreview] = useState("regression");

  const setRule = (id, channel) =>
    updateSlack({ rules: { ...slack.rules, [id]: channel } });
  const sample = {
    ...SAMPLES[preview],
    at: new Date().toISOString(),
    channel:
      slack?.rules?.[preview] || slack?.defaultChannel || "#agent-quality",
  };

  return (
    <Box>
      <Button
        size="small"
        variant="outlined"
        startIcon={
          <Iconify icon="solar:arrow-left-linear" width={16} height={16} />
        }
        onClick={() => navigate(paths.dashboard.settings.integrations)}
        sx={{ mb: 2 }}
      >
        Back to Integrations
      </Button>

      <Section variant="panel" sx={{ mb: 3, p: 3 }}>
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
                ? `Added to ${slack.workspace} · default channel ${slack.defaultChannel}`
                : "Tell the team when quality moves, and send alerts to a channel."}
            </Typography>
            <Stack direction="row" spacing={0.75} sx={{ mt: 1 }}>
              {["Notifications", "Alert destination"].map((t) => (
                <Chip key={t} label={t} size="small" variant="outlined" />
              ))}
            </Stack>
          </Box>
          {slack ? (
            <>
              <StatusPill on />
              <Button
                size="small"
                aria-label="More actions"
                onClick={(e) => setMenu(e.currentTarget)}
                sx={{ minWidth: 0, color: "text.secondary" }}
              >
                <Iconify icon="solar:menu-dots-bold" />
              </Button>
              <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}>
                {demo && (
                  <MenuItem
                    onClick={() => {
                      setMenu(null);
                      uninstallSlack({ inSlack: true });
                    }}
                    sx={{ typography: "s2" }}
                  >
                    <DemoLabel />
                    Simulate: removed in Slack
                  </MenuItem>
                )}
                <MenuItem
                  onClick={() => {
                    setMenu(null);
                    setRemoving(true);
                  }}
                  // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
                  sx={{ color: "status.danger.fg", typography: "s2" }}
                >
                  Remove from Slack
                </MenuItem>
              </Menu>
              <Dialog
                open={removing}
                onClose={() => setRemoving(false)}
                maxWidth={false}
                PaperProps={{ sx: { maxWidth: 560 } }}
                fullWidth
              >
                <DialogTitle
                  sx={{ typography: "m2", fontWeight: 600 }}
                >{`Remove from ${slack.workspace}?`}</DialogTitle>
                <DialogContent>
                  <DialogContentText sx={{ typography: "s2" }}>
                    Run, regression, must-pass, fix and alert messages stop
                    posting. Adding it again starts from one default channel.
                  </DialogContentText>
                </DialogContent>
                <DialogActions sx={{ px: 3, pb: 2 }}>
                  <Button
                    onClick={() => setRemoving(false)}
                    sx={{ color: "text.secondary" }}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="contained"
                    onClick={() => {
                      uninstallSlack();
                      setRemoving(false);
                    }}
                    color="error"
                  >
                    Remove
                  </Button>
                </DialogActions>
              </Dialog>
            </>
          ) : (
            <Stack alignItems="flex-end" spacing={0.5}>
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => setInstallOpen(true)}
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
      </Section>

      {/* Removed from Slack's side (app_uninstalled / tokens_revoked). */}
      {!slack && slackRemoved && !slackRequest && (
        <Notice
          icon="solar:link-broken-linear"
          title={`Future AGI was removed from ${slackRemoved.workspace} in Slack`}
        >
          Notifications and alerts stopped. Add it again to{" "}
          {slackRemoved.workspace} to pick up where you left off — its events
          and channels come back.
        </Notice>
      )}

      {/* The workspace's admins approve apps before anyone can add them. */}
      {!slack && slackRequest?.status === "pending" && (
        <Notice
          icon="solar:clock-circle-linear"
          title={`Waiting for an admin of ${slackRequest.workspace} to approve`}
          action={
            <>
              <Button
                size="small"
                onClick={cancelSlackRequest}
                sx={{ color: "text.secondary" }}
              >
                Withdraw
              </Button>
              {demo && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={approveSlackRequest}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  <DemoLabel />
                  Admin approved
                </Button>
              )}
            </>
          }
        >
          Slack sent your request to the workspace admins and will message you
          when one decides.
        </Notice>
      )}
      {!slack && slackRequest?.status === "approved" && (
        <Notice
          icon="solar:check-circle-bold"
          iconColor="status.success.fg"
          title={`An admin of ${slackRequest.workspace} approved Future AGI`}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => setInstallOpen(slackRequest.workspace)}
            >
              Finish adding
            </Button>
          }
        >
          Add it to the workspace now — it takes one more click on Slack.
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
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600, mb: 2 }}>
              What connecting Slack unlocks
            </Typography>
            <Stack spacing={2}>
              {[
                {
                  icon: "solar:bell-linear",
                  title: "Know when quality moves",
                  body: "A run finishes, a version regresses, a must-pass scenario fails — the people who care hear about it in their channel.",
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
                      width: 28,
                      height: 28,
                      borderRadius: "6px",
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
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
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
          </Section>
          <Section variant="panel" sx={{ p: 2 }}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.subtitle",
                textTransform: "none",
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
          </Section>
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
            <Section variant="panel">
              <Box sx={{ px: 3, pt: 2.5, pb: 1.5 }}>
                <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                  Notifications
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  Choose what gets posted and where. Hover a row to preview it.
                </Typography>
              </Box>
              {SLACK_EVENTS.map((e) => {
                const ch =
                  slack.rules && e.id in slack.rules
                    ? slack.rules[e.id]
                    : e.on
                      ? slack.defaultChannel
                      : null;
                return (
                  <Stack
                    key={e.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    onMouseEnter={() => setPreview(e.id)}
                    sx={{
                      px: 3,
                      py: 1,
                      borderTop: "1px solid",
                      borderColor: "divider",
                      bgcolor:
                        preview === e.id ? "action.hover" : "transparent",
                    }}
                  >
                    <Switch
                      size="small"
                      checked={!!ch}
                      onChange={(ev) =>
                        setRule(
                          e.id,
                          ev.target.checked ? slack.defaultChannel : null,
                        )
                      }
                    />
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2_1", fontWeight: 500 }}>
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
                      disabled={!ch}
                      onChange={(ev) => setRule(e.id, ev.target.value)}
                      displayEmpty
                      renderValue={(v) => v || "Off"}
                      sx={{ minWidth: 160, height: 30, typography: "s2" }}
                    >
                      {SLACK_CHANNELS.map((c) => (
                        <MenuItem key={c} value={c} sx={{ typography: "s2" }}>
                          {c}
                        </MenuItem>
                      ))}
                    </Select>
                  </Stack>
                );
              })}
            </Section>
          </Stack>

          <Stack
            spacing={3}
            sx={{ position: { lg: "sticky" }, top: { lg: 16 } }}
          >
            <Section variant="panel" sx={{ p: 2 }}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.subtitle",
                  textTransform: "none",
                  letterSpacing: 0.5,
                  mb: 1,
                }}
              >
                Preview
              </Typography>
              <SlackMessage msg={sample} />
            </Section>
            <Section variant="panel">
              <Box sx={{ px: 2, pt: 2, pb: 1 }}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  Recent messages
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  What Future AGI posted. Each button opens what the message is
                  about.
                </Typography>
              </Box>
              {slackLog.length === 0 ? (
                <Typography
                  sx={{
                    px: 2,
                    pb: 2,
                    typography: "s2",
                    color: "text.subtitle",
                  }}
                >
                  Nothing posted yet — finish a simulation run or open a fix.
                </Typography>
              ) : (
                <Stack
                  spacing={1}
                  sx={{ px: 2, pb: 2, maxHeight: 520, overflowY: "auto" }}
                >
                  {slackLog
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
            </Section>
          </Stack>
        </Box>
      )}

      <SlackInstallDialog
        open={!!installOpen}
        onClose={() => setInstallOpen(false)}
        workspace={typeof installOpen === "string" ? installOpen : undefined}
      />
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
    <Section variant="panel" sx={{ mb: 3, px: 3, py: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Iconify
          icon={icon}
          width={20}
          sx={{ color: iconColor, flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {children}
          </Typography>
        </Box>
        {action}
      </Stack>
    </Section>
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
