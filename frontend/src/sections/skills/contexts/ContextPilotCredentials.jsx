import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableContainer,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import StatusBadge from "src/components/status-badge";
import Iconify from "src/components/iconify";
import IdentityChip from "../components/IdentityChip";
import When from "../components/When";
import { ConfirmDialog } from "src/components/custom-dialog";
import { ROW_HEIGHT } from "src/theme/sizes";
import {
  useContextCredentials,
  useIssueContextCredential,
  useRevokeContextCredential,
} from "src/api/skills";

const EXPIRY_DAYS = [1, 7, 30];
const DAY_MS = 86_400_000;

// A pilot credential is purpose-bound: consume only, this context, this exact
// version. No labels, so a later release cannot be delivered through it.
export function pilotCredentialInput({
  contextId,
  versionId,
  days,
  now = Date.now(),
}) {
  if (!contextId || !versionId || !EXPIRY_DAYS.includes(days))
    throw new Error("Choose an exact version and an expiry.");
  return {
    purpose: "consume",
    grants: [
      {
        kind: "agent_context",
        resourceId: contextId,
        labels: [],
        versionIds: [versionId],
      },
    ],
    bindingIds: [],
    expiresAt: new Date(now + days * DAY_MS).toISOString(),
  };
}

export const grantsContext = (credential, contextId) =>
  credential.purpose === "consume" &&
  credential.grants.some(
    (grant) =>
      grant.kind === "agent_context" && grant.resourceId === contextId,
  );

const credentialStatus = (credential, now = Date.now()) => {
  if (credential.revokedAt) return "revoked";
  if (Date.parse(credential.expiresAt) <= now) return "expired";
  return "active";
};

const newKey = () => crypto.randomUUID();

