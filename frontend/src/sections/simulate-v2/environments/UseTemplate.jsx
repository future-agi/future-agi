import PropTypes from "prop-types";
import { useMemo, useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Tooltip, TextField, MenuItem, Collapse, Switch,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { getRows } from "../_mock/scenarios";
import {
  seedScenariosForClone, resolveSeedPromptToJson,
} from "../_mock/twins";
import { checkCompatibility } from "../_mock/compatibility";
import { runtimeTypesFor, runtimeTypeFor } from "../_mock/builder";
import { MODALITY_FOR } from "../_mock/fidelity";
import { useSimStore } from "../store";
import { SectionCard, EmptyState, cardGrid } from "../components/primitives";
import { BootSequence } from "../components/loading";
import DynamicField from "../workspace/connect/DynamicField";
import TwinProvisioningModal from "./TwinProvisioningModal";
import FitCheckDialog from "./FitCheckDialog";
import TemplateReviewLayout from "./TemplateReviewLayout";

/**
 * Test your agent in a template.
 *
 * The third way in. Building *from* an agent derives the world from it, so the
 * contract matches by construction. A template is the other way round: the
 * world already exists and the agent has to fit it — which is why this flow's
 * middle step is a compatibility check rather than a derivation. A scenario
 * that ends in a tool the agent does not have still fails, but it fails for
 * the wrong reason, and a pass rate computed over those measures nothing.
 */
const STEPS = ["Connect your agent", "Check it fits", "Ready"];

export default function UseTemplate() {
  const { templateId } = useParams();
  const navigate = useNavigate();
  const { dispatch, state } = useSimStore();

  const env = useMemo(() => getEnvironment(templateId), [templateId]);
  const [step, setStep] = useState(0);
  const [runtimeTypeId, setRuntimeTypeId] = useState(null);
  const [values, setValues] = useState({});
  const [showMore, setShowMore] = useState(false);
  const [dropBlocked, setDropBlocked] = useState(true);
  const [twinProvisioning, setTwinProvisioning] = useState(false);
  /*
    Track which env ids we've already adopted so the step-2 useEffect
    doesn't loop. Must be declared before any early return per rules
    of hooks.
  */
  const adoptedRef = useRef(new Set());
  /*
    Seed the values map with each field's `defaultValue` (from the
    agent-type registry) whenever the resolved type changes. Users
    can still override; this just means selects render with their
    intended default (e.g. Authentication → "No auth") instead of
    an empty state that requires an extra click.
  */
  useEffect(() => {
    if (!env) return;
    const modality = MODALITY_FOR[env.surface] || "chat";
    const resolved = runtimeTypeFor(modality, null, runtimeTypeId);
    if (!resolved) return;
    const defaults = {};
    (resolved.fields || []).forEach((f) => {
      if (f.defaultValue !== undefined) defaults[f.key] = f.defaultValue;
    });
    if (Object.keys(defaults).length === 0) return;
    setValues((prev) => {
      const next = { ...defaults, ...prev };
      /* Only trigger re-render when something actually changed. */
      const changed = Object.keys(defaults).some((k) => prev[k] === undefined);
      return changed ? next : prev;
    });
  }, [env?.id, runtimeTypeId]);
  /*
    Adopt the env on step 2 so DerivedPanels has a real envState.
    Runs once per env id — the ref-guard prevents the infinite loop
    that including state.myEnvironments in deps would cause.
  */
  useEffect(() => {
    if (step !== 2) return;
    if (!env) return;
    if (adoptedRef.current.has(env.id)) return;
    adoptedRef.current.add(env.id);
    const now = new Date().toISOString();
    dispatch({ type: "adoptEnvironment", env, now });
    const modality = MODALITY_FOR[env.surface] || "chat";
    const t = runtimeTypeFor(modality, null, runtimeTypeId);
    const fit = checkCompatibility(env);
    const templateScenarios = [
      ...getRows(`${env.id}::core`, env),
      ...getRows(`${env.id}::rules`, env),
    ];
    const blockedIds = new Set((fit?.blocked || []).map((b) => b.id));
    const keptScenarios = dropBlocked
      ? templateScenarios.filter((s) => !blockedIds.has(s.id))
      : templateScenarios;
    const seededScenarios = env.twinBacking
      ? seedScenariosForClone(env.twinBacking.services)
      : keptScenarios;
    dispatch({
      type: "patchEnvState",
      envId: env.id,
      patch: {
        agent: { typeId: t?.id, values, via: "endpoint", connectedAt: now },
        scenarios: seededScenarios.length ? seededScenarios : keptScenarios,
        scenarioSource: env.twinBacking ? "twin_starter" : "templates",
        evals: [],
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, env?.id]);
  /*
    Fit check is a popup now — it runs the scripted probe, then on
    completion commits the env and navigates the user straight to the
    workspace. No inline "does it fit" step in the page body anymore.
  */
  const [fitCheckOpen, setFitCheckOpen] = useState(false);

  if (!env) {
    return (
      <Box sx={{ p: 3 }}>
        <EmptyState icon="solar:danger-triangle-linear" title="Template not found" body="It may have been renamed." />
      </Box>
    );
  }

  const surface = getSurface(env.surface);
  const modality = MODALITY_FOR[env.surface] || "chat";
  const choices = runtimeTypesFor(modality);
  const type = runtimeTypeFor(modality, null, runtimeTypeId);
  const required = (type?.fields || []).filter((f) => f.required);
  const optional = (type?.fields || []).filter((f) => !f.required);
  const missing = required.filter((f) => !values[f.key]);
  const fit = checkCompatibility(env);

  /* The template's own packs — the suite this world ships with. */
  const scenarios = [
    ...getRows(`${env.id}::core`, env),
    ...getRows(`${env.id}::rules`, env),
  ];
  const blockedIds = new Set((fit?.blocked || []).map((b) => b.id));
  const kept = dropBlocked ? scenarios.filter((s) => !blockedIds.has(s.id)) : scenarios;

  /*
    Twin-backed templates run the four-phase provisioning modal
    before committing, so setup here feels the same as the
    "From a service twin" flow. Non-twin templates commit
    immediately — nothing to spin up on our infrastructure.
  */
  const finish = () => {
    if (env.twinBacking) {
      setTwinProvisioning(true);
      return;
    }
    commitTemplate();
  };

  /*
    The env was already adopted and seeded in the step 2 useEffect
    below — this just materialises the twin sandbox (endpoints, seed
    JSON, activity counters) for twin templates and navigates to the
    workspace. Non-twin templates are already fully live; just navigate.
  */
  const commitTemplate = () => {
    const now = new Date().toISOString();
    const twinTemplate = env.twinBacking;
    if (twinTemplate) {
      dispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: {
          twinBacking: {
            services: twinTemplate.services,
            seedPrompt: twinTemplate.seedPrompt || "",
            seed: resolveSeedPromptToJson(twinTemplate.services, twinTemplate.seedPrompt || ""),
            endpoints: Object.fromEntries(twinTemplate.services.map((sId) => [
              sId, `https://${sId}.sandbox.futureagi.com/e/${env.id.slice(-6)}`,
            ])),
            activity: Object.fromEntries(twinTemplate.services.map((sId) => [sId, { requests: 0, failures: 0 }])),
            provisionedAt: now,
            status: "ready",
          },
        },
      });
    }
    navigate(paths.dashboard.simulate.environmentDetail(env.id));
  };

  /*
    Post-fit-check: render the review layout instead of the connect
    layout. The user tweaks the template on the left-hand chat and
    previews it on the right; hitting Finish there calls `finish()`
    which either commits directly (non-twin) or opens the twin
    provisioning modal (twin templates).
  */
  if (step === 2) {
    /*
      Wait for the adopt effect to land before mounting the review
      layout — DerivedPanels + its child panels expect the env to be
      present in the store, and rendering a frame early crashed the
      deeper eval / scenario panels.
    */
    const adopted = state.myEnvironments?.some((e) => e.id === env.id);
    if (!adopted) {
      return (
        <Box sx={{ p: 4, height: "100%", display: "grid", placeItems: "center" }}>
          <Iconify icon="solar:refresh-circle-linear" width={22}
            sx={{ color: "text.subtitle", animation: "spin 1.2s linear infinite", "@keyframes spin": { to: { transform: "rotate(360deg)" } } }} />
        </Box>
      );
    }
    return (
      <>
        <TemplateReviewLayout
          env={env}
          isTwin={!!env.twinBacking}
          onBack={() => setStep(0)}
          onFinish={finish}
        />
        {env.twinBacking && (
          <TwinProvisioningModal
            open={twinProvisioning}
            services={env.twinBacking.services || []}
            onDone={commitTemplate}
          />
        )}
      </>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── header ── */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Tooltip arrow title="All environments">
          <Button
            onClick={() => navigate(paths.dashboard.simulate.environments)}
            sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle", flexShrink: 0 }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </Button>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>{env.name}</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Test your agent in this template — the world already exists
          </Typography>
        </Box>

        <Stack direction="row" alignItems="flex-start" sx={{ width: 420, display: { xs: "none", lg: "flex" } }}>
          {STEPS.map((label, i) => (
            <Stack key={label} direction="row" alignItems="flex-start" sx={{ flex: i === STEPS.length - 1 ? "0 0 auto" : 1 }}>
              <Box sx={{ width: 116, textAlign: "center", flexShrink: 0 }}>
                <Iconify
                  icon={i < step ? "solar:check-circle-bold" : "solar:circle-linear"}
                  width={15}
                  sx={{ color: i < step ? "#16A34A" : i === step ? "primary.main" : "text.disabled", display: "block", mx: "auto" }}
                />
                <Typography sx={{ typography: "s3", fontWeight: 700, color: i <= step ? "text.primary" : "text.subtitle", mt: 0.25 }}>
                  {label}
                </Typography>
              </Box>
              {i < STEPS.length - 1 && (
                <Box sx={{ flex: 1, height: "1px", mt: "7px", bgcolor: i < step ? "#16A34A" : "divider" }} />
              )}
            </Stack>
          ))}
        </Stack>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1.55fr) minmax(300px, 1fr)" } }}>
          <Stack spacing={2}>
            {/* ── 1 · connect ── */}
            {step === 0 && (
              <SectionCard
                title="Connect your agent"
                subtitle={`${surface.blurb} We handle the ${surface.transports.join(", ")} side.`}
              >
                <Stack spacing={2.25} sx={{ p: 2.5 }}>
                  {choices.length > 1 && (
                    <TextField
                      select size="small" label="Connect via" fullWidth
                      value={type?.id || ""}
                      onChange={(e) => { setRuntimeTypeId(e.target.value); setValues({}); }}
                    >
                      {choices.map((c) => (
                        <MenuItem key={c.id} value={c.id} sx={{ display: "block" }}>
                          <Typography sx={{ typography: "s2", fontWeight: 600 }}>{c.label}</Typography>
                          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.blurb}</Typography>
                        </MenuItem>
                      ))}
                    </TextField>
                  )}

                  {required.map((f) => (
                    <DynamicField
                      key={f.key} field={f} value={values[f.key]} values={values}
                      onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                    />
                  ))}

                  {optional.length > 0 && (
                    <Box>
                      <Button
                        size="small"
                        onClick={() => setShowMore((o) => !o)}
                        startIcon={<Iconify icon={showMore ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={14} />}
                        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", px: 0.5 }}
                      >
                        {showMore ? "Hide" : `${optional.length} more, usually detected`}
                      </Button>
                      <Collapse in={showMore} unmountOnExit>
                        <Stack spacing={2.25} sx={{ mt: 2 }}>
                          {optional.map((f) => (
                            <DynamicField
                              key={f.key} field={f} value={values[f.key]} values={values}
                              onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                            />
                          ))}
                        </Stack>
                      </Collapse>
                    </Box>
                  )}
                </Stack>

                <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
                  <Button
                    variant="contained" color="primary"
                    disabled={missing.length > 0}
                    onClick={() => setFitCheckOpen(true)}
                    endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    Check it fits
                  </Button>
                  {missing.length > 0 && (
                    <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                      {missing.length} required {missing.length === 1 ? "field" : "fields"} left
                    </Typography>
                  )}
                </Stack>
              </SectionCard>
            )}

          </Stack>

          {/* ── what you are about to get ── */}
          <Stack spacing={2}>
            <SectionCard title="What this template gives you" subtitle="Already built — you are not deriving it">
              <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
                <Line label="World" value={`${(env.seed?.tables || []).reduce((a, t) => a + t.rows, 0).toLocaleString()} seeded rows`} />
                <Line label="Tools" value={`${env.tools?.length || 0} the world answers`} />
                <Line label="Hard rules" value={`${env.rules?.length || 0} graded on every run`} />
                <Line label="Scenarios" value={`${kept.length} ready to run`} />
                <Line label="Evals" value={`${(env.evalPreset || []).length} applied`} />
              </Stack>
            </SectionCard>

            <Box
              sx={{
                p: 2, borderRadius: 1.25, border: "1px solid",
                borderColor: alpha("#16A34A", 0.3),
                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.08 : 0.04),
              }}
            >
              <Stack direction="row" spacing={1.25} alignItems="flex-start">
                <Iconify icon="solar:shield-keyhole-linear" width={16} sx={{ color: "#16A34A", flexShrink: 0, mt: "1px" }} />
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>Nothing touches production.</Box>{" "}
                  Seeded data and test credentials in an isolated sandbox — your deployed agent is never called.
                </Typography>
              </Stack>
            </Box>

            <Typography sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}>
              Prefer to work locally? The same template scaffolds into your repo from the CLI —
              see Develop locally on the template.
            </Typography>
          </Stack>
        </Box>
      </Box>

      <FitCheckDialog
        open={fitCheckOpen}
        probeSteps={fit.probe}
        onDone={() => {
          setFitCheckOpen(false);
          /* Advance to the review layout — user tweaks the template
             on the left-side chat, previews it on the right, then
             clicks Finish to commit. */
          setStep(2);
        }}
      />

      {env.twinBacking && (
        <TwinProvisioningModal
          open={twinProvisioning}
          services={env.twinBacking.services || []}
          onDone={commitTemplate}
        />
      )}
    </Box>
  );
}

function Tally({ n, label, tone }) {
  return (
    <Box sx={{ p: 1.75, borderRadius: 1.25, border: "1px solid", borderColor: "divider" }}>
      <Typography sx={{ typography: "m1", fontWeight: 700, color: tone }}>{n}</Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
    </Box>
  );
}
Tally.propTypes = { n: PropTypes.number, label: PropTypes.string, tone: PropTypes.string };

function Line({ label, value }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 96, flexShrink: 0 }}>{label}</Typography>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{value}</Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.node };
