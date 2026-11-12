import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { GATEWAY_MONO } from "../../utils/fonts";

const KEY_STATE = {
  idle: "loading",
  loading: "loading",
  ready: "ready",
  error: "error",
};

// The exact status and error code under a plain sentence, for a developer
// who matches it; the sentence above says what happens in words.
function CodeLine({ children }) {
  return (
    <Typography
      variant="caption"
      component="p"
      color="text.secondary"
      data-testid="admission-code"
      sx={{ fontFamily: GATEWAY_MONO, mt: 0.5 }}
    >
      {children}
    </Typography>
  );
}
CodeLine.propTypes = { children: PropTypes.node };

function warningsFor(report) {
  const warnings = report.uncapped.map(
    ({ key, agent }) =>
      `Key ${key} is bound to "${agent}", which has no agent cap in this draft: no agent cap applies to it (its key cap and the per-run ceiling still apply).`,
  );
  if (report.unknownKeys.length)
    warnings.push(
      `${report.unknownKeys.join(", ")} ${report.unknownKeys.length === 1 ? "is" : "are"} not an active key of this organization. Check the key ID: a mistyped ID leaves the intended key unbound.`,
    );
  if (report.rolloutConflicts.length)
    warnings.push(
      `A routing rollout is configured and its candidate routing does not repeat the binding for ${report.rolloutConflicts.join(", ")}. The server will refuse this save until the rollout candidate repeats it; this panel never edits a rollout.`,
    );
  return warnings;
}

export default function AdmissionBindingsEditor({
  draft,
  onChange,
  busy,
  report,
  keys,
  onReloadKeys,
}) {
  const bindings = draft.bindings || [];
  const state = KEY_STATE[keys.status] || "loading";
  const activeKeys = keys.keys.filter((key) => key.status === "active");
  const agentIds = draft.agents.map((row) => row.id).filter(Boolean);
  const warnings = warningsFor(report);
  const setBindings = (next) => onChange({ ...draft, bindings: next });
  const updateRow = (index, field, value) =>
    setBindings(
      bindings.map((row, i) =>
        i === index ? { ...row, [field]: value } : row,
      ),
    );
  // Selecting a key adds its binding; with one agent cap the agent is preset.
  const bindKey = (key) =>
    setBindings([
      ...bindings,
      { key, agent: agentIds.length === 1 ? agentIds[0] : "" },
    ]);
  const ids = report.unbound.map((key) => key.id).join(", ");
  return (
    <Stack spacing={1} data-testid="admission-bindings">
      <Typography variant="subtitle2">Key-to-agent bindings</Typography>
      {report.required ? (
        <Alert severity="warning" data-testid="admission-binding-disclosure">
          While any agent cap exists, the gateway refuses requests from every
          execution key that has no binding below, including keys that have no
          cap at all. Bind each key that should keep working; bind a key to an
          agent that has no cap to leave it without an agent cap. Rebinding a
          key moves its future spend to the new agent&apos;s cap; past spend is
          not moved.
          <CodeLine>402 call_cap_unenforceable</CodeLine>
        </Alert>
      ) : (
        <Alert severity="info" data-testid="admission-binding-disclosure">
          This draft has no agent cap, so key caps and the per-run ceiling work
          without bindings, and stored bindings are kept. Once an agent cap
          exists, the gateway refuses requests from every execution key without
          a binding.
          <CodeLine>402 call_cap_unenforceable</CodeLine>
        </Alert>
      )}
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography
          variant="body2"
          color="text.secondary"
          role="status"
          data-testid="admission-keys-status"
          data-state={state}
        >
          {state === "loading" && "Loading this organization's keys…"}
          {state === "ready" &&
            `${activeKeys.length} active key${activeKeys.length === 1 ? "" : "s"} found in this organization.`}
          {state === "error" &&
            "This organization's keys could not be read, so the keys that would be refused cannot be listed. Add bindings by key ID."}
        </Typography>
        {state !== "loading" && (
          <Button size="small" onClick={onReloadKeys} disabled={busy}>
            Reload keys
          </Button>
        )}
      </Stack>
      {bindings.map((row, index) => (
        <Stack
          key={index}
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ sm: "center" }}
        >
          <TextField
            size="small"
            fullWidth
            label={`Binding ${index + 1} key ID`}
            value={row.key}
            onChange={(event) => updateRow(index, "key", event.target.value)}
          />
          <TextField
            size="small"
            fullWidth
            label={`Binding ${index + 1} agent`}
            value={row.agent}
            onChange={(event) => updateRow(index, "agent", event.target.value)}
          />
          <IconButton
            size="small"
            aria-label={`Remove binding ${index + 1} from draft`}
            onClick={() => setBindings(bindings.filter((_, i) => i !== index))}
          >
            <Icon icon="solar:trash-bin-trash-linear" width={16} />
          </IconButton>
        </Stack>
      ))}
      <Button
        size="small"
        variant="outlined"
        sx={{ alignSelf: "flex-start" }}
        onClick={() => setBindings([...bindings, { key: "", agent: "" }])}
      >
        Add binding
      </Button>
      {report.unbound.length > 0 && (
        <Stack spacing={0.5} data-testid="admission-unbound-keys">
          <Typography variant="subtitle2" color="warning.main">
            Keys that will be refused until they are bound
          </Typography>
          <CodeLine>402 call_cap_unenforceable</CodeLine>
          {report.unbound.map((key) => (
            <Stack
              key={key.id}
              direction="row"
              alignItems="center"
              gap={1}
              flexWrap="wrap"
            >
              <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
                {key.name === key.id ? key.id : `${key.name} · ${key.id}`}
              </Typography>
              <Button size="small" onClick={() => bindKey(key.id)}>
                {`Bind ${key.id}`}
              </Button>
            </Stack>
          ))}
        </Stack>
      )}
      {warnings.length > 0 && (
        <Stack spacing={1} data-testid="admission-warnings">
          {warnings.map((text) => (
            <Alert key={text} severity="warning">
              {text}
            </Alert>
          ))}
        </Stack>
      )}
      {report.needsAck && (
        <FormControlLabel
          data-testid="admission-unbound-ack"
          control={
            <Checkbox
              checked={draft.ack === report.signature}
              onChange={(event) =>
                onChange({
                  ...draft,
                  ack: event.target.checked ? report.signature : "",
                })
              }
            />
          }
          label={
            report.listUnavailable
              ? "I understand that this organization's key list could not be read, so every key not bound above will be refused once I activate."
              : `I understand that ${report.unbound.length} key${report.unbound.length === 1 ? "" : "s"} without a binding will be refused once I activate: ${ids}.`
          }
        />
      )}
    </Stack>
  );
}
AdmissionBindingsEditor.propTypes = {
  draft: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  report: PropTypes.object.isRequired,
  keys: PropTypes.object.isRequired,
  onReloadKeys: PropTypes.func.isRequired,
};
