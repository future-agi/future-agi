import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  IconButton,
  Switch,
  Slider,
  Chip,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Select,
  MenuItem,
} from "@mui/material";
import { useSnackbar } from "notistack";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard, CopyField } from "../components/primitives";
import {
  envVarsOf,
  envVarRefPatch,
  envVarCheckOf,
  isAgentBehaviourKey,
  AGENT_BEHAVIOUR_MESSAGE,
  credentialSetPatch,
} from "../_mock/envConfig";
import {
  validateSecretRef,
  looksLikeRawKey,
  RAW_KEY_MESSAGE,
} from "../_mock/secretRefs";
import {
  SecretRefText,
  SetRefAction,
  SecretRefEditor,
  PreflightLine,
  RefTag,
} from "./SecretRefControls";
import { envMcpServersOf } from "../_mock/worldTools";
import {
  MAX_EGRESS_DOMAINS,
  egressCheck,
  parseEgress,
} from "../agents/connect/preflight";
import {
  currentEnvVersion,
  pendingEnvChanges,
  envChangePatch,
  saveEnvChangesPatch,
  envChangeLabel,
  scenariosInSet,
} from "../_mock/versions";
import { twinById, resolveSeedPromptToJson } from "../_mock/twins";
import TwinLogo from "./../components/TwinLogo";
import { envLabelOf, pendingCostOf } from "../_mock/envVersionKinds";
import {
  snapshotHistoryOf,
  currentEnvSnapshot,
  envSnapshotsOf,
  SNAPSHOT_KIND_LABEL,
} from "../_mock/envSnapshot";
import {
  REPLAY_HEADER,
  REPLAY_SDK,
  stubbedToolNames,
  toggleStubPatch,
} from "../_mock/replay";
import {
  runDefaultsOf,
  SIMULATOR_MODELS,
  REPEATS_MAX,
  PARALLEL_MAX,
  productionScenariosOn,
} from "../_mock/rsiModel";
import { simulatorOf, callerChangePatch } from "../_mock/envModel";
import { envTurnLimitOf, TURN_LIMIT_RANGE } from "../_mock/runStream";
import { useSimStore, useWorkspaceSettings } from "../store";
import RollbackDialog from "./RollbackDialog";

/**
 * Environment settings.
 *
 * Simulation defaults sit here rather than in the SDK on purpose: repeats,
 * conversations at once and the simulated user's model decide what a run
 * costs and whether its results compare, and burying them in code means
 * nobody checks them before pressing Run.
 */
