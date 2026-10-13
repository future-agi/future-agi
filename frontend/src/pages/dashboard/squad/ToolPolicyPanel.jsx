import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

const PROTOCOL = "omega-workforce-tool-policy/v1";
const MODES = ["auto", "ask", "deny"];
const LABELS = {
  auto: "Auto",
  ask: "Ask",
  deny: "Deny",
  intrinsic: "Tool default",
};
const STRENGTH = { auto: 0, intrinsic: 0, ask: 1, deny: 2 };
const record = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const version = (value) => Number.isSafeInteger(value) && value > 0;
const label = (mode) => LABELS[mode] || "Not reported";
const strength = (mode) => STRENGTH[mode] ?? 0;
const requestKey = () =>
  "squad-tool-policy-" +
  (globalThis.crypto?.randomUUID?.() ||
    Date.now() + "-" + Math.random().toString(36).slice(2));
const invalid = () => ({
  available: false,
  code: "INVALID_RESPONSE",
  reason: "The server did not return a valid tool policy for this agent.",
});
const failed = () => ({
  available: false,
  code: "NETWORK_ERROR",
  reason:
    "The remote tool policy request failed. Your draft has been retained.",
});

function validPolicy(value) {
  return (
    record(value) &&
    value.protocol === PROTOCOL &&
    (value.defaultMode === undefined || MODES.includes(value.defaultMode)) &&
    Object.keys(value).every((key) =>
      ["protocol", "defaultMode", "tools"].includes(key),
    ) &&
    record(value.tools) &&
    Object.keys(value.tools).length <= 256 &&
    Object.entries(value.tools).every(
      ([id, entry]) =>
        id.trim().length > 0 &&
        id === id.trim() &&
        record(entry) &&
        MODES.includes(entry.mode) &&
        (entry.reason === undefined || typeof entry.reason === "string") &&
        Object.keys(entry).every((key) => ["mode", "reason"].includes(key)),
    )
  );
}

function validProjection(result, agentId) {
  if (result?.available !== true)
    return result?.available === false ? result : invalid();
  const ids = new Set();
  if (
    result.schemaVersion !== "omega-workforce/v1" ||
    result.agentId !== agentId ||
    !version(result.currentVersion) ||
    (result.deployedVersion !== undefined &&
      (!version(result.deployedVersion) ||
        result.deployedVersion > result.currentVersion)) ||
    !validPolicy(result.toolPolicy) ||
    (result.deployedToolPolicy !== undefined &&
      (!version(result.deployedVersion) ||
        !validPolicy(result.deployedToolPolicy))) ||
    !Array.isArray(result.tools) ||
    result.tools.some((tool) => {
      if (
        !record(tool) ||
        typeof tool.toolId !== "string" ||
        !tool.toolId.trim() ||
        ids.has(tool.toolId) ||
        ![...MODES, "intrinsic"].includes(tool.mode) ||
        !MODES.includes(tool.effectiveMode) ||
        typeof tool.configured !== "boolean" ||
        typeof tool.available !== "boolean" ||
        (tool.mandatoryApproval !== undefined &&
          typeof tool.mandatoryApproval !== "boolean")
      )
        return true;
      ids.add(tool.toolId);
      return false;
    })
  )
    return invalid();
  return result;
}

function copyPolicy(policy) {
  return {
    protocol: PROTOCOL,
    ...(policy.defaultMode === undefined
      ? {}
      : { defaultMode: policy.defaultMode }),
    tools: Object.fromEntries(
      Object.entries(policy.tools).map(([id, entry]) => [id, { ...entry }]),
    ),
  };
}

function samePolicy(a, b) {
  return (
    a?.defaultMode === b?.defaultMode &&
    Object.keys(a?.tools || {}).length === Object.keys(b?.tools || {}).length &&
    Object.entries(a?.tools || {}).every(
      ([id, entry]) =>
        entry.mode === b?.tools?.[id]?.mode &&
        entry.reason === b?.tools?.[id]?.reason,
    )
  );
}

function ErrorNotice({ error }) {
  return (
    <Alert severity="error">
      {error.reason || "The request could not be completed."}
      {error.code ? " (" + error.code + ")" : ""}
    </Alert>
  );
}
ErrorNotice.propTypes = { error: PropTypes.object.isRequired };

