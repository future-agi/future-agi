import PropTypes from "prop-types";
import { useState } from "react";
import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SLACK_NEEDS_APPROVAL,
  SLACK_PERMISSIONS,
  SLACK_WORKSPACES,
  getIntegrations,
} from "../../_mock/integrations";
import BrandMark from "../BrandMark";
import StandInWindow from "../ExternalPage";
import { replyToOpener } from "../connectWindow";

/**
 * Stand-in for Slack's consent page, opened in its own window by "Add to
 * Slack". The Slack workspace is chosen here (Slack knows which ones you're
 * signed in to). Allow → back to Future AGI with a code; Cancel → back with
 * access_denied; a workspace that vets apps → a request to its admins.
 *
 * ?workspace=<name> comes from "Finish adding" after an admin approved.
 */
export default function SlackAuthorizeWindow() {
  const [params] = useSearchParams();
  const preset = params.get("workspace");
  const [workspace, setWorkspace] = useState(preset || SLACK_WORKSPACES[0]);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(null);

  const req = getIntegrations().slackRequest;
  const approved = req?.status === "approved" && req.workspace === workspace;
  const needsApproval = SLACK_NEEDS_APPROVAL.includes(workspace) && !approved;

  const finish = (outcome) => {
    if (!replyToOpener({ provider: "slack", outcome, workspace }))
      setSent(outcome);
  };

  if (sent) {
    return (
      <StandInWindow site="Slack" url="slack.com">
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {sent === "cancel" ? "Cancelled" : "Done"}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          You can close this window and go back to Future AGI.
        </Typography>
      </StandInWindow>
    );
  }

  return (
    <StandInWindow
      site="Slack"
      url="slack.com/oauth/v2/authorize?client_id=future-agi"
      footer={
        <>
          <Button
            size="small"
            onClick={() => finish("cancel")}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          {needsApproval ? (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => finish("request")}
            >
              Submit request
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => finish("allow")}
            >
              Allow
            </Button>
          )}
        </>
      }
    >
      <Helmet>
        <title>Add Future AGI · Slack (prototype stand-in)</title>
      </Helmet>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2.5 }}>
        <BrandMark brand="slack" size={32} />
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          Future AGI is requesting permission to access a Slack workspace
        </Typography>
      </Stack>

      <Label>Slack workspace</Label>
      <Select
        size="small"
        fullWidth
        value={workspace}
        onChange={(e) => setWorkspace(e.target.value)}
        disabled={!!preset}
        sx={{ typography: "s2", mb: 0.5 }}
      >
        {SLACK_WORKSPACES.map((w) => (
          <MenuItem key={w} value={w} sx={{ typography: "s2" }}>
            {w}
          </MenuItem>
        ))}
      </Select>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 2.5 }}>
        The workspaces you&apos;re signed in to on Slack.
      </Typography>

      <Label>Future AGI will be able to</Label>
      <Stack spacing={0.75}>
        {SLACK_PERMISSIONS.map((p) => (
          <Stack key={p} direction="row" spacing={1} alignItems="flex-start">
            <Iconify
              icon="solar:check-linear"
              width={14}
              sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {p}
            </Typography>
          </Stack>
        ))}
      </Stack>

      {needsApproval && (
        <Box
          sx={{
            mt: 2.5,
            p: 1.5,
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Stack
            direction="row"
            spacing={1}
            alignItems="flex-start"
            sx={{ mb: 1 }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={16}
              sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`${workspace} requires an admin to approve new apps. Send a request — you'll hear back in Slack.`}
            </Typography>
          </Stack>
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why your team needs it (optional)"
            InputProps={{ sx: { typography: "s2" } }}
          />
        </Box>
      )}
    </StandInWindow>
  );
}

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