export default function SettingsPanel({
  env,
  envState,
  patch,
  onDelete,
  locked = false,
}) {
  /* Edits made since the pinned version — what "New version" saves. */
  const pending = pendingEnvChanges(envState);
  const pinned = currentEnvVersion(env, envState);
  /* Stored labels read as major.minor (v3 → v2.1). */
  const shown = (label) => envLabelOf(env, envState, label);
  /* The environment version ("v5") pending a Roll back confirmation. */
  const [restoring, setRestoring] = useState(null);
  /* Rename — a field on the environment itself, not a version. */
  const { state, dispatch } = useSimStore();
  const adopted = (state.myEnvironments || []).some((e) => e.id === env.id);
  const [name, setName] = useState(env.name);
  const canRename =
    !locked && adopted && !!name.trim() && name.trim() !== env.name;
  const rename = () => {
    if (canRename)
      dispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: { name: name.trim() },
      });
  };

  return (
    <Box sx={{ p: 2 }}>
      {/* No heading — opens straight on the build details; the tab's
          hover hint carries the description. */}
      <Stack spacing={2}>
        {/* ── identity ── */}
        <SectionCard title="Environment info">
          <Stack spacing={2.25} sx={{ p: 2.5 }}>
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.625 }}>
                Environment name
              </Typography>
              <Stack direction="row" spacing={1}>
                <TextField
                  size="small"
                  value={name}
                  disabled={locked || !adopted}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") rename();
                  }}
                  sx={{
                    flex: 1,
                    maxWidth: 460,
                    "& .MuiInputBase-root": { typography: "s2" },
                  }}
                />
                <Button
                  variant="outlined"
                  size="small"
                  disabled={!canRename}
                  onClick={rename}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Rename
                </Button>
              </Stack>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.625 }}
              >
                The name only — the environment ID below and every run stay as
                they are.
              </Typography>
            </Box>
            <Box sx={{ maxWidth: 460 }}>
              <CopyField
                label="Environment ID"
                value={`${env.id}-bffc78f9-4c2a-424a`}
              />
            </Box>
          </Stack>
        </SectionCard>

        {/* ── twin config — visible only when the env is twin-backed ── */}
        {envState?.twinBacking && (
          <TwinConfigSection
            backing={envState.twinBacking}
            envState={envState}
            patch={patch}
            locked={locked}
          />
        )}

        {/* ── what each new run starts from, saved on the environment ── */}
        <SimulationDefaultsSection
          env={env}
          envState={envState}
          patch={patch}
          locked={locked}
        />

        {/* ── production-derived scenario sources (workspace admin) ── */}
        <ProductionScenariosSection envState={envState} />

        {/* ── replay flag — so the agent can hold back real side effects ── */}
        <ReplaySection env={env} envState={envState} patch={patch} />

        {/* ── env vars — secrets by reference ── */}
        <EnvVarsSection envState={envState} patch={patch} locked={locked} />
        <EgressSection envState={envState} patch={patch} locked={locked} />
        <AgentCredentialsSection envState={envState} />
        <ToolCredentialsSection env={env} envState={envState} />

        {/* ── versions — reproducibility, which HUD does not surface ── */}
        <SectionCard
          title="Versions"
          subtitle="One environment version covers the world, scenarios, sub-goals, evaluations, simulated caller and credentials. Every run pins one, so a result can always be traced to what produced it"
          action={
            !locked && (
              <Tooltip
                arrow
                title={
                  pending.length
                    ? ""
                    : `Nothing to save since environment ${currentEnvSnapshot(env, envState)?.label || shown(pinned.label)} — edit the world, contract or actors first. Scenario, sub-goal, evaluation and caller changes make a version on their own.`
                }
              >
                <span>
                  <Button
                    size="small"
                    disabled={!pending.length}
                    onClick={() => patch(saveEnvChangesPatch(env, envState))}
                    startIcon={
                      <Iconify icon="solar:add-circle-linear" width={16} />
                    }
                    sx={{ color: "text.secondary" }}
                  >
                    New version
                  </Button>
                </span>
              </Tooltip>
            )
          }
        >
          {/*
            Changing the world is an event, not a save. A version records the
            edits made since the one runs pinned — kinds decide which proofs
            survive (rules and evaluations are graded, so every scenario can
            still be staged; seed, scenario checks and tools may not).
          */}
          {pending.length > 0 && (
            <Box
              sx={{
                px: 2.5,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {`Changes since environment ${currentEnvSnapshot(env, envState)?.label || shown(pinned.label)} · ${pendingCostOf(env, envState)?.text}`}
              </Typography>
              <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                {pending.map((c) => (
                  <Typography
                    key={c.at + c.detail}
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    · {envChangeLabel(c.kind)}
                    {c.detail ? ` — ${c.detail}` : ""}
                  </Typography>
                ))}
              </Stack>
            </Box>
          )}
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {snapshotHistoryOf(env, envState).map((v) => {
              return (
                <Stack
                  key={v.label}
                  direction="row"
                  alignItems="center"
                  spacing={2}
                  sx={{ px: 2.5, py: 1 }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontFamily: "ui-monospace, Menlo, monospace",
                      width: 44,
                      flexShrink: 0,
                    }}
                  >
                    {v.label}
                  </Typography>
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >
                        {v.summary}
                      </Typography>
                      {v.current && (
                        <Chip
                          size="small"
                          label="Current"
                          sx={{
                            color: "status.success.fg",
                            bgcolor: "status.success.bg",
                          }}
                        />
                      )}
                    </Stack>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {[
                        v.at && new Date(v.at).toLocaleDateString(),
                        v.kinds.map((k) => SNAPSHOT_KIND_LABEL[k]).join(" + "),
                        `${scenariosInSet(envState, v.parts.env).length} scenarios`,
                        `${v.runs} run${v.runs === 1 ? "" : "s"}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </Box>
                  {/* Roll back makes the next version with everything as in
                    this one — world, scenarios, sub-goals, evaluations and
                    caller. RollbackDialog says what moves first. */}
                  {!v.current && !locked && (
                    <Button
                      size="small"
                      onClick={() => setRestoring(v.label)}
                      startIcon={
                        <Iconify icon="solar:history-linear" width={14} />
                      }
                      sx={{ color: "text.secondary", flexShrink: 0 }}
                    >
                      Roll back
                    </Button>
                  )}
                </Stack>
              );
            })}
          </Stack>
        </SectionCard>

        {/* ── danger zone ── */}
        <SectionCard title="Danger zone">
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={2}
            sx={{ p: 2.5 }}
          >
            <Box flex={1}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Delete this environment
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Removes the environment, its scenarios, evaluations and every
                run against it. Personas stay in your library. Cannot be undone.
              </Typography>
            </Box>
            <Tooltip
              arrow
              title={onDelete ? "" : "Built-in templates can't be deleted."}
            >
              <span>
                <Button
                  variant="outlined"
                  size="small"
                  color="error"
                  disabled={!onDelete}
                  onClick={onDelete}
                  sx={{ flexShrink: 0 }}
                >
                  Delete environment
                </Button>
              </span>
            </Tooltip>
          </Stack>
        </SectionCard>
      </Stack>

      <RollbackDialog
        open={!!restoring}
        targetLabel={restoring}
        env={env}
        envState={envState}
        patch={patch}
        onClose={() => setRestoring(null)}
      />
    </Box>
  );
}

SettingsPanel.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  env: PropTypes.object.isRequired,
  onDelete: PropTypes.func,
  locked: PropTypes.bool,
};

/* ── environment variables ─────────────────────────────────────────────── */

const MONO = "ui-monospace, Menlo, monospace";
const KEY_W = 220;

/*
  Secrets are references into the team's secret manager (AC-6.12): a secret
  row shows where its value lives, never the value or a mask of it, and
  "Set reference" takes only a reference. Pointing a key somewhere else
  re-runs the access check and saves a credentials-only environment version
  (v7 → v7.1): versioned with the environment, but the world behaves the
  same, so nothing is re-checked. Plain variables keep plain values.
*/
/* What runs use to reach the agent — the platform key a test copy or a draft
   is made with. By reference only; read here, set where the agent connects. */
function AgentCredentialsSection({ envState }) {
  const refs = Object.entries(envState?.credentialRefs || {});
  if (!refs.length) return null;
  return (
    <SectionCard
      title="Agent credentials"
      subtitle="What runs use to reach the agent. References into your secret manager — the values are never stored here."
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {refs.map(([k, ref]) => (
          <Stack
            key={k}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1 }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontFamily: MONO,
                width: KEY_W,
                flexShrink: 0,
              }}
            >
              {k}
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <SecretRefText value={ref} />
            </Box>
            <Iconify
              icon="solar:lock-keyhole-minimalistic-linear"
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
AgentCredentialsSection.propTypes = { envState: PropTypes.object };

/*
  The public hosts the sandbox lets the agent reach while it runs —
  hardcoded APIs, TURN endpoints. Everything else is firewalled. The build
  doesn't use the list. A change is a world edit: it waits as an unsaved
  edit once a run has pinned the version, and rollbacks restore the list.
*/
function EgressSection({ envState, patch, locked = false }) {
  const domains = envState?.egressDomains || [];
  const [text, setText] = useState("");
  const [error, setError] = useState(null);
  const write = (next, detail) =>
    patch?.({
      egressDomains: next,
      ...envChangePatch(envState, "network", detail),
    });
  const add = () => {
    const asked = parseEgress(text).filter((d) => !domains.includes(d));
    if (!asked.length) {
      setText("");
      return;
    }
    const check = egressCheck([...domains, ...asked]);
    if (!check.ok) {
      setError(check.detail);
      return;
    }
    write([...domains, ...asked], `Egress: allowed ${asked.join(", ")}`);
    setText("");
    setError(null);
  };
  return (
    <SectionCard
      title={`Egress · ${domains.length}`}
      subtitle={`Public hostnames the agent may reach while it runs — hardcoded APIs or TURN endpoints. Everything else is firewalled at the sandbox. Up to ${MAX_EGRESS_DOMAINS}.`}
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {domains.length === 0 && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              px: 2.5,
              py: 1.375,
            }}
          >
            None — the agent can only reach what the world answers.
          </Typography>
        )}
        {domains.map((d) => (
          <Stack
            key={d}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1 }}
          >
            <Iconify
              icon="solar:global-linear"
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Typography
              sx={{ typography: "s2", fontFamily: MONO, flex: 1, minWidth: 0 }}
            >
              {d}
            </Typography>
            {!locked && patch && (
              <IconButton
                size="small"
                aria-label={`Remove ${d}`}
                onClick={() =>
                  write(
                    domains.filter((x) => x !== d),
                    `Egress: removed ${d}`,
                  )
                }
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            )}
          </Stack>
        ))}
        {!locked && patch && (
          <Stack
            direction="row"
            alignItems="flex-start"
            spacing={1}
            sx={{ px: 2.5, py: 1.25 }}
          >
            <Box flex={1} minWidth={0}>
              <TextField
                fullWidth
                size="small"
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    add();
                  }
                }}
                placeholder="api.example.com, turn.example.com"
                inputProps={{
                  spellCheck: false,
                  "aria-label": "Add egress domains",
                }}
                sx={{
                  "& .MuiInputBase-input": {
                    typography: "s2",
                    fontFamily: MONO,
                  },
                }}
              />
              {error && (
                <Typography
                  sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
                >
                  {error}
                </Typography>
              )}
            </Box>
            <Button
              size="small"
              variant="outlined"
              disabled={!text.trim()}
              onClick={add}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
                mt: 0.25,
              }}
            >
              Allow
            </Button>
          </Stack>
        )}
      </Stack>
    </SectionCard>
  );
}
EgressSection.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/* The tokens the world's MCP tool sources use — references, changed from World → Tool sources. */
function ToolCredentialsSection({ env, envState }) {
  const sources = envMcpServersOf(env, envState).filter(
    (s) => s.transport !== "stdio",
  );
  if (!sources.length) return null;
  return (
    <SectionCard
      title="Tool credentials"
      subtitle="What a Live call to an MCP tool source sends. References into your secret manager, or kept in your Gateway — change one from World → Tool sources."
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {sources.map((src) => (
          <Stack
            key={src.id}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1 }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                width: KEY_W,
                flexShrink: 0,
              }}
            >
              {src.label}
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              {src.gateway ? (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Kept in your Gateway
                </Typography>
              ) : (
                <SecretRefText
                  value={envState?.mcpTokens?.[src.id] || src.tokenRef || null}
                  empty="No token"
                />
              )}
            </Box>
            <Iconify
              icon="solar:lock-keyhole-minimalistic-linear"
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
ToolCredentialsSection.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
};

function EnvVarsSection({ envState, patch, locked = false }) {
  const vars = envVarsOf(envState);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState({ key: "", value: "", secret: true });
  const [addError, setAddError] = useState(null);

  /* Every write goes through the credential set, so a moved reference is the next (credentials-only) version. */
  const save = (next) =>
    patch?.(credentialSetPatch(envState, { envVars: next }));
  const setRef = (key, ref) => {
    patch?.(credentialSetPatch(envState, envVarRefPatch(envState, key, ref)));
    setEditing(null);
  };
  const remove = (key) => save(vars.filter((v) => v.key !== key));

  const add = () => {
    const key = draft.key.trim();
    if (!key) return;
    if (vars.some((v) => v.key === key)) {
      setAddError(`${key} is already set.`);
      return;
    }
    /* Secret or not, a model id or flag changes the agent, not the world. */
    if (isAgentBehaviourKey(key)) {
      setAddError(AGENT_BEHAVIOUR_MESSAGE);
      return;
    }
    if (draft.secret) {
      const res = validateSecretRef(draft.value);
      if (!res.ok) {
        setAddError(res.error);
        return;
      }
      patch?.(
        credentialSetPatch(
          envState,
          envVarRefPatch(
            {
              ...envState,
              envVars: [
                ...vars,
                { key, secret: true, usedBy: "environment", ref: res.ref },
              ],
            },
            key,
            res.ref,
          ),
        ),
      );
    } else {
      if (looksLikeRawKey(draft.value)) {
        setAddError(RAW_KEY_MESSAGE);
        return;
      }
      save([
        ...vars,
        { key, value: draft.value, secret: false, usedBy: "environment" },
      ]);
    }
    setAddError(null);
    setDraft({ key: "", value: "", secret: true });
  };

  return (
    <SectionCard
      title="Environment variables"
      subtitle="Configuration passed into the environment. Secrets are references into your secret manager — the value is resolved inside the sandbox and never stored here. Pointing a secret somewhere else saves a credentials-only version of the environment — a minor one, like v7 → v7.1."
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {vars.map((v) => {
          const check = v.secret ? envVarCheckOf(envState, v.key) : null;
          const open = editing === v.key;
          return (
            <Box key={v.key} sx={{ px: 2.5, py: 1 }}>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: MONO,
                    width: KEY_W,
                    flexShrink: 0,
                  }}
                >
                  {v.key}
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  {v.secret ? (
                    <SecretRefText value={v.ref} />
                  ) : (
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontFamily: MONO,
                        color: "text.secondary",
                        minWidth: 0,
                      }}
                    >
                      {v.value}
                    </Typography>
                  )}
                  {v.migrated && <RefTag>moved from a pasted value</RefTag>}
                </Stack>
                {v.secret && (
                  <Tooltip
                    arrow
                    title="Stored as a reference — the value stays in your secret manager"
                  >
                    <Iconify
                      icon="solar:lock-keyhole-minimalistic-linear"
                      width={14}
                      sx={{ color: "text.tertiary", flexShrink: 0 }}
                    />
                  </Tooltip>
                )}
                <Chip
                  size="small"
                  label={v.usedBy}
                  sx={{
                    color: "text.secondary",
                    flexShrink: 0,
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "transparent",
                  }}
                />
                {v.secret && !locked && !open && (
                  <SetRefAction onClick={() => setEditing(v.key)} />
                )}
                {!locked && (
                  <IconButton
                    size="small"
                    onClick={() => remove(v.key)}
                    aria-label={`Remove ${v.key}`}
                  >
                    <Iconify
                      icon="solar:trash-bin-trash-linear"
                      width={16}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                )}
              </Stack>
              {(open || check) && (
                <Box sx={{ pl: { md: `calc(${KEY_W}px + 12px)` }, pt: 1 }}>
                  {open ? (
                    <SecretRefEditor
                      initial=""
                      placeholder={v.ref || "vault://acme/sim/api_key"}
                      onSave={(ref) => setRef(v.key, ref)}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <PreflightLine check={check} />
                  )}
                </Box>
              )}
            </Box>
          );
        })}
      </Stack>

      {!locked && (
        <Box
          sx={{
            px: 2.5,
            py: 2,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <TextField
              size="small"
              placeholder="ENV_KEY_…"
              value={draft.key}
              onChange={(e) => {
                setDraft((n) => ({ ...n, key: e.target.value }));
                setAddError(null);
              }}
              sx={{
                width: KEY_W,
                "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
              }}
            />
            <TextField
              size="small"
              placeholder={draft.secret ? "vault://acme/sim/…" : "Value"}
              value={draft.value}
              error={!!addError}
              onChange={(e) => {
                setDraft((n) => ({ ...n, value: e.target.value }));
                setAddError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") add();
              }}
              inputProps={{ spellCheck: false, autoComplete: "off" }}
              sx={{
                flex: 1,
                maxWidth: 360,
                "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
              }}
            />
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Switch
                size="small"
                checked={draft.secret}
                onChange={(e) => {
                  setDraft((n) => ({ ...n, secret: e.target.checked }));
                  setAddError(null);
                }}
              />
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Secret
              </Typography>
            </Stack>
            <Button
              variant="outlined"
              size="small"
              onClick={add}
              disabled={!draft.key.trim()}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Add
            </Button>
          </Stack>
          <Typography
            role={addError ? "alert" : undefined}
            sx={{
              typography: "s3",
              color: addError ? "error.main" : "text.tertiary",
              mt: 0.75,
              pl: { md: `calc(${KEY_W}px + 8px)` },
            }}
          >
            {addError ||
              (draft.secret
                ? "A secret is added as a reference — paste its path in your secret manager, not the key."
                : "Plain configuration — stored and shown as typed.")}
          </Typography>
        </Box>
      )}
    </SectionCard>
  );
}
EnvVarsSection.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/* ── replay flag ────────────────────────────────────────────────────────── */

/**
 * The flag every simulated call carries, and which tools the agent should stub
 * when it sees it. The sandbox answers these tools either way; the flag is for
 * the agent's own code, which may call a real system directly (a payments API,
 * a booking backend) — a refund issued during a test is still a refund.
 */
function ReplaySection({ env, envState, patch }) {
  const tools = env.tools || [];
  const stubbed = stubbedToolNames(env, envState);
  return (
    <SectionCard
      title="Replay flag"
      subtitle="Every simulated call says it is one, so your agent can hold back tools with real side effects"
    >
      <Box sx={{ p: 2.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
          <Box sx={{ flex: 1, minWidth: 0, maxWidth: 360 }}>
            <CopyField label="HTTP header" value={REPLAY_HEADER} />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0, maxWidth: 360 }}>
            <CopyField label="SDK context" value={REPLAY_SDK} />
          </Box>
        </Stack>
      </Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={0.75}
        sx={{
          px: 2.5,
          py: 1,
          borderTop: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Tools
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${stubbed.length} of ${tools.length} marked as side effect`}
        </Typography>
      </Stack>
      {tools.length === 0 ? (
        <Typography
          sx={{ px: 2.5, py: 1.5, typography: "s2", color: "text.tertiary" }}
        >
          This environment has no tools yet — nothing to stub.
        </Typography>
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {tools.map((t) => {
            const on = stubbed.includes(t.name);
            return (
              <Stack
                key={t.name}
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ px: 2.5, py: 1 }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: "ui-monospace, Menlo, monospace",
                    width: 220,
                    flexShrink: 0,
                  }}
                >
                  {t.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    typography: "s2",
                    color: "text.tertiary",
                  }}
                >
                  {t.desc || ""}
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ flexShrink: 0 }}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      color: on ? "text.primary" : "text.tertiary",
                    }}
                  >
                    Side effect
                  </Typography>
                  <Switch
                    size="small"
                    checked={on}
                    onChange={(e) =>
                      patch(
                        toggleStubPatch(
                          env,
                          envState,
                          t.name,
                          e.target.checked,
                        ),
                      )
                    }
                  />
                </Stack>
              </Stack>
            );
          })}
        </Stack>
      )}
      <Typography
        sx={{
          px: 2.5,
          py: 1.5,
          typography: "s3",
          color: "text.tertiary",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        Marked tools run against the environment&apos;s sandboxed version; your
        agent should skip the real call when it sees the flag.
      </Typography>
    </SectionCard>
  );
}
ReplaySection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
};

