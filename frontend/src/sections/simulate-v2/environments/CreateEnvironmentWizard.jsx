import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, Grid, IconButton, Chip, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SURFACE_LIST, DOMAINS, getSurface } from "../_mock/surfaces";
import { DIFFICULTIES, DIFFICULTY_COLOR } from "../_mock/environments";
import { agentTypesForSurface } from "../_mock/agentTypes";
import { useSimStore } from "../store";
import { SectionCard, SurfaceIcon } from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";

const STEPS = [
  { id: "surface", label: "Channel", blurb: "How will your agent be reached?" },
  { id: "domain", label: "World", blurb: "What business does this environment simulate?" },
  { id: "data", label: "Data & tools", blurb: "What can the agent see and do inside it?" },
  { id: "rules", label: "Rules", blurb: "What must the agent never get wrong?" },
];

/**
 * Build an environment from scratch.
 *
 * Same four-part composite as the templates — channel, world, data + tools,
 * rules — so a hand-built environment is indistinguishable from a shipped one
 * everywhere downstream. The step order matters: the channel constrains what
 * the later steps can even offer.
 */
export default function CreateEnvironmentWizard() {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();

  const [stepIdx, setStepIdx] = useState(0);
  const [provisioning, setProvisioning] = useState(false);
  const [draft, setDraft] = useState({
    name: "",
    tagline: "",
    description: "",
    surface: null,
    domain: null,
    difficulty: "Intermediate",
    tables: [{ name: "", rows: "" }],
    tools: [{ name: "", desc: "" }],
    rules: [""],
  });

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const step = STEPS[stepIdx];
  const surface = draft.surface ? getSurface(draft.surface) : null;

  const canAdvance = useMemo(() => {
    switch (step.id) {
      case "surface": return !!draft.surface && draft.name.trim().length > 1;
      case "domain": return !!draft.domain;
      case "data": return draft.tables.some((t) => t.name.trim());
      case "rules": return draft.rules.some((r) => r.trim());
      default: return true;
    }
  }, [step.id, draft]);

  const finish = () => {
    setProvisioning(true);
  };

  const onProvisioned = () => {
    const id = `env-custom-${Date.now().toString(36)}`;
    const env = {
      id,
      name: draft.name.trim(),
      surface: draft.surface,
      domain: draft.domain,
      tagline: draft.tagline.trim() || "Custom environment",
      description: draft.description.trim() || "A custom environment you built.",
      official: false,
      custom: true,
      popularity: 0,
      agentType: agentTypesForSurface(draft.surface).recommended[0]?.id || "api_agent",
      difficulty: draft.difficulty,
      seed: {
        tables: draft.tables
          .filter((t) => t.name.trim())
          .map((t) => ({ name: t.name.trim(), rows: Number(t.rows) || 100 })),
      },
      tools: draft.tools.filter((t) => t.name.trim()).map((t) => ({ name: t.name.trim(), desc: t.desc.trim() })),
      rules: draft.rules.filter((r) => r.trim()),
      evalPreset: ["task_success", "policy_adherence"],
    };
    dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    navigate(paths.dashboard.simulate.environmentDetail(id));
  };

  if (provisioning) {
    return (
      <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }}>
        <Box sx={{ width: "100%", maxWidth: 520, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper" }}>
          <ProvisioningPanel
            icon={surface?.icon}
            accent={surface?.color}
            title={`Building ${draft.name}`}
            subtitle="Creating the seed database, registering tools and compiling your rules into graders."
            steps={[
              "Creating seed database",
              "Registering tool schemas",
              "Compiling rules into graders",
              "Taking a baseline snapshot",
            ]}
            onDone={onProvisioned}
          />
        </Box>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2, maxWidth: 940, mx: "auto" }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2.5 }}>
        <Button
          onClick={() => navigate(paths.dashboard.simulate.environments)}
          startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={16} />}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
        >
          Environments
        </Button>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Build an environment</Typography>
        </Box>
      </Stack>

      {/* progress rail */}
      <Stack direction="row" spacing={1} sx={{ mb: 3 }}>
        {STEPS.map((s, i) => (
          <Box key={s.id} sx={{ flex: 1 }}>
            <LinearProgress
              variant="determinate"
              value={i < stepIdx ? 100 : i === stepIdx ? 50 : 0}
              sx={{
                height: 3, borderRadius: 2, mb: 0.75,
                bgcolor: "background.neutral",
                "& .MuiLinearProgress-bar": { bgcolor: i <= stepIdx ? "primary.main" : "transparent" },
              }}
            />
            <Typography sx={{ typography: "s3", fontWeight: i === stepIdx ? 700 : 500, color: i <= stepIdx ? "text.primary" : "text.subtitle" }}>
              {s.label}
            </Typography>
          </Box>
        ))}
      </Stack>

      <SectionCard title={step.blurb}>
        <Box sx={{ p: 2.5 }}>
          {/* ── 1. channel ── */}
          {step.id === "surface" && (
            <Stack spacing={2.5}>
              <TextField
                fullWidth size="small" label="Environment name"
                value={draft.name}
                onChange={(e) => set({ name: e.target.value })}
                placeholder="e.g. Mortgage Pre-approval Line"
                sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
              />
              <TextField
                fullWidth size="small" label="One-line summary"
                value={draft.tagline}
                onChange={(e) => set({ tagline: e.target.value })}
                placeholder="e.g. Inbound calls from applicants checking eligibility"
                sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
              />
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Channel</Typography>
                <Grid container spacing={1.25}>
                  {SURFACE_LIST.map((s) => {
                    const on = draft.surface === s.id;
                    return (
                      <Grid item xs={6} sm={4} md={3} key={s.id}>
                        <Box
                          onClick={() => set({ surface: s.id })}
                          sx={{
                            p: 1.5, borderRadius: 1.25, cursor: "pointer", height: "100%",
                            border: "1px solid",
                            borderColor: on ? alpha(s.color, 0.55) : "divider",
                            bgcolor: (t) => on ? alpha(s.color, t.palette.mode === "dark" ? 0.14 : 0.06) : "transparent",
                            transition: "border-color .15s ease",
                            "&:hover": { borderColor: alpha(s.color, 0.4) },
                          }}
                        >
                          <Stack direction="row" alignItems="center" spacing={1}>
                            <Iconify icon={s.icon} width={17} sx={{ color: s.color, flexShrink: 0 }} />
                            <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{s.label}</Typography>
                          </Stack>
                          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
                            {s.blurb}
                          </Typography>
                        </Box>
                      </Grid>
                    );
                  })}
                </Grid>
              </Box>
            </Stack>
          )}

          {/* ── 2. world ── */}
          {step.id === "domain" && (
            <Stack spacing={2.5}>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Domain</Typography>
                <Stack direction="row" flexWrap="wrap" gap={1}>
                  {DOMAINS.map((d) => (
                    <Chip
                      key={d.id}
                      label={d.label}
                      icon={<Iconify icon={d.icon} width={14} />}
                      onClick={() => set({ domain: d.id })}
                      sx={{
                        borderRadius: 1, height: 32,
                        border: "1px solid",
                        borderColor: draft.domain === d.id ? "primary.main" : "divider",
                        color: draft.domain === d.id ? "primary.main" : "text.secondary",
                        bgcolor: (t) => draft.domain === d.id ? alpha(t.palette.primary.main, 0.08) : "transparent",
                        "& .MuiChip-label": { typography: "s2", fontWeight: 600 },
                        "& .MuiChip-icon": { color: "inherit" },
                      }}
                    />
                  ))}
                </Stack>
              </Box>
              <TextField
                fullWidth multiline minRows={3} size="small" label="Description"
                value={draft.description}
                onChange={(e) => set({ description: e.target.value })}
                placeholder="What happens in this environment, and who is on the other end?"
                sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
              />
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>Difficulty</Typography>
                <Stack direction="row" spacing={1}>
                  {DIFFICULTIES.map((d) => (
                    <Chip
                      key={d}
                      label={d}
                      onClick={() => set({ difficulty: d })}
                      sx={{
                        borderRadius: 1, height: 28,
                        border: "1px solid",
                        borderColor: draft.difficulty === d ? alpha(DIFFICULTY_COLOR[d], 0.5) : "divider",
                        color: draft.difficulty === d ? DIFFICULTY_COLOR[d] : "text.secondary",
                        bgcolor: () => draft.difficulty === d ? alpha(DIFFICULTY_COLOR[d], 0.08) : "transparent",
                        "& .MuiChip-label": { typography: "s3", fontWeight: 700 },
                      }}
                    />
                  ))}
                </Stack>
              </Box>
            </Stack>
          )}

          {/* ── 3. data & tools ── */}
          {step.id === "data" && (
            <Stack spacing={3}>
              <RepeatingRows
                title="Seed tables"
                hint="The mock database your agent's tools read from. We generate realistic rows."
                rows={draft.tables}
                onChange={(tables) => set({ tables })}
                fields={[
                  { key: "name", placeholder: "orders", flex: 1.4, mono: true },
                  { key: "rows", placeholder: "500", flex: 0.6, mono: true },
                ]}
                blank={{ name: "", rows: "" }}
                addLabel="Add table"
              />
              <RepeatingRows
                title="Tools"
                hint="Actions the agent can call inside this environment."
                rows={draft.tools}
                onChange={(tools) => set({ tools })}
                fields={[
                  { key: "name", placeholder: "lookup_order", flex: 1, mono: true },
                  { key: "desc", placeholder: "Fetch an order by ID or email", flex: 1.6 },
                ]}
                blank={{ name: "", desc: "" }}
                addLabel="Add tool"
              />
            </Stack>
          )}

          {/* ── 4. rules ── */}
          {step.id === "rules" && (
            <Stack spacing={2}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                Write these as plain sentences. We compile each one into a grader that runs
                against every task.
              </Typography>
              {draft.rules.map((r, i) => (
                <Stack key={i} direction="row" spacing={1} alignItems="center">
                  <Iconify icon="solar:shield-check-linear" width={17} sx={{ color: "primary.main", flexShrink: 0 }} />
                  <TextField
                    fullWidth size="small"
                    value={r}
                    placeholder="e.g. Never disclose account details before two identity factors are confirmed"
                    onChange={(e) => set({ rules: draft.rules.map((x, idx) => (idx === i ? e.target.value : x)) })}
                    sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
                  />
                  <IconButton
                    size="small"
                    disabled={draft.rules.length === 1}
                    onClick={() => set({ rules: draft.rules.filter((_, idx) => idx !== i) })}
                  >
                    <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Stack>
              ))}
              <Button
                size="small"
                startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
                onClick={() => set({ rules: [...draft.rules, ""] })}
                sx={{ alignSelf: "flex-start", typography: "s2", color: "text.secondary" }}
              >
                Add rule
              </Button>
            </Stack>
          )}
        </Box>

        <Stack
          direction="row" alignItems="center" spacing={1.5}
          sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
        >
          {stepIdx > 0 && (
            <Button
              onClick={() => setStepIdx((i) => i - 1)}
              sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
            >
              Back
            </Button>
          )}
          <Box flex={1} />
          {surface && (
            <Stack direction="row" alignItems="center" spacing={1}>
              <SurfaceIcon surface={draft.surface} size={24} radius={0.75} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {draft.name || "Untitled"} · {surface.label}
              </Typography>
            </Stack>
          )}
          <Button
            variant="contained"
            color="primary"
            disabled={!canAdvance}
            onClick={() => (stepIdx === STEPS.length - 1 ? finish() : setStepIdx((i) => i + 1))}
            endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            {stepIdx === STEPS.length - 1 ? "Create environment" : "Continue"}
          </Button>
        </Stack>
      </SectionCard>
    </Box>
  );
}

