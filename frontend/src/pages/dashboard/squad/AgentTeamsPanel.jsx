import { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  Grid,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

const EMPTY_DRAFT = {
  name: "",
  purpose: "",
  useMinRole: "member",
  editMinRole: "admin",
  agentIds: [],
};

function reason(result) {
  return result?.reason || "The remote agent-team service is unavailable.";
}

function teamDraft(team) {
  if (!team) return EMPTY_DRAFT;
  return {
    name: team.name || "",
    purpose: team.purpose || "",
    useMinRole: team.useMinRole || "member",
    editMinRole: team.editMinRole || "admin",
    agentIds: Array.isArray(team.agentIds) ? team.agentIds : [],
  };
}

function idempotencyKey(prefix, id = "") {
  const suffix =
    globalThis.crypto?.randomUUID?.() ||
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}${id ? `-${id}` : ""}-${suffix}`;
}

export default function AgentTeamsPanel({ client, agents }) {
  const [result, setResult] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [mode, setMode] = useState("");
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const next = await client.listAgentTeams();
    setResult(next);
    if (next.available) {
      const teams = Array.isArray(next.teams) ? next.teams : [];
      setSelectedId((current) =>
        teams.some((team) => team.id === current)
          ? current
          : teams[0]?.id || "",
      );
    } else {
      setSelectedId("");
    }
    setLoading(false);
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const teams = useMemo(
    () =>
      result?.available && Array.isArray(result.teams) ? result.teams : [],
    [result],
  );
  const selected = teams.find((team) => team.id === selectedId) || null;
  const agentsById = useMemo(
    () => new Map((agents || []).map((agent) => [agent.id, agent])),
    [agents],
  );
  const canCreate = result?.available === true && result.canCreate === true;

  const beginCreate = () => {
    setDraft({ ...EMPTY_DRAFT, agentIds: [] });
    setMode("create");
    setMessage("");
    setError("");
  };

  const beginEdit = () => {
    if (!selected) return;
    setDraft(teamDraft(selected));
    setMode("edit");
    setMessage("");
    setError("");
  };

  const toggleAgent = (agentId) => {
    setDraft((current) => ({
      ...current,
      agentIds: current.agentIds.includes(agentId)
        ? current.agentIds.filter((id) => id !== agentId)
        : [...current.agentIds, agentId],
    }));
  };

  const save = async (event) => {
    event.preventDefault();
    if (!draft.name.trim() || !draft.purpose.trim()) return;
    setBusy(mode);
    setError("");
    setMessage("");
    const input = {
      name: draft.name.trim(),
      purpose: draft.purpose.trim(),
      agentIds: draft.agentIds,
      useMinRole: draft.useMinRole,
      editMinRole: draft.editMinRole,
    };
    const response =
      mode === "edit" && selectedId
        ? await client.updateAgentTeam(
            selectedId,
            input,
            idempotencyKey("team-update", selectedId),
          )
        : await client.createAgentTeam(input, idempotencyKey("team-create"));
    setBusy("");
    if (!response.available) {
      setError(reason(response));
      return;
    }
    setMode("");
    setMessage(
      mode === "edit"
        ? "Agent team updated remotely."
        : "Agent team created remotely.",
    );
    const nextId = response.team?.id || selectedId;
    if (nextId) setSelectedId(nextId);
    await load();
  };

  const remove = async () => {
    if (
      !selectedId ||
      (typeof globalThis.confirm === "function" &&
        !globalThis.confirm(
          "Disband this agent team? Agents will remain unchanged.",
        ))
    )
      return;
    setBusy("delete");
    setError("");
    setMessage("");
    const response = await client.deleteAgentTeam(
      selectedId,
      idempotencyKey("team-delete", selectedId),
    );
    setBusy("");
    if (!response.available) {
      setError(reason(response));
      return;
    }
    setMessage("Agent team disbanded remotely; its agents were not changed.");
    setSelectedId("");
    await load();
  };

  return (
    <Card variant="outlined" aria-label="Agent teams">
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="h6">Agent teams</Typography>
            <Typography variant="body2" color="text.secondary">
              Group tenant-managed agents with explicit use and edit role
              floors. Team addressing remains separate from a managed run.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="solar:refresh-linear" />}
              onClick={() => void load()}
              disabled={loading}
            >
              Refresh
            </Button>
            {canCreate ? (
              <Button size="small" variant="contained" onClick={beginCreate}>
                Create team
              </Button>
            ) : null}
          </Stack>
        </Stack>
        {error ? (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        ) : null}
        {message ? (
          <Alert severity="success" sx={{ mt: 2 }}>
            {message}
          </Alert>
        ) : null}
        {loading && !result ? (
          <CircularProgress size={22} sx={{ mt: 2 }} />
        ) : null}
        {result?.available === false ? (
          <Alert severity="info" sx={{ mt: 2 }}>
            {reason(result)}
          </Alert>
        ) : null}
        {result?.available && teams.length === 0 ? (
          <Typography color="text.secondary" sx={{ mt: 2 }}>
            No agent teams are configured for this workspace.
          </Typography>
        ) : null}
        {teams.length > 0 ? (
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} md={4}>
              <Stack spacing={1}>
                {teams.map((team) => (
                  <Card
                    key={team.id}
                    variant="outlined"
                    onClick={() => {
                      setSelectedId(team.id);
                      setMode("");
                    }}
                    sx={{
                      cursor: "pointer",
                      borderColor:
                        selectedId === team.id ? "primary.main" : "divider",
                    }}
                  >
                    <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
                      <Typography fontWeight={600}>{team.name}</Typography>
                      <Typography variant="body2" color="text.secondary" noWrap>
                        {team.purpose}
                      </Typography>
                      <Stack direction="row" spacing={0.5} sx={{ mt: 1 }}>
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`${team.agentIds?.length || 0} agents`}
                        />
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`use: ${team.useMinRole || "member"}`}
                        />
                      </Stack>
                    </CardContent>
                  </Card>
                ))}
              </Stack>
            </Grid>
            <Grid item xs={12} md={8}>
              {selected ? (
                <Card variant="outlined">
                  <CardContent>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      justifyContent="space-between"
                      alignItems={{ sm: "center" }}
                      gap={1}
                    >
                      <Box>
                        <Typography variant="subtitle1" fontWeight={600}>
                          {selected.name}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          {selected.purpose}
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={1}>
                        <Button
                          size="small"
                          onClick={beginEdit}
                          disabled={busy !== ""}
                        >
                          Edit
                        </Button>
                        <Button
                          size="small"
                          color="error"
                          onClick={() => void remove()}
                          disabled={busy !== ""}
                        >
                          {busy === "delete" ? "Disbanding…" : "Disband"}
                        </Button>
                      </Stack>
                    </Stack>
                    <Divider sx={{ my: 1.5 }} />
                    <Typography variant="subtitle2">Members</Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 0.5 }}
                    >
                      {selected.agentIds?.length
                        ? selected.agentIds
                            .map(
                              (agentId) =>
                                agentsById.get(agentId)?.name ||
                                `${agentId} (not visible)`,
                            )
                            .join(", ")
                        : "No managed agents assigned."}
                    </Typography>
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      <Chip
                        size="small"
                        label={`Use: ${selected.useMinRole || "member"}`}
                      />
                      <Chip
                        size="small"
                        label={`Edit: ${selected.editMinRole || "admin"}`}
                      />
                    </Stack>
                  </CardContent>
                </Card>
              ) : (
                <Typography color="text.secondary">
                  Select an agent team to inspect its membership and role
                  floors.
                </Typography>
              )}
            </Grid>
          </Grid>
        ) : null}
        {mode ? (
          <Box
            component="form"
            onSubmit={save}
            sx={{
              mt: 2,
              p: 2,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
            }}
            aria-label={
              mode === "edit" ? "Edit agent team" : "Create agent team"
            }
          >
            <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
              {mode === "edit" ? "Edit agent team" : "Create an agent team"}
            </Typography>
            <Grid container spacing={1.5}>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  size="small"
                  required
                  label="Name"
                  value={draft.name}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  size="small"
                  required
                  label="Purpose"
                  value={draft.purpose}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      purpose: event.target.value,
                    }))
                  }
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  select
                  fullWidth
                  size="small"
                  label="Minimum role to use"
                  value={draft.useMinRole}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      useMinRole: event.target.value,
                    }))
                  }
                >
                  <MenuItem value="member">Member</MenuItem>
                  <MenuItem value="admin">Admin</MenuItem>
                  <MenuItem value="owner">Owner</MenuItem>
                </TextField>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  select
                  fullWidth
                  size="small"
                  label="Minimum role to edit"
                  value={draft.editMinRole}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      editMinRole: event.target.value,
                    }))
                  }
                >
                  <MenuItem value="admin">Admin</MenuItem>
                  <MenuItem value="owner">Owner</MenuItem>
                </TextField>
              </Grid>
              <Grid item xs={12}>
                <Typography variant="caption" color="text.secondary">
                  Managed agents
                </Typography>
                <Stack
                  direction="row"
                  spacing={1}
                  flexWrap="wrap"
                  sx={{ mt: 0.5 }}
                >
                  {(agents || []).map((agent) => (
                    <FormControlLabel
                      key={agent.id}
                      control={
                        <Checkbox
                          size="small"
                          checked={draft.agentIds.includes(agent.id)}
                          onChange={() => toggleAgent(agent.id)}
                        />
                      }
                      label={agent.name || agent.id}
                    />
                  ))}
                  {(agents || []).length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No visible managed agents are available.
                    </Typography>
                  ) : null}
                </Stack>
              </Grid>
            </Grid>
            <Stack
              direction="row"
              justifyContent="flex-end"
              spacing={1}
              sx={{ mt: 1.5 }}
            >
              <Button
                size="small"
                onClick={() => setMode("")}
                disabled={busy !== ""}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                type="submit"
                disabled={
                  busy !== "" || !draft.name.trim() || !draft.purpose.trim()
                }
              >
                {busy
                  ? "Saving remotely…"
                  : mode === "edit"
                    ? "Save changes"
                    : "Create team"}
              </Button>
            </Stack>
          </Box>
        ) : null}
      </CardContent>
    </Card>
  );
}

AgentTeamsPanel.propTypes = {
  client: PropTypes.object.isRequired,
  agents: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string,
    }),
  ).isRequired,
};