/**
 * Restore preflight dialog.
 *
 * The one thing this needs to spell out is what the fork will invalidate:
 * scenarios proved against the current world may no longer be proved
 * against the restored one, and running with lapsed proofs is exactly
 * the failure the whole versioning story exists to prevent.
 */
/* ── twin config section ────────────────────────────────────────────────── */

/**
 * Twin-backed environment configuration.
 *
 * Sits under Environment info so the settings panel reads top-down as
 * "what this env is → what its world is → how runs behave here".
 * Grouped into four concerns:
 *
 *   · Services       — the twinned SDKs, one row each, with the
 *                      copyable sandbox endpoint runs point at.
 *   · Seed prompt    — editable NL description of the starting state.
 *                      Saving re-resolves the JSON in one step; the
 *                      env's run history isn't invalidated because
 *                      the seed carries a version.
 *   · Danger zone    — reset the current sandbox. (Each run signs in
 *                      to the cloned services with its own test
 *                      sign-ins, so there's nothing to rotate.)
 */
function TwinConfigSection({ backing, envState, patch, locked = false }) {
  const { enqueueSnackbar } = useSnackbar();
  const [seedPrompt, setSeedPrompt] = useState(backing.seedPrompt || "");
  const [showJson, setShowJson] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);

  const services = backing.services || [];
  const dirty = seedPrompt !== (backing.seedPrompt || "");

  const saveSeed = () => {
    const newSeed = resolveSeedPromptToJson(services, seedPrompt.trim());
    patch({
      twinBacking: {
        ...backing,
        seedPrompt: seedPrompt.trim(),
        seed: newSeed,
      },
      ...envChangePatch(envState, "seed", "Seed prompt rewritten"),
    });
    enqueueSnackbar("Seed updated · the next run starts from this state", {
      variant: "success",
    });
  };

  const resetSandbox = () => {
    /*
      In production this pings the twin runtime to tear down and
      re-provision the sandbox. Here we bump `provisionedAt` and
      zero out the activity counters — enough that the Overview
      panel's activity ticker resets visibly, so users see the
      action landed.
    */
    patch({
      twinBacking: {
        ...backing,
        provisionedAt: new Date().toISOString(),
        activity: Object.fromEntries(
          services.map((sId) => [sId, { requests: 0, failures: 0 }]),
        ),
      },
    });
    setResetDialogOpen(false);
    enqueueSnackbar("Sandbox reset · fresh seed installed", {
      variant: "success",
    });
  };

  return (
    <SectionCard
      title="Sandboxes"
      subtitle="How the sandbox is provisioned and seeded per run"
      action={
        <Chip
          size="small"
          label="Sandboxed"
          icon={
            <Iconify
              icon="solar:server-square-linear"
              width={12}
              sx={{
                ml: "6px !important",
                color: (t) => `${t.palette.primary.main} !important`,
              }}
            />
          }
          sx={{
            bgcolor: "status.discovery.bg",
            color: "primary.main",
            border: "1px solid",
            borderColor: "status.discovery.border",
          }}
        />
      }
    >
      <Stack spacing={2.75} sx={{ p: 2.5 }}>
        {/* services */}
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>
            Sandboxed apps ({services.length})
          </Typography>
          <Stack spacing={0.75}>
            {services.map((sId) => {
              const t = twinById(sId);
              const endpoint = backing.endpoints?.[sId] || "—";
              return (
                <Stack
                  key={sId}
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{
                    p: 1.25,
                    borderRadius: "8px",
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "background.paper",
                  }}
                >
                  <Box
                    sx={{
                      width: 24,
                      height: 24,
                      flexShrink: 0,
                      display: "grid",
                      placeItems: "center",
                    }}
                  >
                    <TwinLogo twin={t} width={18} />
                  </Box>
                  <Box flex={1} minWidth={0}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {t?.name || sId}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}
                    >
                      {endpoint}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => {
                      navigator.clipboard?.writeText(endpoint);
                      enqueueSnackbar("Endpoint copied", { variant: "info" });
                    }}
                  >
                    <Iconify
                      icon="solar:copy-linear"
                      width={14}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                </Stack>
              );
            })}
          </Stack>
        </Box>

        {/* seed prompt */}
        <Box>
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={0.75}
            sx={{ mb: 0.625 }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Seed prompt
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              — natural language description of the starting state
            </Typography>
          </Stack>
          <TextField
            fullWidth
            multiline
            minRows={3}
            value={seedPrompt}
            disabled={locked}
            onChange={(e) => setSeedPrompt(e.target.value)}
            placeholder="Describe the state each run should start with…"
            sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!dirty || locked}
              onClick={saveSeed}
            >
              Save & re-resolve
            </Button>
            <Button
              size="small"
              disabled={!dirty}
              onClick={() => setSeedPrompt(backing.seedPrompt || "")}
              sx={{ color: "text.secondary" }}
            >
              Discard
            </Button>
            <Box flex={1} />
            <Button
              size="small"
              onClick={() => setShowJson((v) => !v)}
              startIcon={
                <Iconify
                  icon={
                    showJson
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={12}
                />
              }
              sx={{ color: "text.secondary" }}
            >
              {showJson ? "Hide resolved JSON" : "View resolved JSON"}
            </Button>
          </Stack>
          {showJson && (
            <Box
              sx={{
                mt: 1,
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
                maxHeight: 320,
                overflow: "auto",
              }}
            >
              <Typography
                component="pre"
                sx={{
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "text.primary",
                  whiteSpace: "pre",
                  m: 0,
                }}
              >
                {backing.seed || "{}"}
              </Typography>
            </Box>
          )}
        </Box>

        {/* The world resets before every conversation — always; not a setting. */}
        {/* actions */}
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Button
            variant="outlined"
            size="small"
            onClick={() => setResetDialogOpen(true)}
            startIcon={
              <Iconify icon="solar:refresh-circle-linear" width={14} />
            }
            sx={{
              color: "status.danger.fg",
              borderColor: "status.danger.border",
              "&:hover": {
                borderColor: "status.danger.solid",
                bgcolor: "status.danger.bg",
              },
            }}
          >
            Reset sandbox now
          </Button>
        </Stack>
      </Stack>

      <Dialog
        open={resetDialogOpen}
        onClose={() => setResetDialogOpen(false)}
        PaperProps={{
          sx: {
            borderRadius: 2,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 600, p: 2.5, pb: 1 }}>
          Reset the sandbox?
        </DialogTitle>
        <DialogContent sx={{ p: 2.5, pt: 1 }} dividers>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            The current sandbox state is discarded and re-provisioned from the
            current seed prompt. In-flight runs against this environment will
            fail. Past runs remain in history.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2, pt: 1.5 }}>
          <Button
            size="small"
            onClick={() => setResetDialogOpen(false)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={resetSandbox}
            sx={{
              bgcolor: "status.danger.solid",
              color: "common.white",
              "&:hover": { bgcolor: "status.danger.fg" },
            }}
          >
            Reset sandbox
          </Button>
        </DialogActions>
      </Dialog>
    </SectionCard>
  );
}
TwinConfigSection.propTypes = {
  backing: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

function NumberSetting({ label, help, value, min, max, step = 1, onChange }) {
  return (
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Stack
        direction="row"
        alignItems="baseline"
        justifyContent="space-between"
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {label}
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </Typography>
      </Stack>
      <Slider
        size="small"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(_, v) => onChange(v)}
        sx={{ py: 1, "& .MuiSlider-thumb": { width: 12, height: 12 } }}
      />
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {help}
      </Typography>
    </Box>
  );
}
NumberSetting.propTypes = {
  label: PropTypes.string,
  help: PropTypes.string,
  value: PropTypes.number,
  min: PropTypes.number,
  max: PropTypes.number,
  step: PropTypes.number,
  onChange: PropTypes.func,
};