export default function ToolPolicyPanel({
  agentId,
  client,
  canEdit = false,
  onSaved,
}) {
  const scope = useMemo(() => ({ agentId, client }), [agentId, client]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const requestId = useRef(0);
  const [state, setState] = useState(null);
  const visible = state?.scope === scope ? state : null;

  const load = useCallback(
    async (discardDraft = false) => {
      const request = ++requestId.current;
      const current = () =>
        currentScope.current === scope && requestId.current === request;
      setState((old) => ({
        ...(old?.scope === scope ? old : {}),
        scope,
        loading: true,
        error: null,
      }));
      let next;
      try {
        next = validProjection(
          await client.getWorkforceAgentTools(agentId),
          agentId,
        );
      } catch {
        next = failed();
      }
      if (!current()) return;
      setState((old) => {
        const previous = old?.scope === scope ? old : {};
        if (!next.available)
          return {
            ...previous,
            scope,
            loading: false,
            blocked: true,
            error: next,
          };
        const retain = !discardDraft && previous.dirty && previous.draft;
        return {
          ...previous,
          scope,
          projection: next,
          loading: false,
          blocked: false,
          error: null,
          draft: retain ? previous.draft : copyPolicy(next.toolPolicy),
          baseVersion: retain ? previous.baseVersion : next.currentVersion,
          dirty: Boolean(retain),
        };
      });
    },
    [scope, client, agentId],
  );

  useEffect(() => {
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);

  const projection = visible?.projection;
  const draft = visible?.draft;
  const stale = Boolean(
    projection && visible.baseVersion !== projection.currentVersion,
  );
  const rows = projection
    ? [
        ...projection.tools,
        ...Object.keys(draft?.tools || {})
          .filter((id) => !projection.tools.some((row) => row.toolId === id))
          .map((toolId) => ({ toolId, available: false })),
      ]
    : [];
  const busy = visible?.loading || visible?.saving;
  const editable = canEdit && projection && !busy && !visible?.blocked;
  const edit = (update) => {
    if (!editable) return;
    setState((old) => ({
      ...old,
      draft: update(copyPolicy(old.draft)),
      dirty: true,
      message: null,
      error: null,
    }));
  };

  const save = async () => {
    if (!editable || stale || !visible?.dirty || !validPolicy(draft)) return;
    const request = ++requestId.current;
    const current = () =>
      currentScope.current === scope && requestId.current === request;
    const desired = copyPolicy(draft);
    const expectedVersion = visible.baseVersion;
    setState((old) => ({ ...old, saving: true, error: null, message: null }));
    let response;
    try {
      response = await client.updateWorkforceAgent(
        agentId,
        { expectedVersion, toolPolicy: desired },
        requestKey(),
      );
    } catch {
      response = failed();
    }
    if (!current()) return;
    if (!response?.available) {
      setState((old) => ({
        ...old,
        saving: false,
        error: response?.available === false ? response : invalid(),
        blocked: response?.status === 403 || response?.status === 404,
      }));
      return;
    }
    // A 2xx is not enough: read back the canonical policy and version before
    // clearing the draft or presenting the new effective permissions.
    let readback;
    try {
      readback = validProjection(
        await client.getWorkforceAgentTools(agentId),
        agentId,
      );
    } catch {
      readback = failed();
    }
    if (!current()) return;
    if (
      !readback.available ||
      readback.currentVersion <= expectedVersion ||
      !samePolicy(readback.toolPolicy, desired)
    ) {
      setState((old) => ({
        ...old,
        saving: false,
        blocked: true,
        error: readback.available
          ? {
              available: false,
              code: "SAVE_UNVERIFIED",
              reason:
                "The server did not confirm this draft as the current tool policy. Refresh and review before saving again.",
            }
          : readback,
      }));
      return;
    }
    setState({
      scope,
      projection: readback,
      draft: copyPolicy(readback.toolPolicy),
      baseVersion: readback.currentVersion,
      loading: false,
      saving: false,
      dirty: false,
      blocked: false,
      message:
        "Tool policy saved and read back. Tighter restrictions apply to new tool dispatches; looser settings require redeployment.",
    });
    onSaved?.(response);
  };

  return (
    <Card variant="outlined" component="section" aria-label="Tool policy">
      <CardContent>
        <Stack spacing={2}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            gap={1}
          >
            <Typography variant="h6">Tool policy</Typography>
            <Button
              size="small"
              onClick={() => void load()}
              disabled={Boolean(busy)}
            >
              Refresh policy
            </Button>
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Auto uses a tool under existing safeguards. Ask requires approval.
            Deny blocks the call. Mandatory approval and operator restrictions
            always apply, including when Auto is selected. Looser settings take
            effect after redeployment.
          </Typography>
          {visible?.loading ? (
            <CircularProgress size={22} aria-label="Loading tool policy" />
          ) : null}
          {visible?.error ? <ErrorNotice error={visible.error} /> : null}
          {projection && draft ? (
            <>
              <Typography variant="body2">
                Authored revision {projection.currentVersion} ·{" "}
                {projection.deployedVersion
                  ? "Deployed revision " + projection.deployedVersion
                  : "Not deployed"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Saved default:{" "}
                {projection.toolPolicy.defaultMode
                  ? label(projection.toolPolicy.defaultMode)
                  : "Tool default"}
                {" · "}Deployed default:{" "}
                {projection.deployedVersion
                  ? label(
                      projection.deployedToolPolicy?.defaultMode || "intrinsic",
                    )
                  : "Not deployed"}
              </Typography>
              {stale ? (
                <Alert severity="warning">
                  A newer agent revision is available. Your draft is retained
                  against revision {visible.baseVersion}. Review the saved modes
                  below before applying your draft to revision{" "}
                  {projection.currentVersion}.
                </Alert>
              ) : null}
              <TextField
                select
                SelectProps={{ native: true }}
                size="small"
                label="Default tool mode"
                value={draft.defaultMode || ""}
                disabled={!editable}
                onChange={(event) =>
                  edit((next) => {
                    if (event.target.value)
                      next.defaultMode = event.target.value;
                    else delete next.defaultMode;
                    return next;
                  })
                }
              >
                <option value="">Use tool defaults</option>
                {MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {label(mode)}
                  </option>
                ))}
              </TextField>
              {rows.length ? (
                rows.map((tool) => (
                  <Card key={tool.toolId} variant="outlined">
                    <CardContent>
                      <Stack spacing={1}>
                        <Typography variant="subtitle2">
                          {tool.toolId}
                        </Typography>
                        <Stack
                          direction="row"
                          spacing={1}
                          useFlexGap
                          flexWrap="wrap"
                        >
                          <Chip
                            size="small"
                            label={
                              "Saved: " +
                              label(
                                projection.toolPolicy.tools[tool.toolId]
                                  ?.mode ||
                                  projection.toolPolicy.defaultMode ||
                                  "intrinsic",
                              )
                            }
                          />
                          <Chip
                            size="small"
                            label={
                              "Deployed: " +
                              (projection.deployedVersion
                                ? label(
                                    projection.deployedToolPolicy?.tools[
                                      tool.toolId
                                    ]?.mode ||
                                      projection.deployedToolPolicy
                                        ?.defaultMode ||
                                      "intrinsic",
                                  )
                                : "Not deployed")
                            }
                          />
                          <Chip
                            size="small"
                            label={"Effective: " + label(tool.effectiveMode)}
                          />
                          {tool.mandatoryApproval ? (
                            <Chip
                              size="small"
                              color="warning"
                              label="Mandatory approval"
                            />
                          ) : null}
                          {strength(tool.effectiveMode) >
                          Math.max(
                            strength(
                              projection.toolPolicy.tools[tool.toolId]?.mode ||
                                projection.toolPolicy.defaultMode ||
                                "intrinsic",
                            ),
                            strength(
                              projection.deployedToolPolicy?.tools[tool.toolId]
                                ?.mode ||
                                projection.deployedToolPolicy?.defaultMode ||
                                "intrinsic",
                            ),
                          ) ? (
                            <Chip
                              size="small"
                              color="warning"
                              label="Runtime/operator floor"
                            />
                          ) : null}
                          {!tool.available ? (
                            <Chip size="small" label="Not in current catalog" />
                          ) : null}
                        </Stack>
                        {projection.toolPolicy.tools[tool.toolId]?.reason ? (
                          <Typography variant="body2" color="text.secondary">
                            {projection.toolPolicy.tools[tool.toolId].reason}
                          </Typography>
                        ) : null}
                        <TextField
                          select
                          SelectProps={{ native: true }}
                          size="small"
                          label={"Mode for " + tool.toolId}
                          value={draft.tools[tool.toolId]?.mode || ""}
                          disabled={!editable}
                          onChange={(event) =>
                            edit((next) => {
                              if (event.target.value)
                                next.tools[tool.toolId] = {
                                  ...next.tools[tool.toolId],
                                  mode: event.target.value,
                                };
                              else delete next.tools[tool.toolId];
                              return next;
                            })
                          }
                        >
                          <option value="">Use agent default</option>
                          {MODES.map((mode) => (
                            <option
                              key={mode}
                              value={mode}
                              disabled={
                                !tool.available &&
                                mode !== "deny" &&
                                draft.tools[tool.toolId]?.mode !== mode
                              }
                            >
                              {label(mode)}
                            </option>
                          ))}
                        </TextField>
                      </Stack>
                    </CardContent>
                  </Card>
                ))
              ) : (
                <Typography variant="body2">
                  No tools are currently reported. A default policy also applies
                  to tools added later.
                </Typography>
              )}
              {!canEdit ? (
                <Alert severity="info">
                  You do not have permission to edit this agent. Its tool policy
                  is shown for review.
                </Alert>
              ) : null}
              {visible?.message ? (
                <Alert severity="success">{visible.message}</Alert>
              ) : null}
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
                <Button
                  variant="contained"
                  onClick={() => void save()}
                  disabled={!editable || stale || !visible?.dirty}
                >
                  {visible?.saving ? "Saving…" : "Save tool policy"}
                </Button>
                {stale && visible?.dirty ? (
                  <Button
                    disabled={!editable}
                    onClick={() =>
                      setState((old) => ({
                        ...old,
                        baseVersion: old.projection.currentVersion,
                        error: null,
                      }))
                    }
                  >
                    Apply draft to latest revision
                  </Button>
                ) : null}
                {visible?.dirty ? (
                  <Button
                    disabled={Boolean(busy)}
                    onClick={() => void load(true)}
                  >
                    Discard draft and reload
                  </Button>
                ) : null}
              </Stack>
            </>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

ToolPolicyPanel.propTypes = {
  agentId: PropTypes.string.isRequired,
  client: PropTypes.shape({
    getWorkforceAgentTools: PropTypes.func.isRequired,
    updateWorkforceAgent: PropTypes.func.isRequired,
  }).isRequired,
  canEdit: PropTypes.bool,
  onSaved: PropTypes.func,
};
