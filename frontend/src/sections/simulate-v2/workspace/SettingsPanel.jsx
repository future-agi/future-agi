import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, IconButton, Switch, Slider, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { SectionCard, CopyField } from "../components/primitives";
import {
  DEFAULT_ENV_VARS, DEFAULT_BUILD_ARGS, DEFAULT_RUNTIME,
  ISOLATION_OPTIONS,
} from "../_mock/envConfig";
import { environmentVersions, nextEnvVersion, ENV_CHANGES } from "../_mock/versions";
import { INVALIDATING, staleScenarios } from "../_mock/proofs";
import { twinById, resolveSeedPromptToJson } from "../_mock/twins";
import TwinLogo from "./../components/TwinLogo";

const TWIN_TINT = "#7857FC";

/**
 * Environment settings.
 *
 * Run defaults sit here rather than in the SDK on purpose: concurrency,
 * timeouts and isolation decide what a run costs and whether its results mean
 * anything, and burying them in code means nobody checks them before pressing
 * Run. The pre-flight quotes its estimate from these numbers.
 */
export default function SettingsPanel({ env, envState, patch }) {
  const [changing, setChanging] = useState(null);
  /* The version pending a Restore confirmation. Null when the dialog is
     closed. Holds the actual version object so the preflight can read
     its `changed` list. */
  const [restoring, setRestoring] = useState(null);
  const [vars, setVars] = useState(DEFAULT_ENV_VARS);
  const [newVar, setNewVar] = useState({ key: "", value: "", secret: true });
  const [buildArgs, setBuildArgs] = useState(
    DEFAULT_BUILD_ARGS.map((a) => `${a.key}=${a.value}`).join("\n"),
  );
  const [runtime, setRuntime] = useState(DEFAULT_RUNTIME);
  const [reveal, setReveal] = useState({});

  const set = (patch) => setRuntime((r) => ({ ...r, ...patch }));

  const addVar = () => {
    if (!newVar.key.trim()) return;
    setVars((v) => [...v, { ...newVar, key: newVar.key.trim(), usedBy: "environment" }]);
    setNewVar({ key: "", value: "", secret: true });
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
                  defaultValue={env.name}
                  sx={{ flex: 1, maxWidth: 460, "& .MuiInputBase-root": { typography: "s2" } }}
                />
                <Button
                  variant="outlined"
                  size="small"
                  sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
                >
                  Rename
                </Button>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.625 }}>
                Renaming changes the identifier used by runs and SDK clients.
              </Typography>
            </Box>
            <Box sx={{ maxWidth: 460 }}>
              <CopyField label="Environment ID" value={`${env.id}-bffc78f9-4c2a-424a`} />
            </Box>
          </Stack>
        </SectionCard>

        {/* ── twin config — visible only when the env is twin-backed ── */}
        {envState?.twinBacking && (
          <TwinConfigSection
            backing={envState.twinBacking}
            envState={envState}
            patch={patch}
          />
        )}

        {/* ── run defaults — the part HUD leaves to the SDK ── */}
        <SectionCard
          title="Run defaults"
          subtitle="Every run inherits these unless it overrides them"
        >
          <Stack spacing={2.75} sx={{ p: 2.5 }}>
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Isolation</Typography>
              <Stack spacing={1}>
                {ISOLATION_OPTIONS.map((o) => {
                  const on = runtime.isolation === o.value;
                  return (
                    <Box
                      key={o.value}
                      onClick={() => set({ isolation: o.value })}
                      sx={{
                        p: 1.5, borderRadius: 1.25, cursor: "pointer",
                        border: "1px solid",
                        borderColor: on ? "primary.main" : "divider",
                        bgcolor: (t) => on ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05) : "transparent",
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
                onChange={(v) => set({ concurrency: v })}
              />
              <NumberSetting
                label="Task timeout"
                help="Seconds before a task is abandoned"
                value={runtime.taskTimeoutS}
                min={30} max={1800} step={30}
                onChange={(v) => set({ taskTimeoutS: v })}
              />
              <NumberSetting
                label="Step budget"
                help="Max agent steps per task"
                value={runtime.stepBudget}
                min={5} max={200} step={5}
                onChange={(v) => set({ stepBudget: v })}
              />
            </Stack>

            <Stack spacing={1.5}>
              <ToggleSetting
                label="File tracking"
                help="File changes inside the environment appear as diffs on the trace."
                checked={runtime.fileTracking}
                onChange={(v) => set({ fileTracking: v })}
              />
              <ToggleSetting
                label="Record video"
                help="Capture a replay of every task. Adds storage cost."
                checked={runtime.recordVideo}
                onChange={(v) => set({ recordVideo: v })}
              />
            </Stack>
          </Stack>
        </SectionCard>

        {/* ── env vars ── */}
        <SectionCard
          title="Environment variables"
          subtitle="Runtime secrets and configuration passed into the environment"
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {vars.map((v, i) => (
              <Stack key={v.key} direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.375 }}>
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", width: 220, flexShrink: 0 }}
                >
                  {v.key}
                </Typography>
                <Typography
                  noWrap
                  sx={{ flex: 1, typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", color: "text.subtitle" }}
                >
                  {v.secret && !reveal[v.key] ? "••••••••••••" : v.value}
                </Typography>
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
                <IconButton size="small" onClick={() => setVars((x) => x.filter((_, idx) => idx !== i))}>
                  <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                </IconButton>
              </Stack>
            ))}
          </Stack>

          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
          >
            <TextField
              size="small"
              placeholder="ENV_KEY_…"
              value={newVar.key}
              onChange={(e) => setNewVar((n) => ({ ...n, key: e.target.value }))}
              sx={{ width: 220, "& .MuiInputBase-root": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
            <TextField
              size="small"
              placeholder="Value"
              value={newVar.value}
              onChange={(e) => setNewVar((n) => ({ ...n, value: e.target.value }))}
              sx={{ flex: 1, maxWidth: 360, "& .MuiInputBase-root": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
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
        </SectionCard>

        {/* ── build args ── */}
        <SectionCard
          title="Build arguments"
          subtitle="Passed at image build time, not at runtime. One per line: KEY=value"
        >
          <Box sx={{ p: 2.5 }}>
            <TextField
              fullWidth
              multiline
              minRows={3}
              value={buildArgs}
              onChange={(e) => setBuildArgs(e.target.value)}
              sx={{
                "& .MuiInputBase-root": {
                  typography: "s2",
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                },
              }}
            />
          </Box>
        </SectionCard>

        {/* ── versions — reproducibility, which HUD does not surface ── */}
        <SectionCard
          title="Versions"
          subtitle="Every run pins a version, so a result can always be traced to the world that produced it"
          action={
            <Button
              size="small"
              onClick={() => setChanging(changing ? null : ENV_CHANGES[0].id)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
            >
              New version
            </Button>
          }
        >
          {/*
            Changing the world is an event, not a save.

            Every proof in this environment is a claim about one version of it,
            so a change that reseeds the data or rewrites the checks has to
            invalidate those claims rather than quietly outdate them. Naming the
            kind of change is what decides which ones survive: rules are graded,
            so they leave every scenario stageable; seed, checks and tools do
            not.
          */}
          {changing && (
            <Box sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, mb: 1 }}>What changed in the world?</Typography>
              <Stack spacing={0.75} sx={{ mb: 1.5 }}>
                {ENV_CHANGES.map((c) => (
                  <Stack
                    key={c.id}
                    direction="row" alignItems="flex-start" spacing={1}
                    onClick={() => setChanging(c.id)}
                    sx={{
                      p: 1.25, borderRadius: 1, cursor: "pointer",
                      border: "1px solid", borderColor: changing === c.id ? "primary.main" : "divider",
                    }}
                  >
                    <Iconify
                      icon={changing === c.id ? "solar:record-circle-bold" : "solar:circle-linear"}
                      width={15}
                      sx={{ color: changing === c.id ? "primary.main" : "text.disabled", flexShrink: 0, mt: "1px" }}
                    />
                    <Box minWidth={0}>
                      <Stack direction="row" alignItems="center" spacing={0.75}>
                        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{c.label}</Typography>
                        <Typography sx={{ typography: "s3", fontWeight: 700, color: c.invalidates ? "#CA8A04" : "text.subtitle" }}>
                          {c.invalidates ? "invalidates proofs" : "proofs survive"}
                        </Typography>
                      </Stack>
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.blurb}</Typography>
                    </Box>
                  </Stack>
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <Button
                  size="small" variant="contained" color="primary"
                  onClick={() => {
                    const change = ENV_CHANGES.find((c) => c.id === changing);
                    const list = envState?.envVersions?.length
                      ? envState.envVersions
                      : [...environmentVersions(env, envState)].reverse();
                    patch({
                      envVersions: [
                        ...list,
                        nextEnvVersion(env, envState, { changed: [changing], note: change.label }),
                      ],
                    });
                    setChanging(null);
                  }}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  Create {nextEnvVersion(env, envState).label}
                </Button>
                <Button
                  size="small" onClick={() => setChanging(null)}
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
                  sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace", width: 44, flexShrink: 0 }}
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
                {/*
                  Two actions per historical row: **Switch** just pins
                  the active pointer to this version (non-destructive —
                  newest still exists), and **Restore** forks by minting
                  a new version with the same intent as this one, so the
                  history stays linear. Both are inline here; the header
                  pin also switches, this is the settings-side entry
                  point.
                */}
                {!v.current && (
                  <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
                    <Button
                      size="small"
                      onClick={() => patch({ activeEnvVersion: v.label })}
                      startIcon={<Iconify icon="solar:arrow-right-linear" width={13} />}
                      sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                    >
                      Switch
                    </Button>
                    {/*
                      Restore forks — mints v(N+1) with the same intent
                      as the older row and pins it active. Preflight
                      first because the fork can invalidate proofs on
                      every scenario currently proved against the newer
                      world, and doing that silently on click is exactly
                      the kind of thing versioning exists to make
                      visible.
                    */}
                    <Button
                      size="small"
                      onClick={() => setRestoring(v)}
                      startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
                      sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                    >
                      Restore
                    </Button>
                  </Stack>
                )}
              </Stack>
            ))}
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
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Removes the environment, its scenarios and every run against it. Cannot be undone.
              </Typography>
            </Box>
            <Button
              variant="outlined"
              size="small"
              color="error"
              sx={{ flexShrink: 0, typography: "s2", fontWeight: 600 }}
            >
              Delete environment
            </Button>
          </Stack>
        </SectionCard>
      </Stack>

      {/*
        Restore preflight. Restoring an older env version forks — mints
        v(N+1) with the older row's intent and pins it active. The old
        versions are still there (versioning never loses data), but
        every scenario currently proved against the newer world lapses
        against the new active version. The dialog spells that out
        instead of relying on the user to notice the RE-PROVE banner
        later.
      */}
      <RestorePreflight
        restoring={restoring}
        env={env}
        envState={envState}
        onCancel={() => setRestoring(null)}
        onConfirm={() => {
          const list = envState?.envVersions?.length
            ? envState.envVersions
            : [...environmentVersions(env, envState)].reverse();
          const forked = nextEnvVersion(env, envState, {
            changed: restoring.changed || [],
            note: `Restored from ${restoring.label} — ${restoring.note}`,
          });
          patch({
            envVersions: [...list, forked],
            activeEnvVersion: forked.label,
          });
          setRestoring(null);
        }}
      />
    </Box>
  );
}

SettingsPanel.propTypes = { envState: PropTypes.object, patch: PropTypes.func, env: PropTypes.object.isRequired };

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
 *   · Reset policy   — whether the sandbox rolls back between runs
 *                      (default: on).
 *   · Danger zone    — reset the current sandbox, rotate all
 *                      service credentials at once.
 */
function TwinConfigSection({ backing, envState, patch }) {
  const { enqueueSnackbar } = useSnackbar();
  const [seedPrompt, setSeedPrompt] = useState(backing.seedPrompt || "");
  const [showJson, setShowJson] = useState(false);
  const [resetBetween, setResetBetween] = useState(
    envState?.twinResetBetween !== false,
  );
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [rotating, setRotating] = useState(false);

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
    });
    enqueueSnackbar("Seed updated · the next run starts from this state", { variant: "success" });
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
        activity: Object.fromEntries(services.map((sId) => [sId, { requests: 0, failures: 0 }])),
      },
    });
    setResetDialogOpen(false);
    enqueueSnackbar("Sandbox reset · fresh seed installed", { variant: "success" });
  };

  const rotateCreds = () => {
    setRotating(true);
    /* Scripted delay — real rotation is a round-trip per service. */
    setTimeout(() => {
      setRotating(false);
      enqueueSnackbar(
        `Credentials rotated for ${services.length} service${services.length === 1 ? "" : "s"}`,
        { variant: "success" },
      );
    }, 900);
  };

  const toggleReset = (next) => {
    setResetBetween(next);
    patch({ twinResetBetween: next });
  };

  return (
    <SectionCard
      title="Clone backing"
      subtitle="How the sandbox is provisioned and seeded per run"
      action={
        <Chip
          size="small" label="Clone-backed"
          icon={<Iconify icon="solar:server-square-linear" width={11} sx={{ ml: "6px !important", color: `${TWIN_TINT} !important` }} />}
          sx={{
            height: 20, borderRadius: 0.75,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: TWIN_TINT,
            border: "1px solid", borderColor: alpha(TWIN_TINT, 0.35),
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700, letterSpacing: 0.4 },
          }}
        />
      }
    >
      <Stack spacing={2.75} sx={{ p: 2.5 }}>
        {/* services */}
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>
            Cloned services ({services.length})
          </Typography>
          <Stack spacing={0.75}>
            {services.map((sId) => {
              const t = twinById(sId);
              const endpoint = backing.endpoints?.[sId] || "—";
              return (
                <Stack key={sId} direction="row" alignItems="center" spacing={1.5}
                  sx={{
                    p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider",
                    bgcolor: "background.paper",
                  }}
                >
                  <Box sx={{
                    width: 24, height: 24, flexShrink: 0,
                    display: "grid", placeItems: "center",
                  }}>
                    <TwinLogo twin={t} width={18} />
                  </Box>
                  <Box flex={1} minWidth={0}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{t?.name || sId}</Typography>
                    <Typography noWrap sx={{
                      typography: "s3", color: "text.subtitle",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}>
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
                    <Iconify icon="solar:copy-linear" width={14} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Stack>
              );
            })}
          </Stack>
        </Box>

        {/* seed prompt */}
        <Box>
          <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ mb: 0.625 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>Seed prompt</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              — natural language description of the starting state
            </Typography>
          </Stack>
          <TextField
            fullWidth multiline minRows={3}
            value={seedPrompt}
            onChange={(e) => setSeedPrompt(e.target.value)}
            placeholder="Describe the state each run should start with…"
            sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
            <Button
              size="small" variant="contained" color="primary"
              disabled={!dirty}
              onClick={saveSeed}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Save & re-resolve
            </Button>
            <Button
              size="small"
              disabled={!dirty}
              onClick={() => setSeedPrompt(backing.seedPrompt || "")}
              sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
            >
              Discard
            </Button>
            <Box flex={1} />
            <Button
              size="small"
              onClick={() => setShowJson((v) => !v)}
              startIcon={<Iconify icon={showJson ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
              sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
            >
              {showJson ? "Hide resolved JSON" : "View resolved JSON"}
            </Button>
          </Stack>
          {showJson && (
            <Box sx={{
              mt: 1, p: 1.5, borderRadius: 1, border: "1px solid", borderColor: "divider",
              bgcolor: "background.neutral", maxHeight: 320, overflow: "auto",
            }}>
              <Typography component="pre" sx={{
                typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                color: "text.primary", whiteSpace: "pre", m: 0,
              }}>
                {backing.seed || "{}"}
              </Typography>
            </Box>
          )}
        </Box>

        {/* reset policy */}
        <Stack direction="row" alignItems="flex-start" spacing={2}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>Reset sandbox between runs</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Every scenario starts from a fresh copy of the seeded state. Turn off to let runs share sandbox state — useful when you&apos;re debugging a multi-run sequence, but scenarios become order-dependent.
            </Typography>
          </Box>
          <Switch checked={resetBetween} onChange={(e) => toggleReset(e.target.checked)} />
        </Stack>

        {/* actions */}
        <Stack
          direction={{ xs: "column", sm: "row" }} spacing={1}
          sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Button
            variant="outlined" size="small"
            disabled={rotating}
            onClick={rotateCreds}
            startIcon={
              rotating
                ? <Iconify icon="solar:refresh-circle-linear" width={13} sx={{ animation: "spin 1.2s linear infinite", "@keyframes spin": { to: { transform: "rotate(360deg)" } } }} />
                : <Iconify icon="solar:key-linear" width={13} />
            }
            sx={{
              typography: "s2", fontWeight: 700,
              color: "text.primary", borderColor: "divider",
            }}
          >
            {rotating ? "Rotating…" : "Rotate service credentials"}
          </Button>
          <Button
            variant="outlined" size="small"
            onClick={() => setResetDialogOpen(true)}
            startIcon={<Iconify icon="solar:refresh-circle-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 700,
              color: "#C2603F",
              borderColor: (t) => alpha("#C2603F", t.palette.mode === "dark" ? 0.5 : 0.4),
              "&:hover": {
                borderColor: "#C2603F",
                bgcolor: (t) => alpha("#C2603F", t.palette.mode === "dark" ? 0.1 : 0.06),
              },
            }}
          >
            Reset sandbox now
          </Button>
        </Stack>
      </Stack>

      <Dialog
        open={resetDialogOpen} onClose={() => setResetDialogOpen(false)}
        PaperProps={{ sx: { borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" } }}
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 700, p: 2.5, pb: 1 }}>
          Reset the clone sandbox?
        </DialogTitle>
        <DialogContent sx={{ p: 2.5, pt: 1 }} dividers>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            The current sandbox state is discarded and re-provisioned from the current seed prompt.
            In-flight runs against this env will fail. Past runs remain in history.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2, pt: 1.5 }}>
          <Button onClick={() => setResetDialogOpen(false)} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={resetSandbox}
            sx={{
              typography: "s2", fontWeight: 700,
              bgcolor: "#C2603F", color: "common.white",
              "&:hover": { bgcolor: "#A54E32" },
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
};

function RestorePreflight({ restoring, env, envState, onCancel, onConfirm }) {
  const open = !!restoring;
  const versions = environmentVersions(env, envState);
  const current = versions.find((v) => v.current) || versions[0];
  const changedKinds = (restoring?.changed || []).filter((c) => INVALIDATING[c]);

  /* Simulate what proofs would look like if we were on the older
     version — the fork carries the older version's `changed` kinds, so
     stale count against that world approximates the impact. */
  const projectedStale = restoring
    ? staleScenarios(envState?.scenarios || [], env, {
      ...envState,
      envVersions: (envState?.envVersions?.length
        ? envState.envVersions
        : [...versions].reverse()).concat([{
        ...restoring,
        label: "next",
        changed: restoring.changed || [],
      }]),
    })
    : [];

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
        Restore {restoring?.label}?
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Forks the environment: mints the next version with the same intent as{" "}
            <b>{restoring?.label}</b> and pins it active. {current?.label} stays in the history.
          </Typography>

          {changedKinds.length > 0 && (
            <Box sx={{ p: 1.5, borderRadius: 1, bgcolor: "background.neutral" }}>
              <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.5 }}>
                What changes
              </Typography>
              <Stack spacing={0.375}>
                {changedKinds.map((k) => (
                  <Typography key={k} sx={{ typography: "s3", color: "text.secondary" }}>
                    · {INVALIDATING[k]}
                  </Typography>
                ))}
              </Stack>
            </Box>
          )}

          {projectedStale.length > 0 && (
            <Stack
              direction="row" alignItems="flex-start" spacing={1}
              sx={{
                px: 1.5, py: 1.25, borderRadius: 1,
                bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.07),
                border: "1px solid",
                borderColor: (t) => alpha("#CA8A04", 0.35),
              }}
            >
              <Iconify icon="solar:danger-triangle-bold" width={14} sx={{ color: "#CA8A04", flexShrink: 0, mt: "2px" }} />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                <b style={{ color: "#CA8A04" }}>{projectedStale.length} of {envState?.scenarios?.length || 0} scenarios</b> would need re-proving against the restored world — their current proofs were made against a version the fork does not include.
              </Typography>
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained" color="primary"
          onClick={onConfirm}
          startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Restore
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RestorePreflight.propTypes = {
  restoring: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  onCancel: PropTypes.func,
  onConfirm: PropTypes.func,
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