function ToggleSetting({ label, help, checked, onChange }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
    >
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {label}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {help}
        </Typography>
      </Box>
      <Switch
        size="small"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </Stack>
  );
}
ToggleSetting.propTypes = {
  label: PropTypes.string,
  help: PropTypes.string,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
};

/*
  Simulation defaults (RSI PRD) — what a new run of this environment starts
  from: how many times each scenario repeats (1–20), how many conversations
  run at once (up to 4 when parallel runs are on) and the model that plays
  the simulated user. Saved on the environment as envState.runDefaults, so a
  run and an improvement read the same numbers.
*/
function SimulationDefaultsSection({ env, envState, patch, locked = false }) {
  const d = runDefaultsOf(envState);
  const save = (next) => {
    if (!locked && patch)
      patch({ runDefaults: { ...(envState?.runDefaults || {}), ...next } });
  };
  /* Parallel runs live on envState.parallelRuns (default on) — what a run reads. */
  const setParallel = (on) => {
    if (locked || !patch) return;
    patch({
      parallelRuns: on,
      runDefaults: {
        ...(envState?.runDefaults || {}),
        conversationsAtOnce: on
          ? Math.max(
              2,
              d.conversationsAtOnce > 1 ? d.conversationsAtOnce : PARALLEL_MAX,
            )
          : 1,
      },
    });
  };
  const voice = env?.surface === "voice";
  const turnLimit = envTurnLimitOf(envState);
  /* The caller new runs pin — chosen here, else the surface's default. */
  const model =
    envState?.runDefaults?.simulatorModel || simulatorOf(env, envState).model;
  const models = SIMULATOR_MODELS.some((m) => m.id === model)
    ? SIMULATOR_MODELS
    : [
        { id: model, label: model, note: "The default for this channel." },
        ...SIMULATOR_MODELS,
      ];
  /* Newest first: each change, then the caller the environment started with. */
  const changes = envState?.callerChanges || [];
  /* Each change made the next environment version — named so the history
     and the run headers can be matched up. */
  const snaps = envSnapshotsOf(env, envState);
  const madeIn = (c) =>
    snaps.find(
      (x) =>
        x.kinds.includes("caller") &&
        x.parts.caller === c.to &&
        Date.parse(x.at || 0) >= Date.parse(c.at || 0) - 2000,
    )?.label;
  const callerRows = [
    ...[...changes].reverse().map((c, i) => ({
      key: `${c.label}-${c.at}`,
      title: `${c.from} → ${c.to}`,
      sub: `${new Date(c.at).toLocaleDateString()}${madeIn(c) ? ` · made environment ${madeIn(c)}` : ""} · runs before this don't compare with runs after`,
      current: i === 0,
    })),
    {
      key: "caller-first",
      title: changes[0]?.from || model,
      sub: "The simulated user this environment started with",
      current: !changes.length,
    },
  ];
  return (
    <SectionCard
      title="Simulation defaults"
      subtitle="What every new run of this environment starts from — a run can still change them"
    >
      <Stack spacing={2.5} sx={{ p: 2.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={3}>
          <NumberSetting
            label="Repeats per scenario"
            help={`Each scenario runs this many times, 1 to ${REPEATS_MAX}. Three tells a flaky scenario from a failing one.`}
            value={d.repeats}
            min={1}
            max={REPEATS_MAX}
            onChange={(v) => save({ repeats: v })}
          />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack
              direction="row"
              alignItems="baseline"
              justifyContent="space-between"
            >
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Conversations at once
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {d.conversationsAtOnce}
              </Typography>
            </Stack>
            <Slider
              size="small"
              value={d.conversationsAtOnce}
              min={1}
              max={PARALLEL_MAX}
              step={1}
              disabled={locked || !d.parallel}
              onChange={(_, v) => save({ conversationsAtOnce: Math.max(2, v) })}
              sx={{ py: 1, "& .MuiSlider-thumb": { width: 12, height: 12 } }}
            />
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {d.parallel
                ? `Up to ${PARALLEL_MAX} at once while parallel runs are on.`
                : `One at a time. Turn on parallel runs to run up to ${PARALLEL_MAX} at once.`}
            </Typography>
          </Box>
        </Stack>
        <ToggleSetting
          label={`Run conversations in parallel — up to ${PARALLEL_MAX} at once`}
          help={
            voice
              ? "Place several calls at once. Your provider's concurrency limit still applies, and a live agent takes them all at the same time. Off, one at a time."
              : "Run several conversations at once. Results are the same; a run finishes sooner. Off, one at a time."
          }
          checked={d.parallel}
          onChange={setParallel}
        />
        <NumberSetting
          label="Turn limit"
          help={`A conversation stops after this many agent turns and fails with “Hit the turn limit”. A scenario's own limit wins${voice ? " — a voice scenario's Max turns" : ""}. ${TURN_LIMIT_RANGE.min} to ${TURN_LIMIT_RANGE.max}.`}
          value={turnLimit}
          min={TURN_LIMIT_RANGE.min}
          max={TURN_LIMIT_RANGE.max}
          onChange={(v) => save({ turnLimit: v })}
        />
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
        >
          <Box minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Simulated user&apos;s model
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {SIMULATOR_MODELS.find((m) => m.id === model)?.note ||
                "The model that plays the caller in every scenario."}
              {
                " Part of the environment version a run holds fixed — changing it makes the next version, and runs before and after it don't compare."
              }
            </Typography>
          </Box>
          <Select
            size="small"
            value={model}
            disabled={locked}
            onChange={(e) => {
              if (!locked && patch)
                patch(callerChangePatch(env, envState, e.target.value));
            }}
            sx={{ minWidth: 200, typography: "s2", flexShrink: 0 }}
          >
            {models.map((m) => (
              <MenuItem key={m.id} value={m.id} sx={{ typography: "s2" }}>
                {m.label}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      </Stack>
      {/* Caller history — each change is also a step in the environment's version history. */}
      <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
        <Typography
          sx={{
            px: 2.5,
            pt: 1.25,
            pb: 0.5,
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
          }}
        >
          Simulated caller changes
        </Typography>
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {callerRows.map((c) => (
            <Stack
              key={c.key}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2.5, py: 1 }}
            >
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
                  {c.title}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {c.sub}
                </Typography>
              </Box>
              {c.current && (
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.secondary",
                    flexShrink: 0,
                  }}
                >
                  Current
                </Typography>
              )}
            </Stack>
          ))}
        </Stack>
      </Box>
    </SectionCard>
  );
}
SimulationDefaultsSection.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/*
  Scenarios from production (RSI PRD) — a workspace-admin setting, not an
  environment's. Off, the production-derived sources (the Error Feed and
  production conversations replayed word for word) are unavailable on Add
  scenarios in every environment, with the reason. Scenarios already added
  stay. Stored on the workspace (`state.workspaceSettings.productionScenarios`);
  this card shows its state, links out, and lets an admin change it in place.
*/
function ProductionScenariosSection({ envState }) {
  const { settings, set, isAdmin, readOnlyReason } = useWorkspaceSettings();
  const on = productionScenariosOn(envState, settings);
  return (
    <SectionCard
      title="Scenarios from production"
      subtitle="A workspace setting — it applies to every environment"
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={2}
        sx={{ p: 2.5 }}
      >
        <Box minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon={
                on
                  ? "solar:check-circle-linear"
                  : "solar:forbidden-circle-linear"
              }
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {on ? "On for this workspace" : "Off for this workspace"}
            </Typography>
          </Stack>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              mt: 0.25,
              maxWidth: 640,
            }}
          >
            {on
              ? "Error Feed issues and production conversations can become scenarios. Names, phone numbers, emails, addresses, account and card numbers are replaced with fake values before a scenario is saved; call audio is never reused."
              : "The Error Feed and production conversations are unavailable as sources on Add scenarios. Scenarios already added stay."}
          </Typography>
          <Typography
            component={RouterLink}
            to={paths.dashboard.settings.orgSettings}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              mt: 0.5,
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              textDecoration: "none",
              "&:hover": { color: "text.primary", textDecoration: "underline" },
            }}
          >
            Managed in workspace settings
            <Iconify icon="solar:arrow-right-up-linear" width={12} />
          </Typography>
        </Box>
        <Tooltip
          arrow
          title={
            isAdmin
              ? `Workspace admin · ${on ? "turns it off" : "turns it on"} for every environment`
              : readOnlyReason
          }
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={!isAdmin}
              onClick={() => set("productionScenarios", !on)}
              sx={{
                color: "text.secondary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              {on ? "Turn off for workspace" : "Turn on for workspace"}
            </Button>
          </span>
        </Tooltip>
      </Stack>
    </SectionCard>
  );
}
ProductionScenariosSection.propTypes = { envState: PropTypes.object };
