import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { agentStatusOf, needsLiveConsent, retellPinStateOf } from "../_mock/rsiModel";
import { replicationOf } from "../_mock/replication";
import { QuietTag } from "./AgentBits";
import { definitionOf, normalizeRepoUrl } from "../_mock/agentRegistry";
import { installationFor, installedRepos, repoNameOf, useIntegrations } from "../_mock/integrations";
import GithubInstallDialog from "../integrations/GithubInstallDialog";

const btnSx = { typography: "s3", fontWeight: 700, height: 28, color: "text.primary", borderColor: "divider", flexShrink: 0 };

/**
 * The fix for a repository the GitHub App lost: reinstall it, here — on
 * GitHub, in its own window — rather than a trip to Settings. When the
 * account is still installed it opens that installation's repository access;
 * once the repository is back in it, the agent reconnects on its own.
 */
export function ReinstallGithubButton({ agent, onReconnected, variant = "outlined" }) {
  useIntegrations(); // re-read when the install comes back
  const [open, setOpen] = useState(false);
  const repo = repoNameOf(normalizeRepoUrl(definitionOf(agent).repoUrl || ""));
  const account = repo.split("/")[0] || null;
  const installed = account ? installationFor(account) : null;
  return (
    <>
      <Button
        size="small" variant={variant} color={variant === "contained" ? "primary" : "inherit"} onClick={() => setOpen(true)}
        sx={variant === "contained" ? { typography: "s3", fontWeight: 700, height: 28, flexShrink: 0 } : btnSx}
      >
        {installed ? `Give access to ${repo.split("/")[1] || "the repository"}` : "Reinstall GitHub App"}
      </Button>
      <GithubInstallDialog
        open={open}
        configure={installed ? account : undefined}
        onClose={() => setOpen(false)}
        onInstalled={() => {
          if (installedRepos().some((r) => r.name.toLowerCase() === repo.toLowerCase())) onReconnected?.();
        }}
      />
    </>
  );
}
ReinstallGithubButton.propTypes = { agent: PropTypes.object.isRequired, onReconnected: PropTypes.func, variant: PropTypes.string };


/**
 * Disconnected or archived — said at the top of the agent page, with the
 * reason and the one thing that fixes it: a lost GitHub App is reinstalled
 * from right here; a revoked key or a deleted platform agent is fixed on
 * Connections, where the key and the agent it points at are set.
 */
export function AgentStatusBanner({ agent, onReconnect, onRestore, onReconnected }) {
  useIntegrations(); // a reinstall clears it
  const s = agentStatusOf(agent);
  if (s.status === "connected") return null;
  const red = s.status === "disconnected";
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        mb: 2, px: 1.75, py: 1.125, borderRadius: 1.25, border: "1px solid",
        borderColor: red ? "rgba(220,38,38,0.4)" : "divider",
        bgcolor: (t) => (red ? alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05) : "background.neutral"),
      }}
    >
      <Iconify icon={red ? "solar:link-broken-linear" : s.status === "deleted" ? "solar:trash-bin-minimalistic-linear" : "solar:archive-linear"} width={16} sx={{ color: red ? "#DC2626" : "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700, color: red ? "#DC2626" : "text.primary" }}>{s.label}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {red ? `${s.reason} Runs and improvements are stopped until it's reconnected.` : s.reason}
        </Typography>
      </Box>
      {/* A deleted agent has nothing to restore — only its history. */}
      {red && s.cause === "app" ? (
        <ReinstallGithubButton agent={agent} variant="contained" onReconnected={onReconnected} />
      ) : s.status !== "deleted" && (
        <Button size="small" variant="outlined" onClick={red ? onReconnect : onRestore} sx={btnSx}>
          {!red ? "Restore" : s.cause === "deleted" ? "Choose another agent" : "Replace the key"}
        </Button>
      )}
    </Stack>
  );
}
AgentStatusBanner.propTypes = { agent: PropTypes.object.isRequired, onReconnect: PropTypes.func, onRestore: PropTypes.func, onReconnected: PropTypes.func };

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
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{ mb: 2, px: 1.75, py: 1.125, borderRadius: 1.25, border: "1px solid", borderColor: (t) => alpha(t.palette.text.primary, 0.24), bgcolor: "background.paper" }}
    >
      <Iconify icon="solar:shield-warning-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{consent ? "Needs consent" : "Pin the live numbers"}</Typography>
          {consent && pin && <QuietTag>Live numbers to pin</QuietTag>}
        </Stack>
        <Typography
          title={consent ? "Tests call your real agent, so anything it does — like issuing a refund — really happens." : undefined}
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          {consent
            ? "Approve test calls once before the first run."
            : `${pins.line}. Until then tests run with settings overridden for one call.`}
        </Typography>
      </Box>
      <Button
        size="small" variant="contained" color="primary" onClick={onOpen}
        sx={{ typography: "s3", fontWeight: 700, height: 28, flexShrink: 0 }}
      >
        {consent ? "Review" : "Pin numbers"}
      </Button>
    </Stack>
  );
}
NeedsConsentNotice.propTypes = { agent: PropTypes.object.isRequired, env: PropTypes.object, onOpen: PropTypes.func };
