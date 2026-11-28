import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import {
  pollWhileChecking,
  toVersion,
  useAgentVersionValues,
  useChangeVersion,
} from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { Card, LabeledField, LineSkeleton, Pill } from "./bits";
import { MONO } from "./format";
import {
  changeBody,
  draftEmpty,
  draftSummary,
  emptyDraft,
  newNameProblem,
  removeName,
  setValue,
  summaryLines,
  undoName,
} from "./agentValues";
import useAgentRoute from "./useAgentRoute";

function ValueRow({ row, pending, canEdit, onChange, onRemove, onUndo }) {
  const [editing, setEditing] = useState(false);
  const [value, setValueText] = useState("");
  const removed = pending === "remove";
  const changed = pending === "set";
  return (
    <Box
      data-testid={`agent-value-${row.name}`}
      sx={{
        px: 1.5,
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: "none" },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography
          sx={{
            typography: "s2_1",
            fontFamily: MONO,
            fontWeight: 500,
            textDecoration: removed ? "line-through" : "none",
            color: removed ? "text.disabled" : "text.primary",
          }}
        >
          {row.name}
        </Typography>
        {row.required && <Pill>Required</Pill>}
        {row.madeByEnvironment && <Pill>Made by the test environment</Pill>}
        {changed && <Pill tone="primary">{row.added ? "New" : "Changed"}</Pill>}
        <Box sx={{ flex: 1 }} />
        {canEdit && (removed || changed) && (
          <Button size="small" onClick={() => onUndo(row.name)}>
            Undo
          </Button>
        )}
        {canEdit && !removed && !changed && !editing && (
          <>
            {!row.madeByEnvironment && (
              <Button size="small" onClick={() => setEditing(true)}>
                Change
              </Button>
            )}
            {!row.providerKey && (
              <Button
                size="small"
                color="error"
                onClick={() => onRemove(row.name)}
              >
                Remove
              </Button>
            )}
          </>
        )}
      </Stack>
      {row.madeByEnvironment && !removed && (
        <Typography
          sx={{ typography: "s2", color: "status.warning.fg", mt: 0.5 }}
        >
          The test environment makes this value itself, so builds and runs are
          refused while the agent has one of its own. Remove it.
        </Typography>
      )}
      {removed && row.required && (
        <Typography
          sx={{ typography: "s2", color: "status.warning.fg", mt: 0.5 }}
        >
          The agent needs this value: builds are refused until it has one again,
          and runs warn that it is missing.
        </Typography>
      )}
      {editing && (
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <TextField
            type="password"
            size="small"
            fullWidth
            autoComplete="off"
            placeholder={`New value for ${row.name}`}
            inputProps={{ "aria-label": `New value for ${row.name}` }}
            value={value}
            onChange={(e) => setValueText(e.target.value)}
          />
          <Button
            size="small"
            variant="outlined"
            disabled={!value}
            onClick={() => {
              onChange(row.name, value);
              setValueText("");
              setEditing(false);
            }}
          >
            Keep
          </Button>
          <Button
            size="small"
            onClick={() => {
              setValueText("");
              setEditing(false);
            }}
          >
            Cancel
          </Button>
        </Stack>
      )}
    </Box>
  );
}

ValueRow.propTypes = {
  row: PropTypes.shape({
    name: PropTypes.string.isRequired,
    required: PropTypes.bool,
    madeByEnvironment: PropTypes.bool,
    // A voice provider's key (Vapi, Retell, LiveKit): changed, never removed.
    providerKey: PropTypes.bool,
    // Added in this draft, not yet saved.
    added: PropTypes.bool,
  }).isRequired,
  pending: PropTypes.oneOf(["set", "remove", null]),
  canEdit: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onUndo: PropTypes.func.isRequired,
};

function AddValue({ rows, madeByEnvironment, draft, onAdd }) {
  const [name, setName] = useState("");
  const [value, setValueText] = useState("");
  const [touched, setTouched] = useState(false);
  const pendingNames = Object.keys(draft.set).map((one) => ({ name: one }));
  const problem = newNameProblem(
    name,
    [...rows, ...pendingNames],
    madeByEnvironment,
  );
  const submit = (event) => {
    event.preventDefault();
    setTouched(true);
    if (problem || !value) return;
    onAdd(name.trim(), value);
    setName("");
    setValueText("");
    setTouched(false);
  };
  return (
    <Box component="form" onSubmit={submit} sx={{ px: 1.5, py: 1.5 }}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <LabeledField
          id="agent-value-new-name"
          label="Name"
          error={touched && name ? problem : null}
          sx={{ flex: 1 }}
        >
          <TextField
            id="agent-value-new-name"
            size="small"
            fullWidth
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </LabeledField>
        <LabeledField id="agent-value-new-value" label="Value" sx={{ flex: 1 }}>
          <TextField
            id="agent-value-new-value"
            type="password"
            size="small"
            fullWidth
            autoComplete="off"
            value={value}
            onChange={(e) => setValueText(e.target.value)}
          />
        </LabeledField>
        <Box sx={{ pt: 3 }}>
          <Button
            type="submit"
            size="small"
            variant="outlined"
            disabled={!name || !value}
          >
            Add
          </Button>
        </Box>
      </Stack>
    </Box>
  );
}

AddValue.propTypes = {
  rows: PropTypes.array.isRequired,
  madeByEnvironment: PropTypes.arrayOf(PropTypes.string).isRequired,
  draft: PropTypes.object.isRequired,
  onAdd: PropTypes.func.isRequired,
};

/**
 * The agent's env values, by name only, on its production version: change,
 * add or remove them. Saving makes a new version; once its check passes it
 * becomes production and the environments following production move to it.
 */
export default function AgentValuesTab() {
  const { agentId } = useAgentRoute();
  // Nothing typed for one agent is kept when the page shows another.
  return <AgentValues key={agentId ?? ""} />;
}

function AgentValues() {
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const { agentId, agent } = useAgentRoute();
  const productionId = agent?.activeVersionId ?? null;
  const values = useAgentVersionValues(agentId, productionId);
  const change = useChangeVersion(agentId);
  const versions = useAgentDefinitionVersions({
    selectedAgentId: agentId,
    refetchInterval: pollWhileChecking,
  });
  // A save starts from production: one made while another version is still
  // being checked would leave that version's change out.
  const checking = useMemo(
    () =>
      (versions.data?.pages?.[0]?.results ?? [])
        .map(toVersion)
        .find((v) => v.check === "pending") ?? null,
    [versions.data],
  );
  const [draft, setDraft] = useState(emptyDraft);
  const managed = !!agent?.managedBy;
  const canEdit =
    !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role] && !managed;

  const rows = values.data?.names ?? [];
  const madeByEnvironment = values.data?.madeByEnvironment ?? [];
  const missing = values.data?.missingRequired ?? [];
  const summary = draftSummary(draft, rows);
  const added = summary.added.map((name) => ({ name, added: true }));

  const save = () => {
    change.mutate(changeBody(draft), {
      onSuccess: (data) => {
        setDraft(emptyDraft());
        const label = data?.version?.version_name_display;
        enqueueSnackbar(
          data?.created
            ? `${label || "A new version"} saved and is being checked`
            : `${label || "An earlier version"} already has exactly these values`,
          { variant: "success" },
        );
        const stillMissing = data?.missing_required_names ?? [];
        if (stillMissing.length) {
          enqueueSnackbar(
            `Builds are refused until ${stillMissing.join(", ")} ${
              stillMissing.length === 1 ? "has a value" : "have values"
            }`,
            { variant: "warning" },
          );
        }
      },
      onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
    });
  };

  const pendingOf = (name) => {
    if (draft.remove.includes(name)) return "remove";
    if (name in draft.set) return "set";
    return null;
  };
  const loading = !agent || values.isLoading;

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Values of the production version
        </Typography>
        {agent?.activeVersionNumber != null && (
          <Typography
            sx={{ typography: "s2_1", fontFamily: MONO, fontWeight: 500 }}
          >
            v{agent.activeVersionNumber}
          </Typography>
        )}
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          · names only; values are never shown
        </Typography>
      </Stack>
      {managed && (
        <Alert severity="info">
          An environment manages this agent; change its values from{" "}
          {agent.managedBy.name}.
        </Alert>
      )}
      {missing.length > 0 && (
        <Alert severity="warning">
          The agent needs a value for {missing.join(", ")}. Builds are refused
          until {missing.length === 1 ? "it has one" : "each has one"}, and runs
          warn that {missing.length === 1 ? "it is" : "they are"} missing.
        </Alert>
      )}
      {values.isError && (
        <Alert severity="error">{errorMessage(values.error)}</Alert>
      )}
      <Card title="Values">
        {loading ? (
          <Box sx={{ px: 1.5, py: 1 }}>
            <LineSkeleton width={160} />
          </Box>
        ) : rows.length || added.length ? (
          [...rows, ...added].map((row) => (
            <ValueRow
              key={row.name}
              row={row}
              pending={pendingOf(row.name)}
              canEdit={canEdit}
              onChange={(name, value) =>
                setDraft((current) => setValue(current, name, value))
              }
              onRemove={(name) =>
                setDraft((current) => removeName(current, name))
              }
              onUndo={(name) => setDraft((current) => undoName(current, name))}
            />
          ))
        ) : (
          <Typography
            sx={{ px: 1.5, py: 1, typography: "s2", color: "text.secondary" }}
          >
            No values yet
          </Typography>
        )}
        {canEdit && !loading && (
          <AddValue
            rows={rows}
            madeByEnvironment={madeByEnvironment}
            draft={draft}
            onAdd={(name, value) =>
              setDraft((current) => setValue(current, name, value))
            }
          />
        )}
      </Card>
      {canEdit && !draftEmpty(draft) && (
        <Card title="Save as a new version">
          <Stack spacing={1} sx={{ px: 1.5, py: 1.5 }}>
            {summaryLines(summary).map((line) => (
              <Typography key={line} sx={{ typography: "s2_1" }}>
                {line}
              </Typography>
            ))}
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Saving makes a new version. Nothing is read again and nothing is
              rebuilt. Once its check passes it becomes the production version,
              and the environments that follow production move to it.
            </Typography>
            {summary.namesChange && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                A name added or removed: each environment is prepared again
                before its next run. That uses sandbox time, no model calls.
              </Typography>
            )}
            {checking && (
              <Typography sx={{ typography: "s2", color: "status.warning.fg" }}>
                {checking.label} is still being checked. Save once its check
                ends, so this change starts from it.
              </Typography>
            )}
            <Stack direction="row" spacing={1}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={change.isPending || !!checking}
                onClick={save}
              >
                Save values
              </Button>
              <Button size="small" onClick={() => setDraft(emptyDraft())}>
                Discard changes
              </Button>
            </Stack>
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
