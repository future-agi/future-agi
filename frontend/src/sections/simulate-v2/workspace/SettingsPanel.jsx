import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, IconButton, Switch, Slider, Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, CopyField } from "../components/primitives";
import {
  DEFAULT_ENV_VARS, DEFAULT_BUILD_ARGS, DEFAULT_RUNTIME,
  ISOLATION_OPTIONS, ENV_VERSIONS,
} from "../_mock/envConfig";

/**
 * Environment settings.
 *
 * Run defaults sit here rather than in the SDK on purpose: concurrency,
 * timeouts and isolation decide what a run costs and whether its results mean
 * anything, and burying them in code means nobody checks them before pressing
 * Run. The pre-flight quotes its estimate from these numbers.
 */
export default function SettingsPanel({ env }) {
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
                          icon={on ? "solar:check-circle-bold" : "solar:record-circle-linear"}
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
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {ENV_VERSIONS.map((v) => (
              <Stack key={v.version} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
                <Typography
                  sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace", width: 44, flexShrink: 0 }}
                >
                  {v.version}
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
                    {new Date(v.createdAt).toLocaleDateString()} · {v.runs} runs
                  </Typography>
                </Box>
                {!v.current && (
                  <Button size="small" sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
                    Restore
                  </Button>
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
    </Box>
  );
}

SettingsPanel.propTypes = { env: PropTypes.object.isRequired };

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
