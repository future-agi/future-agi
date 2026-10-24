import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SLACK_CHANNELS,
  installSlack,
  requestSlack,
} from "../_mock/integrations";
import BrandMark from "./BrandMark";
import { Returning, WaitingForWindow } from "./ExternalPage";
import { CONNECT_PATHS, useConnectWindow } from "./connectWindow";

/**
 * Future AGI's side of "Add to Slack". Slack's consent page opens in its own
 * window (see windows/SlackAuthorizeWindow) where the Slack workspace is
 * picked; this waits for it, then:
 *   allow    → connected; now the channel list exists and they pick one
 *   request  → the workspace's admins approve apps first; nothing yet
 *   cancel   → Slack sent back access_denied; nothing changed
 *   closed   → the window was closed; nothing changed
 *
 * `workspace` re-opens Slack for a workspace whose admin has since approved.
 */
export default function SlackInstallDialog({
  open,
  onClose,
  onInstalled,
  workspace: preset,
}) {
  const [phase, setPhase] = useState("waiting"); // waiting → returning → channel | requested | cancelled
  const [workspace, setWorkspace] = useState(null);
  const [reason, setReason] = useState(null);
  const [channel, setChannel] = useState(SLACK_CHANNELS[0]);

  const win = useConnectWindow((msg) => {
    if (msg.outcome === "allow") {
      setWorkspace(msg.workspace);
      setPhase("returning");
      return;
    }
    if (msg.outcome === "request") {
      requestSlack(msg.workspace);
      setWorkspace(msg.workspace);
      setPhase("requested");
      return;
    }
    setReason(msg.outcome);
    setPhase("cancelled");
  });
  const url = `${CONNECT_PATHS.slack}${preset ? `?workspace=${encodeURIComponent(preset)}` : ""}`;

  useEffect(() => {
    if (!open) return;
    setPhase("waiting");
    setWorkspace(null);
    setReason(null);
    setChannel(SLACK_CHANNELS[0]);
    win.open(url);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Slack redirected back with a code; the backend trades it for a bot
     token. From here the connection exists — closing now keeps it. */
  useEffect(() => {
    if (phase !== "returning") return undefined;
    const t = setTimeout(() => {
      installSlack({ workspace, defaultChannel: SLACK_CHANNELS[0] });
      setPhase("channel");
    }, 900);
    return () => clearTimeout(t);
  }, [phase, workspace]);

  const finish = () => {
    installSlack({ workspace, defaultChannel: channel });
    onInstalled?.();
    onClose();
  };
  const close = () => {
    if (phase === "waiting") win.close();
    if (phase === "channel") onInstalled?.();
    onClose();
  };
  const retry = () => {
    setPhase("waiting");
    setReason(null);
    win.open(url);
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 2 }}
      >
        <BrandMark brand="slack" size={36} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            Add to Slack
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            You choose the Slack workspace on Slack.
          </Typography>
        </Box>
        <IconButton size="small" onClick={close}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 3, pb: 2.5 }}>
        {phase === "waiting" && (
          <WaitingForWindow
            site="Slack"
            status={win.status}
            onFocus={win.focus}
            onOpen={
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => win.open(url)}
                startIcon={<Iconify icon="logos:slack-icon" width={13} />}
              >
                Open Slack
              </Button>
            }
          >
            Pick the Slack workspace there and allow Future AGI. This updates as
            soon as you&apos;re done.
          </WaitingForWindow>
        )}

        {phase === "returning" && (
          <Returning label={`Adding Future AGI to ${workspace}…`} />
        )}

        {phase === "channel" && (
          <Stack spacing={2}>
            <Result
              icon="solar:check-circle-bold"
              color="status.success.fg"
              title={`Added to ${workspace}`}
              meta="by you"
            />
            <Box>
              <Label>Default channel</Label>
              <Select
                size="small"
                fullWidth
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                sx={{ typography: "s2" }}
              >
                {SLACK_CHANNELS.map((c) => (
                  <MenuItem key={c} value={c} sx={{ typography: "s2" }}>
                    {c}
                  </MenuItem>
                ))}
              </Select>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
              >
                {`Public channels in ${workspace}. You can send each kind of message to its own channel afterwards.`}
              </Typography>
            </Box>
            <Stack
              direction="row"
              spacing={1}
              alignItems="flex-start"
              sx={{
                px: 1.5,
                py: 1.25,
                borderRadius: 1,
                bgcolor: "background.neutral",
              }}
            >
              <Iconify
                icon="solar:lock-keyhole-minimalistic-linear"
                width={16}
                sx={{ color: "text.subtitle", mt: 0.25, flexShrink: 0 }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {"Private channels show up once you invite the app in Slack: "}
                <Box
                  component="span"
                  sx={{
                    fontFamily: "ui-monospace, Menlo, monospace",
                    color: "text.primary",
                  }}
                >
                  /invite @Future AGI
                </Box>
              </Typography>
            </Stack>
          </Stack>
        )}

        {phase === "requested" && (
          <Stack spacing={1.5}>
            <Result
              icon="solar:clock-circle-linear"
              color="status.warning.fg"
              title={`Requested from ${workspace}'s admins`}
              meta="Nothing connected yet"
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Slack sent your request to the workspace admins. When one
              approves, Slack messages you — then finish adding the app from the
              Slack settings page.
            </Typography>
          </Stack>
        )}

        {phase === "cancelled" && (
          <Stack spacing={1.5}>
            <Result
              icon="solar:close-circle-linear"
              color="text.secondary"
              title="Nothing changed"
              meta={
                reason === "closed"
                  ? "Slack window closed"
                  : "Cancelled on Slack"
              }
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Future AGI wasn&apos;t added to any Slack workspace.
            </Typography>
          </Stack>
        )}
      </Box>

      {phase !== "returning" && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {phase === "waiting" && (
            <Button
              size="small"
              onClick={close}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
          )}
          {phase === "channel" && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={finish}
            >
              Done
            </Button>
          )}
          {phase === "requested" && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={close}
            >
              Done
            </Button>
          )}
          {phase === "cancelled" && (
            <>
              <Button
                size="small"
                onClick={close}
                sx={{ color: "text.secondary" }}
              >
                Close
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={retry}
              >
                Try again
              </Button>
            </>
          )}
        </Stack>
      )}
    </Dialog>
  );
}

SlackInstallDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onInstalled: PropTypes.func,
  workspace: PropTypes.string,
};

function Result({ icon, color, title, meta }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify icon={icon} width={16} sx={{ color, flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
        {title}
      </Typography>
      {meta && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {meta}
        </Typography>
      )}
    </Stack>
  );
}
Result.propTypes = {
  icon: PropTypes.string,
  color: PropTypes.string,
  title: PropTypes.string,
  meta: PropTypes.string,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
