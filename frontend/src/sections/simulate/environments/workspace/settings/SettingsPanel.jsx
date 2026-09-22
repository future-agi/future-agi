import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, IconButton, Switch, Slider, Chip,
  Tooltip, MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  DEFAULT_ENV_VARS, DEFAULT_BUILD_ARGS, DEFAULT_RUNTIME, ISOLATION_OPTIONS,
} from "src/api/simulate-environments/_fixtures/envConfig";
import { environmentVersions, nextEnvVersion } from "src/api/simulate-environments/_fixtures/versions";
import { HARNESS_DETAIL_ENABLED } from "src/api/simulate-environments/environment";
import { useRenameEnvironment } from "src/api/simulate-environments/environments";
import SectionCard from "../../components/SectionCard";
import CopyField from "../../components/CopyField";
import MockBadge from "../../components/MockBadge";
import { validateEnvName, MAX_ENV_NAME } from "../renameEnvironment";

const LOCK_TOOLTIP = "Fork this environment to edit.";

/**
 * Why an environment gets a new version — named rather than free-text because
 * the *kind* of change decides which proofs survive it. Reseeding can strip the
 * state a scenario presumes and rewriting checks can flip a check that used to
 * fail, so those invalidate proofs; a rules change only alters how a run is
 * judged, so every scenario stays stageable and its proofs survive.
 */
const ENV_CHANGES = [
  { id: "seed", label: "Reseeded the world", invalidates: true },
  { id: "checks", label: "Rewrote the checks", invalidates: true },
  { id: "contract", label: "Tools changed", invalidates: true },
  { id: "rules", label: "Rules changed", invalidates: false },
];

// Region isn't carried by envConfig.js — the run default only names one value,
// so a minimal picker is offered around it. us-east-1 (DEFAULT_RUNTIME.region)
// must be present or the select shows a blank.
const REGION_OPTIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-south-1"];

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/**
 * Environment settings.
 *
 * Run defaults sit here rather than in the SDK on purpose: concurrency,
 * timeouts and isolation decide what a run costs and whether its results mean
 * anything, and burying them in code means nobody checks them before pressing
 * Run.
 *
 * Env vars, build args and run defaults have no real backend yet, so each of
 * those section headers carries a MockBadge. Versions are derived from real env
 * state, so that section is not badged.
 */