function CredentialRow({ credential, versionLabel, onRevoke }) {
  const [anchor, setAnchor] = useState(null);
  const status = credentialStatus(credential);
  return (
    <TableRow hover sx={{ height: ROW_HEIGHT.default }}>
      <TableCell>
        <IdentityChip value={credential.id} kind="id" label="Key" />
      </TableCell>
      <TableCell>
        <Typography variant="s1">{versionLabel(credential)}</Typography>
      </TableCell>
      <TableCell>
        <Typography variant="s1">
          <When value={credential.expiresAt} kind="expiry" />
        </Typography>
      </TableCell>
      <TableCell>
        <StatusBadge status={status} />
      </TableCell>
      <TableCell align="right">
        <IconButton
          size="small"
          aria-label={`Actions for credential ${credential.id.slice(0, 8)}`}
          onClick={(event) => setAnchor(event.currentTarget)}
        >
          <Iconify icon="solar:menu-dots-bold" width={16} />
        </IconButton>
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
        >
          <MenuItem
            disabled={status === "revoked"}
            onClick={() => {
              setAnchor(null);
              onRevoke(credential);
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:forbidden-circle-linear" width={16} />
            </ListItemIcon>
            Revoke
          </MenuItem>
        </Menu>
      </TableCell>
    </TableRow>
  );
}
CredentialRow.propTypes = {
  credential: PropTypes.object.isRequired,
  versionLabel: PropTypes.func.isRequired,
  onRevoke: PropTypes.func.isRequired,
};

// Intent: an admin issues and later revokes the isolated credential a pilot
// install uses, next to the exact version it is bound to.
export default function ContextPilotCredentials({
  contextId,
  snapshot,
  canIssue,
  onAvailabilityChange,
}) {
  const [days, setDays] = useState(7);
  const [issueKey, setIssueKey] = useState(newKey);
  const [copyState, setCopyState] = useState("");
  const [revoking, setRevoking] = useState(null);
  const list = useContextCredentials(
    { page: 1, pageSize: 100 },
    { enabled: canIssue },
  );
  const issue = useIssueContextCredential();
  const revoke = useRevokeContextCredential();
  const issued = issue.data;
  const rows = (list.data?.results ?? []).filter((row) =>
    grantsContext(row, contextId) && row.grants.some((grant) => grant.kind === "agent_context" && grant.resourceId === contextId && grant.versionIds?.includes(snapshot.versionId)),
  );
  const hasKey = !list.isError && rows.some((row) => credentialStatus(row) === 'active');
  useEffect(() => { onAvailabilityChange?.(hasKey); }, [hasKey, onAvailabilityChange]);
  const versionLabel = (credential) => {
    const ids = credential.grants.flatMap((grant) => grant.versionIds);
    return ids.length === 1 && ids[0] === snapshot.versionId
      ? `v${snapshot.version}`
      : `${ids.length} exact versions`;
  };
  if (!canIssue)
    return (
      <Typography variant="s3" color="text.secondary">Ask a workspace admin for access.</Typography>
    );
  return (
    <>
      <Stack spacing={1.5}>
        <Typography variant="m3">Create a read-only key for v{snapshot.version}</Typography>
        <Typography variant="s3">
          Read-only, bound to v{snapshot.version} of this context. It cannot
          read drafts, other versions or moving labels. The secret is shown
          once and is never placed in a command.
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
          <TextField
            select
            size="small"
            label="Expires after"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            sx={{ minWidth: 160 }}
          >
            {EXPIRY_DAYS.map((value) => (
              <MenuItem key={value} value={value}>
                {value === 1 ? "1 day" : `${value} days`}
              </MenuItem>
            ))}
          </TextField>
          <Button
            color="primary"
            variant={hasKey ? "outlined" : "contained"}
            disabled={issue.isPending}
            onClick={() => {
              setCopyState("");
              issue.mutate(
                {
                  ...pilotCredentialInput({
                    contextId,
                    versionId: snapshot.versionId,
                    days,
                  }),
                  idempotencyKey: issueKey,
                },
                { onSuccess: () => setIssueKey(newKey()) },
              );
            }}
          >
            Create credential
          </Button>
        </Stack>
        {issue.isError && (
          <Alert severity="error">
            Could not issue the credential. Retrying reuses the same request.
          </Alert>
        )}
        {issued && (
          <Alert severity={issued.secretAvailable ? "success" : "warning"}>
            <Stack spacing={1}>
              <Typography variant="s1">
                Read-only key created for v{snapshot.version}.
                {issued.secretAvailable
                  ? " Copy it now into FI_CONTEXT_CREDENTIAL; it is not shown again."
                  : " This was a replay; the secret is no longer available. Issue a new credential."}
              </Typography>
              <IdentityChip value={issued.id} kind="id" label="Key" />
              {issued.secretAvailable && (
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={async () => {
                      try {
                        if (!navigator.clipboard?.writeText)
                          throw new Error("Unavailable");
                        await navigator.clipboard.writeText(issued.secret);
                        setCopyState("copied");
                      } catch {
                        setCopyState("failed");
                      }
                    }}
                  >
                    Copy credential secret
                  </Button>
                  {copyState === "copied" && (
                    <Typography variant="s3" role="status">
                      Copied. Store it in your secret manager.
                    </Typography>
                  )}
                  {copyState === "failed" && (
                    <Typography variant="s3" role="status">
                      Clipboard unavailable. Revoke this credential and issue
                      another from a browser with clipboard access.
                    </Typography>
                  )}
                </Stack>
              )}
            </Stack>
          </Alert>
        )}
        {list.isError ? (
          <Alert severity="error">
            Could not load credentials.{" "}
            <Button size="small" onClick={() => list.refetch()}>
              Retry
            </Button>
          </Alert>
        ) : rows.length > 0 ? (
          <TableContainer><Table size="small" aria-label="Pilot credentials for this context">
            <TableHead>
              <TableRow>
                <TableCell>Key</TableCell>
                <TableCell>Version</TableCell>
                <TableCell>Expires</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <CredentialRow
                  key={row.id}
                  credential={row}
                  versionLabel={versionLabel}
                  onRevoke={setRevoking}
                />
              ))}
            </TableBody>
          </Table></TableContainer>
        ) : (
          !list.isLoading && (
            <Typography variant="s3">
              No read-only key for this version yet.
            </Typography>
          )
        )}
        {revoke.isError && (
          <Alert severity="error">
            Revocation was not confirmed. Check the status and try again.
          </Alert>
        )}
      </Stack>
      <ConfirmDialog
        open={Boolean(revoking)}
        onClose={() => setRevoking(null)}
        title="Revoke pilot credential"
        content={
          revoking
            ? `Credential ${revoking.id.slice(0, 8)} stops working for every new resolve, download and access check. Installed files stay until you remove them.`
            : ""
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={revoke.isPending}
            onClick={() =>
              revoke.mutate(
                { credentialId: revoking.id },
                { onSuccess: () => setRevoking(null) },
              )
            }
          >
            Revoke
          </Button>
        }
      />
    </>
  );
}
ContextPilotCredentials.propTypes = {
  contextId: PropTypes.string.isRequired,
  snapshot: PropTypes.object.isRequired,
  canIssue: PropTypes.bool,
  onAvailabilityChange: PropTypes.func,
};
