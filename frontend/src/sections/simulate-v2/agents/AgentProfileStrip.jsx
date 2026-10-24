import PropTypes from "prop-types";
import { useState } from "react";
import { Alert, AlertTitle, Button, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  agentStatusOf,
  needsLiveConsent,
  retellPinStateOf,
} from "../_mock/rsiModel";
import { replicationOf } from "../_mock/replication";
import { QuietTag } from "./AgentBits";
import { definitionOf, normalizeRepoUrl } from "../_mock/agentRegistry";
import {
  installationFor,
  installedRepos,
  repoNameOf,
  useIntegrations,
} from "../_mock/integrations";
import GithubInstallDialog from "../integrations/GithubInstallDialog";

const btnSx = { flexShrink: 0 };

/**
 * The fix for a repository the GitHub App lost: reinstall it, here — on
 * GitHub, in its own window — rather than a trip to Settings. When the
 * account is still installed it opens that installation's repository access;
 * once the repository is back in it, the agent reconnects on its own.
 */
export function ReinstallGithubButton({
  agent,
  onReconnected,
  variant = "outlined",
}) {
  useIntegrations(); // re-read when the install comes back
  const [open, setOpen] = useState(false);
  const repo = repoNameOf(normalizeRepoUrl(definitionOf(agent).repoUrl || ""));
  const account = repo.split("/")[0] || null;
  const installed = account ? installationFor(account) : null;
  return (
    <>
      <Button
        size="small"
        variant={variant}
        color={variant === "contained" ? "primary" : "inherit"}
        onClick={() => setOpen(true)}
        sx={btnSx}
      >
        {installed
          ? `Give access to ${repo.split("/")[1] || "the repository"}`
          : "Reinstall GitHub App"}
      </Button>
      <GithubInstallDialog
        open={open}
        configure={installed ? account : undefined}
        onClose={() => setOpen(false)}
        onInstalled={() => {
          if (
            installedRepos().some(
              (r) => r.name.toLowerCase() === repo.toLowerCase(),
            )
          )
            onReconnected?.();
        }}
      />
    </>
  );
}
ReinstallGithubButton.propTypes = {
  agent: PropTypes.object.isRequired,
  onReconnected: PropTypes.func,
  variant: PropTypes.string,
};

/**
 * Disconnected or archived — said at the top of the agent page, with the
 * reason and the one thing that fixes it: a lost GitHub App is reinstalled
 * from right here; a revoked key or a deleted platform agent is fixed on
 * Connections, where the key and the agent it points at are set.
 */
export function AgentStatusBanner({
  agent,
  onReconnect,
  onRestore,
  onReconnected,
}) {
  useIntegrations(); // a reinstall clears it
  const s = agentStatusOf(agent);
  if (s.status === "connected") return null;
  const red = s.status === "disconnected";
  return (
    <Alert
      severity={red ? "error" : "info"}
      icon={
        <Iconify
          icon={
            red
              ? "solar:link-broken-linear"
              : s.status === "deleted"
                ? "solar:trash-bin-minimalistic-linear"
                : "solar:archive-linear"
          }
          width={16}
        />
      }
      sx={{ mb: 2 }}
      action={
        red && s.cause === "app" ? (
          <ReinstallGithubButton
            agent={agent}
            variant="contained"
            onReconnected={onReconnected}
          />
        ) : s.status !== "deleted" ? (
          /* A deleted agent has nothing to restore — only its history. */
          <Button
            size="small"
            variant="outlined"
            onClick={red ? onReconnect : onRestore}
            sx={btnSx}
          >
            {!red
              ? "Restore"
              : s.cause === "deleted"
                ? "Choose another agent"
                : "Replace the key"}
          </Button>
        ) : undefined
      }
    >
      <AlertTitle>{s.label}</AlertTitle>
      {red
        ? `${s.reason} Runs and improvements are stopped until it's reconnected.`
        : s.reason}
    </Alert>
  );
}
AgentStatusBanner.propTypes = {
  agent: PropTypes.object.isRequired,
  onReconnect: PropTypes.func,
  onRestore: PropTypes.func,
  onReconnected: PropTypes.func,
};

/**
 * "Needs consent" — a live agent can't run until someone says we may create
 * test copies in their account and call it. Retell also needs its live
 * numbers pinned before drafts. Both are given on Connections.
 */
export function NeedsConsentNotice({ agent, env, onOpen }) {
  const r = replicationOf(agent, env);
  /* An endpoint has its own acknowledgment, shown above. */
  const consent = needsLiveConsent(agent) && !r.needsConsent;
  const pins = retellPinStateOf(agent);
  const pin = !!pins && !pins.pinned;
  if (!consent && !pin) return null;
  return (
    <Alert
      severity="warning"
      icon={<Iconify icon="solar:shield-warning-linear" width={16} />}
      sx={{ mb: 2 }}
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onOpen}
          sx={{ flexShrink: 0 }}
        >
          {consent ? "Review" : "Pin numbers"}
        </Button>
      }
    >
      <AlertTitle>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          component="span"
        >
          <span>{consent ? "Needs consent" : "Pin the live numbers"}</span>
          {consent && pin && <QuietTag>Live numbers to pin</QuietTag>}
        </Stack>
      </AlertTitle>
      <span
        title={
          consent
            ? "Tests call your real agent, so anything it does — like issuing a refund — really happens."
            : undefined
        }
      >
        {consent
          ? "Approve test calls once before the first run."
          : `${pins.line}. Until then tests run with settings overridden for one call.`}
      </span>
    </Alert>
  );
}
NeedsConsentNotice.propTypes = {
  agent: PropTypes.object.isRequired,
  env: PropTypes.object,
  onOpen: PropTypes.func,
};