export default function SettingsPanel({ env, envState, patch, locked = false }) {
  const [vars, setVars] = useState(DEFAULT_ENV_VARS);
  const [newVar, setNewVar] = useState({ key: "", value: "", secret: true });
  const [buildArgs, setBuildArgs] = useState(
    DEFAULT_BUILD_ARGS.map((a) => `${a.key}=${a.value}`).join("\n"),
  );
  const [runtime, setRuntime] = useState(DEFAULT_RUNTIME);
  const [reveal, setReveal] = useState({});
  // The version pending a Restore confirmation. Null when the dialog is closed.
  const [restoring, setRestoring] = useState(null);
  // Whether the inline "New version" change-kind picker is open, and which
  // change kinds are ticked. Multiple can be true — one world change can be
  // several things at once.
  const [changing, setChanging] = useState(false);
  const [selectedChanges, setSelectedChanges] = useState([]);

  const setRun = (next) => setRuntime((r) => ({ ...r, ...next }));

  // §8 rename — the second rename surface alongside the header. Gated on the §6
  // detail path (its response is the §6 body and the workspace only reflects the
  // new name once the detail cache drives `env`), and disabled on a locked
  // template. The name is the only editable field on a finished environment.
  const rename = useRenameEnvironment();
  const [nameDraft, setNameDraft] = useState(env?.name || "");
  const [nameError, setNameError] = useState(null);
  const canRename = HARNESS_DETAIL_ENABLED && !locked;
  const nameChanged = (nameDraft ?? "").trim() !== (env?.name || "");
  const saveName = () => {
    const check = validateEnvName(nameDraft);
    if (!check.ok) {
      setNameError(check.error);
      return;
    }
    setNameError(null);
    rename.mutate(
      { id: env.id, name: check.value },
      { onError: (e) => setNameError(e?.message || "Couldn't rename the environment.") },
    );
  };

  const toggleChange = (id) =>
    setSelectedChanges((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const cancelNewVersion = () => {
    setChanging(false);
    setSelectedChanges([]);
  };

  const createVersion = () => {
    if (!selectedChanges.length) return;
    // environmentVersions returns newest-first; the stored envVersions list is
    // oldest-first, so mirror the Restore path — reverse the seeded history
    // before appending so the persisted order stays what the fixture expects.
    const list = envState?.envVersions?.length
      ? envState.envVersions
      : [...environmentVersions(env, envState)].reverse();
    const note = ENV_CHANGES.filter((c) => selectedChanges.includes(c.id))
      .map((c) => c.label)
      .join(", ");
    const version = nextEnvVersion(env, envState, { changed: selectedChanges, note });
    patch?.({ envVersions: [...list, version], activeEnvVersion: version.label });
    cancelNewVersion();
  };

  const addVar = () => {
    if (!newVar.key.trim()) return;
    setVars((v) => [...v, { ...newVar, key: newVar.key.trim(), usedBy: "environment" }]);
    setNewVar({ key: "", value: "", secret: true });
  };

  const confirmRestore = () => {
    // Restore forks: it mints v(N+1) carrying the same intent as the older row
    // and pins it active. environmentVersions returns newest-first; the stored
    // envVersions list is oldest-first, so the reverse before appending is
    // load-bearing — it keeps the persisted history in the order the fixture
    // expects.
    const list = envState?.envVersions?.length
      ? envState.envVersions
      : [...environmentVersions(env, envState)].reverse();
    const forked = nextEnvVersion(env, envState, {
      changed: restoring.changed || [],
      note: `Restored from ${restoring.label} — ${restoring.note}`,
    });
    patch?.({ envVersions: [...list, forked], activeEnvVersion: forked.label });
    setRestoring(null);
  };

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Settings</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 760 }}>
          How this environment is built and how runs behave inside it.
        </Typography>
      </Box>

      <Stack spacing={2}>
        {canRename && (
          <SectionCard
            title="Environment name"
            subtitle="The only field you can change on a built environment"
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.5}
              alignItems={{ sm: "flex-start" }}
              sx={{ p: 2.5 }}
            >
              <TextField
                fullWidth
                size="small"
                label="Name"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                error={Boolean(nameError)}
                helperText={nameError || `1–${MAX_ENV_NAME} characters.`}
                inputProps={{ maxLength: MAX_ENV_NAME + 1 }}
                sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
              />
              <Button
                variant="contained"
                onClick={saveName}
                disabled={!nameChanged || rename.isPending}
                sx={{ typography: "s2", fontWeight: "fontWeightBold", mt: { sm: 0.25 } }}
              >
                {rename.isPending ? "Saving…" : "Save"}
              </Button>
            </Stack>
          </SectionCard>
        )}
        <SectionCard
          title="Run defaults"
          subtitle="Every run inherits these unless it overrides them"
          action={<MockBadge />}
        >
          <LockTooltip locked={locked}>
            <Stack spacing={2.75} sx={{ p: 2.5 }}>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Isolation</Typography>
                <Stack spacing={1}>
                  {ISOLATION_OPTIONS.map((o) => {
                    const on = runtime.isolation === o.value;
                    return (
                      <Box
                        key={o.value}
                        onClick={() => setRun({ isolation: o.value })}
                        sx={{
                          p: 1.5, borderRadius: 1.25, cursor: "pointer",
                          border: "1px solid",
                          borderColor: on ? "primary.main" : "divider",
                          bgcolor: (t) => (on ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05) : "transparent"),
                        }}
                      >
                        <Stack direction="row" alignItems="center" spacing={0.75}>
                          <Iconify
                            icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                            width={15}
                            sx={{ color: on ? "primary.main" : "text.subtitle" }}
                          />
                          <Typography sx={{ typography: "s2", fontWeight: 600 }}>{o.label}</Typography>
                          {o.value === "persistent" && (
                            <Chip
                              size="small"
                              label="Not comparable"
                              sx={{
                                height: 18, borderRadius: 0.5, color: "#CA8A04",
                                bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.16 : 0.1),
                                "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
                              }}
                            />
                          )}
                        </Stack>
                        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, pl: 2.75 }}>
                          {o.desc}
                        </Typography>
                      </Box>
                    );
                  })}
                </Stack>
              </Box>

              <Stack direction={{ xs: "column", md: "row" }} spacing={3}>
                <NumberSetting
                  label="Concurrency"
                  help="Tasks running at once"
                  value={runtime.concurrency}
                  min={1} max={32}
                  onChange={(v) => setRun({ concurrency: v })}
                />
                <NumberSetting
                  label="Task timeout"
                  help="Seconds before a task is abandoned"
                  value={runtime.taskTimeoutS}
                  min={30} max={1800} step={30}
                  onChange={(v) => setRun({ taskTimeoutS: v })}
                />
                <NumberSetting
                  label="Step budget"
                  help="Max agent steps per task"
                  value={runtime.stepBudget}
                  min={5} max={200} step={5}
                  onChange={(v) => setRun({ stepBudget: v })}
                />
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} spacing={3} alignItems={{ md: "flex-end" }}>
                <NumberSetting
                  label="Retries"
                  help="Re-attempts after a failed task"
                  value={runtime.retries}
                  min={0} max={5}
                  onChange={(v) => setRun({ retries: v })}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Region</Typography>
                  <TextField
                    select
                    fullWidth
                    size="small"
                    value={runtime.region}
                    onChange={(e) => setRun({ region: e.target.value })}
                    sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
                  >
                    {REGION_OPTIONS.map((r) => (
                      <MenuItem key={r} value={r} sx={{ typography: "s2" }}>{r}</MenuItem>
                    ))}
                  </TextField>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
                    Where instances are provisioned
                  </Typography>
                </Box>
              </Stack>

              <Stack spacing={1.5}>
                <ToggleSetting
                  label="File tracking"
                  help="File changes inside the environment appear as diffs on the trace."
                  checked={runtime.fileTracking}
                  onChange={(v) => setRun({ fileTracking: v })}
                />
                <ToggleSetting
                  label="Record video"
                  help="Capture a replay of every task. Adds storage cost."
                  checked={runtime.recordVideo}
                  onChange={(v) => setRun({ recordVideo: v })}
                />
              </Stack>
            </Stack>
          </LockTooltip>
        </SectionCard>

        <SectionCard
          title="Environment variables"
          subtitle="Runtime secrets and configuration passed into the environment"
          action={<MockBadge />}
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {vars.map((v, i) => (
              <Stack key={v.key} direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.375 }}>
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, width: 220, flexShrink: 0 }}
                >
                  {v.key}
                </Typography>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <CopyField value={v.secret && !reveal[v.key] ? "••••••••••••" : v.value} />
                </Box>
                <Chip
                  size="small"
                  label={v.usedBy}
                  sx={{
                    height: 19, borderRadius: 0.5, color: "text.secondary",
                    border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                    "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
                  }}
                />
                {v.secret && (
                  <IconButton size="small" onClick={() => setReveal((r) => ({ ...r, [v.key]: !r[v.key] }))}>
                    <Iconify
                      icon={reveal[v.key] ? "solar:eye-closed-linear" : "solar:eye-linear"}
                      width={15}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
                )}
                <IconButton
                  size="small"
                  disabled={locked}
                  onClick={() => setVars((x) => x.filter((_, idx) => idx !== i))}
                >
                  <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                </IconButton>
              </Stack>
            ))}
          </Stack>

          <LockTooltip locked={locked}>
            <Stack
              direction="row" alignItems="center" spacing={1}
              sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
            >
              <TextField
                size="small"
                placeholder="ENV_KEY_…"
                value={newVar.key}
                onChange={(e) => setNewVar((n) => ({ ...n, key: e.target.value }))}
                sx={{ width: 220, "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO } }}
              />
              <TextField
                size="small"
                placeholder="Value"
                value={newVar.value}
                onChange={(e) => setNewVar((n) => ({ ...n, value: e.target.value }))}
                sx={{ flex: 1, maxWidth: 360, "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO } }}
              />
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Switch
                  size="small"
                  checked={newVar.secret}
                  onChange={(e) => setNewVar((n) => ({ ...n, secret: e.target.checked }))}
                />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Secret</Typography>
              </Stack>
              <Button
                variant="outlined"
                size="small"
                onClick={addVar}
                disabled={!newVar.key.trim()}
                sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
              >
                Add
              </Button>
            </Stack>
          </LockTooltip>
        </SectionCard>

        <SectionCard
          title="Build arguments"
          subtitle="Passed at image build time, not at runtime. One per line: KEY=value"
          action={<MockBadge />}
        >
          <Box sx={{ p: 2.5 }}>
            <TextField
              fullWidth
              multiline
              minRows={3}
              value={buildArgs}
              onChange={(e) => setBuildArgs(e.target.value)}
              InputProps={{ readOnly: locked }}
              sx={{ "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO } }}
            />
          </Box>
        </SectionCard>

        <SectionCard
          title="Versions"
          subtitle="Every run pins a version, so a result can always be traced to the world that produced it"
          action={(
            <LockTooltip locked={locked}>
              <Button
                size="small"
                disabled={locked}
                onClick={() => (changing ? cancelNewVersion() : setChanging(true))}
                startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
              >
                New version
              </Button>
            </LockTooltip>
          )}
        >
          {/*
            Changing the world is an event, not a save. Every proof in this
            environment is a claim about one version of it, so a change that
            reseeds the data or rewrites the checks has to invalidate those
            claims rather than quietly outdate them. Naming the kind of change is
            what decides which survive: rules are graded, so they leave every
            scenario stageable; seed, checks and tools do not.
          */}
          {changing && (
            <Box
              sx={{
                px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <Typography sx={{ typography: "s2", fontWeight: 700, mb: 1 }}>
                What changed in the world?
              </Typography>
              <Stack spacing={0.75} sx={{ mb: 1.5 }}>
                {ENV_CHANGES.map((c) => {
                  const on = selectedChanges.includes(c.id);
                  return (
                    <Stack
                      key={c.id}
                      direction="row" alignItems="flex-start" spacing={1}
                      onClick={() => toggleChange(c.id)}
                      sx={{
                        p: 1.25, borderRadius: 1, cursor: "pointer",
                        border: "1px solid", borderColor: on ? "primary.main" : "divider",
                      }}
                    >
                      <Iconify
                        icon={on ? "solar:check-square-bold" : "solar:stop-linear"}
                        width={15}
                        sx={{ color: on ? "primary.main" : "text.disabled", flexShrink: 0, mt: "1px" }}
                      />
                      <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
                        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{c.label}</Typography>
                        <Typography
                          sx={{
                            typography: "s3", fontWeight: 700,
                            color: c.invalidates ? "#CA8A04" : "text.subtitle",
                          }}
                        >
                          {c.invalidates ? "invalidates proofs" : "proofs survive"}
                        </Typography>
                      </Stack>
                    </Stack>
                  );
                })}
              </Stack>
              <Stack direction="row" spacing={1}>
                <Button
                  size="small" variant="contained" color="primary"
                  disabled={!selectedChanges.length}
                  onClick={createVersion}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  Create {nextEnvVersion(env, envState).label}
                </Button>
                <Button
                  size="small"
                  onClick={cancelNewVersion}
                  sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                >
                  Cancel
                </Button>
              </Stack>
            </Box>
          )}
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {environmentVersions(env, envState).map((v) => (
              <Stack key={v.label} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
                <Typography
                  sx={{ typography: "s2", fontWeight: 700, fontFamily: MONO, width: 44, flexShrink: 0 }}
                >
                  {v.label}
                </Typography>
                <Box flex={1} minWidth={0}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{v.note}</Typography>
                    {v.current && (
                      <Chip
                        size="small"
                        label="Current"
                        sx={{
                          height: 18, borderRadius: 0.5, color: "#16A34A",
                          bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                          "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
                        }}
                      />
                    )}
                  </Stack>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {new Date(v.createdAt).toLocaleDateString()} · {v.scenarios} scenarios
                  </Typography>
                </Box>
                {!v.current && (
                  <LockTooltip locked={locked}>
                    <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
                      <Button
                        size="small"
                        onClick={() => patch?.({ activeEnvVersion: v.label })}
                        startIcon={<Iconify icon="solar:arrow-right-linear" width={13} />}
                        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                      >
                        Switch
                      </Button>
                      <Button
                        size="small"
                        onClick={() => setRestoring(v)}
                        startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
                        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                      >
                        Restore
                      </Button>
                    </Stack>
                  </LockTooltip>
                )}
              </Stack>
            ))}
          </Stack>
        </SectionCard>
      </Stack>

      {/*
        Restore preflight. Restoring an older env version forks — it mints
        v(N+1) with the older row's intent and pins it active. The old versions
        are still there, but scenarios proved against the newer world may lapse
        against the new active version, so the confirm spells that out rather
        than doing it silently on click.
      */}
      <ConfirmDialog
        open={!!restoring}
        onClose={() => setRestoring(null)}
        title={restoring ? `Restore ${restoring.label}?` : "Restore version?"}
        content={(
          <Typography component="span" sx={{ typography: "s2" }}>
            Forks the environment: mints the next version with the same intent as{" "}
            <b>{restoring?.label}</b> and pins it active. This may invalidate scenarios
            proved against the current version — they would need re-proving.
          </Typography>
        )}
        action={(
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            onClick={confirmRestore}
          >
            Restore
          </Button>
        )}
      />
    </Box>
  );
}

SettingsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/**
 * Wraps mutating controls so a template that hasn't been forked reads as
 * read-only: the controls still render (real values stay visible) but are inert
 * and greyed, and hovering explains why.
 */
function LockTooltip({ locked, children }) {
  if (!locked) return children;
  return (
    <Tooltip title={LOCK_TOOLTIP} arrow>
      <Box component="span" sx={{ display: "block", cursor: "not-allowed" }}>
        <Box sx={{ opacity: 0.6, pointerEvents: "none" }}>{children}</Box>
      </Box>
    </Tooltip>
  );
}
LockTooltip.propTypes = {
  locked: PropTypes.bool,
  children: PropTypes.node,
};

function NumberSetting({ label, help, value, min, max, step = 1, onChange }) {
  return (
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Stack direction="row" alignItems="baseline" justifyContent="space-between">
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography>
        <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
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
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{help}</Typography>
    </Box>
  );
}
NumberSetting.propTypes = {
  label: PropTypes.string, help: PropTypes.string, value: PropTypes.number,
  min: PropTypes.number, max: PropTypes.number, step: PropTypes.number, onChange: PropTypes.func,
};

function ToggleSetting({ label, help, checked, onChange }) {
  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2}>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{help}</Typography>
      </Box>
      <Switch size="small" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </Stack>
  );
}
ToggleSetting.propTypes = {
  label: PropTypes.string, help: PropTypes.string,
  checked: PropTypes.bool, onChange: PropTypes.func,
};
