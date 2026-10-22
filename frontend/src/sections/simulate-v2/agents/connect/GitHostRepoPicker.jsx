import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, CircularProgress, MenuItem, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { GIT_HOST_META, connectGitHost, disconnectGitHost, gitHostConnection, gitHostRepos, reviewNounOf, useIntegrations } from "../../_mock/integrations";
import { keyRefOf } from "../../_mock/secretRefs";
import SecretRefField from "./SecretRefField";
import FoundAgentsList from "./FoundAgentsList";
import { TextLink } from "./connectBits";
import { HintLine } from "./replicationBits";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * GitLab or Bitbucket, the same way as GitHub: connect the host once for the
 * workspace, pick a repository, and say which agent the scan found you mean.
 * The git host is a setting on the agent — a connection, not a new kind of
 * agent — so everything after the pick is the GitHub flow as it is.
 */
export default function GitHostRepoPicker({ host, repo, agentId, onPick, onManual }) {
  const integrations = useIntegrations();
  const meta = GIT_HOST_META[host];
  const connection = gitHostConnection(host, integrations);
  const repos = gitHostRepos(host, integrations);
  const current = repos.find((r) => r.name === repo) || null;
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    if (!repo) return undefined;
    setScanning(true);
    const t = setTimeout(() => setScanning(false), 800);
    return () => clearTimeout(t);
  }, [repo]);

  if (!connection) {
    return (
      <Box>
        <GitHostConnectForm host={host} />
        {onManual && <TextLink onClick={() => onManual()} sx={{ mt: 0.75 }}>{`Enter a ${meta.label} repository URL instead`}</TextLink>}
      </Box>
    );
  }

  return (
    <Stack spacing={1.25}>
      <Box>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>Repository</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`· from ${meta.label} (${connection.account}) · ${connection.access === "write" ? "read and write" : "read only"}`}
          </Typography>
          <Box flex={1} />
          <TextLink onClick={() => { disconnectGitHost(host); onPick({ repo: "", agent: null }); }}>{`Connect another ${meta.account.toLowerCase()}`}</TextLink>
        </Stack>
        <Select
          size="small" fullWidth displayEmpty
          value={repo || ""}
          onChange={(e) => onPick({ repo: e.target.value, agent: null })}
          renderValue={(v) => (v
            ? <Box component="span" sx={{ fontFamily: MONO }}>{v}</Box>
            : <Box component="span" sx={{ color: "text.disabled" }}>Choose a repository</Box>)}
          sx={{ typography: "s2" }}
        >
          {repos.map((r) => (
            <MenuItem key={r.name} value={r.name} sx={{ typography: "s2", gap: 1 }}>
              <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>{r.name}</Box>
              <Box component="span" sx={{ color: "text.subtitle" }}>{r.language}</Box>
            </MenuItem>
          ))}
        </Select>
      </Box>

      {current && (scanning ? (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1.25, borderRadius: 1, bgcolor: "background.neutral" }}>
          <CircularProgress size={13} thickness={5} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>{`Scanning ${current.name} for agents…`}</Typography>
        </Stack>
      ) : current.agents.length === 0 ? (
        <Box sx={{ px: 1.5, py: 1.25, borderRadius: 1, bgcolor: "background.neutral" }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>We didn&apos;t find an agent in this repository.</Typography>
          {onManual && <TextLink onClick={() => onManual({ repo: current.name })} sx={{ mt: 0.5 }}>Point us at the entry file instead</TextLink>}
        </Box>
      ) : (
        <FoundAgentsList agents={current.agents} agentId={agentId} onPick={(a) => onPick({ repo: current.name, agent: a })} />
      ))}
    </Stack>
  );
}

GitHostRepoPicker.propTypes = {
  host: PropTypes.oneOf(["gitlab", "bitbucket"]).isRequired,
  repo: PropTypes.string,
  agentId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onManual: PropTypes.func,
};

/*
  Connecting the host, once for the workspace: an access token by
  reference. A group (GitLab) or workspace (Bitbucket) token belongs to one
  group or workspace, so that's read off the token when it connects — it
  can't be listed before there's a token. Read scopes only — write is asked
  for at the first draft merge or pull request.
*/
export function GitHostConnectForm({ host, onConnected }) {
  const meta = GIT_HOST_META[host];
  const review = reviewNounOf(host);
  const [tokenRef, setTokenRef] = useState("");
  const [connecting, setConnecting] = useState(false);
  /* What's recorded is a reference — the one typed, or (demo) the host's slot. */
  const savedRef = keyRefOf(tokenRef, meta.tokenSlot);

  useEffect(() => {
    if (!connecting) return undefined;
    const t = setTimeout(() => {
      /* Demo: the token's group or workspace, as the host would report it. */
      connectGitHost(host, { account: meta.accounts[0], tokenRef: savedRef });
      setConnecting(false);
      onConnected?.();
    }, 700);
    return () => clearTimeout(t);
  }, [connecting]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.25 }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify icon={meta.icon} width={20} sx={{ color: "text.primary", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{`Connect ${meta.label} to pick the agent`}</Typography>
          <Typography component="div" sx={{ typography: "s3", color: "text.subtitle" }}>
            <HintLine hint={`We scan the ${meta.account.toLowerCase()}'s repositories and find the agents in them. Write access is asked for at your first draft ${review.long}.`}>
              {`Once for the workspace · read only`}
            </HintLine>
          </Typography>
        </Box>
      </Stack>
      <Stack spacing={1.5} sx={{ px: 1.75, py: 1.5 }}>
        <SecretRefField
          label={meta.token} required fullWidth
          placeholder={meta.tokenSlot}
          value={tokenRef} onChange={setTokenRef}
          helper={`A token belongs to one ${meta.account.toLowerCase()} — we read which when you connect, then list its repositories. Stored encrypted, never shown again, never sent to the improver.`}
        />
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          {meta.readScopes.map((p, i) => (
            <Stack key={p.scope} direction="row" spacing={1.5} sx={{ px: 1.5, py: 0.875, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
              <Typography sx={{ typography: "s3", fontFamily: MONO, width: 128, flexShrink: 0 }}>{p.scope}</Typography>
              <Typography sx={{ typography: "s3", width: 40, flexShrink: 0 }}>{p.access}</Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}>{p.why}</Typography>
            </Stack>
          ))}
        </Box>
        <Stack direction="row" justifyContent="flex-end">
          <Button
            size="small" variant="contained" color="primary"
            disabled={!savedRef || connecting}
            onClick={() => setConnecting(true)}
            startIcon={connecting ? <CircularProgress size={12} thickness={6} sx={{ color: "inherit" }} /> : <Iconify icon={meta.icon} width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            {connecting ? `Connecting to ${meta.label}…` : `Connect ${meta.label}`}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

GitHostConnectForm.propTypes = {
  host: PropTypes.oneOf(["gitlab", "bitbucket"]).isRequired,
  onConnected: PropTypes.func,
};
