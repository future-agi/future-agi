import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { agentSharingLabel } from "./agentSharing";

export default function AgentSharingFields({
  sharing,
  onChange,
  directory,
  disabled,
  canManage,
  supported,
}) {
  if (!supported)
    return (
      <Typography variant="body2" color="text.secondary">
        Sharing controls are unavailable in this deployment.
      </Typography>
    );
  if (!canManage)
    return (
      <Box>
        <Typography variant="subtitle2">Agent access</Typography>
        <Typography variant="body2">{agentSharingLabel(sharing)}</Typography>
        <Typography variant="caption" color="text.secondary">
          Only the agent owner with permission to edit can change access.
        </Typography>
      </Box>
    );
  const accountIds = sharing?.accountIds || [];
  const toggle = (id, checked) =>
    onChange({
      sharing: {
        mode: "selected",
        accountIds: checked
          ? [...accountIds, id]
          : accountIds.filter((entry) => entry !== id),
      },
    });
  const candidates = [
    ...new Map(
      [
        ...(directory.knownCandidates || []).filter((entry) =>
          accountIds.includes(entry.accountId),
        ),
        ...directory.candidates,
      ].map((entry) => [entry.accountId, entry]),
    ).values(),
  ];
  const missing = accountIds.filter(
    (id) => !candidates.some((candidate) => candidate.accountId === id),
  );
  return (
    <Stack spacing={1} component="section" aria-label="Agent sharing">
      <TextField
        select
        fullWidth
        label="Agent access"
        SelectProps={{ native: true }}
        value={sharing?.mode || "private"}
        disabled={disabled}
        onChange={(event) =>
          onChange({ sharing: { mode: event.target.value, accountIds: [] } })
        }
        helperText="Sharing lets people use the agent. Each person keeps their own conversations, memory and run history."
      >
        <option value="private">Private — only me</option>
        <option value="selected">Selected people</option>
        <option value="workspace">Everyone in this workspace</option>
      </TextField>
      <Typography variant="caption" color="text.secondary">
        Sharing does not grant access to the owner’s connected accounts. Access
        changes take effect when saved; future work is checked again by the
        server.
      </Typography>
      {sharing?.mode === "selected" ? (
        <>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <TextField
              size="small"
              fullWidth
              label="Find workspace members"
              value={directory.search || ""}
              disabled={disabled}
              inputProps={{ maxLength: 200 }}
              onChange={(event) => directory.setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  directory.searchMembers();
                }
              }}
            />
            <Button
              type="button"
              onClick={directory.searchMembers}
              disabled={disabled || directory.loading}
            >
              Search members
            </Button>
          </Stack>
          {directory.loading ? (
            <Typography variant="body2">Reading workspace members…</Typography>
          ) : null}
          {directory.error ? (
            <Alert
              severity="warning"
              action={
                <Button onClick={directory.reload} disabled={disabled}>
                  Retry
                </Button>
              }
            >
              {directory.error}
            </Alert>
          ) : null}
          {directory.truncated ? (
            <Alert severity="info">
              Showing the first 200 matching members. Search by name or account
              ID to narrow the list.
            </Alert>
          ) : null}
          {directory.available && !directory.candidates.length ? (
            <Typography variant="body2">
              No other active workspace members match this search.
            </Typography>
          ) : null}
          <Box>
            {candidates.map((candidate) => (
              <FormControlLabel
                key={candidate.accountId}
                label={
                  candidates.some(
                    (other) =>
                      other.accountId !== candidate.accountId &&
                      other.displayName === candidate.displayName,
                  )
                    ? `${candidate.displayName} · ${candidate.accountId}`
                    : candidate.displayName
                }
                control={
                  <Checkbox
                    checked={accountIds.includes(candidate.accountId)}
                    disabled={
                      disabled ||
                      directory.loading ||
                      (!accountIds.includes(candidate.accountId) &&
                        accountIds.length >= 64)
                    }
                    onChange={(_, checked) =>
                      toggle(candidate.accountId, checked)
                    }
                  />
                }
              />
            ))}
          </Box>
          {missing.length ? (
            <Alert severity="warning">
              Some saved recipients are not in the current member list. Search
              for them or remove them before saving a new selection.
              <Stack>
                {missing.map((id) => (
                  <Button
                    key={id}
                    disabled={disabled}
                    onClick={() => toggle(id, false)}
                  >
                    Remove unavailable recipient {id}
                  </Button>
                ))}
              </Stack>
            </Alert>
          ) : null}
          {accountIds.length === 0 ? (
            <Typography variant="caption" color="error">
              Choose at least one person, or select Private.
            </Typography>
          ) : null}
        </>
      ) : null}
    </Stack>
  );
}
AgentSharingFields.propTypes = {
  sharing: PropTypes.shape({
    mode: PropTypes.string,
    accountIds: PropTypes.arrayOf(PropTypes.string),
  }),
  onChange: PropTypes.func.isRequired,
  directory: PropTypes.shape({
    candidates: PropTypes.arrayOf(PropTypes.object),
    knownCandidates: PropTypes.arrayOf(PropTypes.object),
    available: PropTypes.bool,
    truncated: PropTypes.bool,
    loading: PropTypes.bool,
    error: PropTypes.string,
    reload: PropTypes.func,
    search: PropTypes.string,
    setSearch: PropTypes.func,
    searchMembers: PropTypes.func,
  }).isRequired,
  disabled: PropTypes.bool,
  canManage: PropTypes.bool,
  supported: PropTypes.bool,
};