function RepeatingRows({ title, hint, rows, onChange, fields, blank, addLabel }) {
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{title}</Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>{hint}</Typography>
      <Stack spacing={0.875}>
        {rows.map((row, i) => (
          <Stack key={i} direction="row" spacing={0.875} alignItems="center">
            {fields.map((f) => (
              <TextField
                key={f.key}
                size="small"
                placeholder={f.placeholder}
                value={row[f.key]}
                onChange={(e) =>
                  onChange(rows.map((r, idx) => (idx === i ? { ...r, [f.key]: e.target.value } : r)))
                }
                sx={{
                  flex: f.flex,
                  "& .MuiInputBase-root": {
                    typography: "s2",
                    ...(f.mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
                  },
                }}
              />
            ))}
            <IconButton
              size="small"
              disabled={rows.length === 1}
              onClick={() => onChange(rows.filter((_, idx) => idx !== i))}
            >
              <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>
        ))}
      </Stack>
      <Button
        size="small"
        startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
        onClick={() => onChange([...rows, { ...blank }])}
        sx={{ mt: 1, typography: "s2", color: "text.secondary" }}
      >
        {addLabel}
      </Button>
    </Box>
  );
}

RepeatingRows.propTypes = {
  title: PropTypes.string,
  hint: PropTypes.string,
  rows: PropTypes.array,
  onChange: PropTypes.func,
  fields: PropTypes.array,
  blank: PropTypes.object,
  addLabel: PropTypes.string,
};
