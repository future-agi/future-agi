import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Divider, Tab,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getSurface } from "../_mock/surfaces";
import { generatedPool } from "../_mock/scenarios";
import { seedScenariosForClone, resolveSeedPromptToJson } from "../_mock/twins";
import { MODALITY_FOR } from "../_mock/fidelity";
import { runtimeTypeFor } from "../_mock/builder";
import { useSimStore } from "../store";
import { SectionCard, CopyField } from "../components/primitives";
import TwinProvisioningModal from "./TwinProvisioningModal";

/**
 * Build a prebuilt template — the "where to build it" panel.
 *
 * A template is a world that already exists (seeded state, tools, rules,
 * scenarios and a baseline agent), so there's no agent to connect and nothing
 * to derive. The only decision is where to build it: here (cloud sandbox) or
 * locally (CLI scaffold). The template's own agent is seeded so the environment
 * is complete the moment it's built; the user swaps in their own agent version
 * afterwards, from the environment's Agents panel.
 *
 * Rendered both as the standalone Use-template page and inline as the detail
 * pane of the templates browser, so it takes a base `template` and mints its
 * own env instance rather than owning a route.
 */
export default function TemplateBuildPanel({ template, showName = false }) {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();

  /* Each selected template gets a distinct env instance. Minted (once) per
     template id, so re-renders reuse it but switching templates mints a fresh
     one. The env keeps `templateId` so downstream lookups fall back to the
     template library when they don't find the instance id. */
  const env = useMemo(() => {
    if (!template) return null;
    const instanceId = `${template.id}-${Math.random().toString(36).slice(2, 8)}`;
    return { ...template, id: instanceId, templateId: template.id };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template?.id]);

  const [mode, setMode] = useState("cloud");
  const [twinProvisioning, setTwinProvisioning] = useState(false);

  if (!env) return null;

  const surface = getSurface(env.surface);
  const scenarios = generatedPool(env);
  const evalCount = env.evalPreset?.length || 2;

  /* Seed the environment from the template — the full scenario suite, the
     template's evals, and the template's own baseline agent (v1) — then adopt
     it. Twin-backed templates spin up their sandbox first; everything else is
     live immediately and lands in the workspace. */
  const buildHere = () => {
    const now = new Date().toISOString();
    dispatch({ type: "adoptEnvironment", env, now });

    const modality = MODALITY_FOR[env.surface] || "chat";
    const t = runtimeTypeFor(modality, null, null);
    const seededScenarios = env.twinBacking
      ? seedScenariosForClone(env.twinBacking.services)
      : scenarios;
    const scenarioCount = (seededScenarios.length ? seededScenarios : scenarios).length;

    dispatch({
      type: "patchEnvState",
      envId: env.id,
      patch: {
        /* A first-time build starts at v1 — seed the version history
           explicitly so it doesn't fall back to the demo envs' synthesised
           v3/v2/v1 lineage (which reads as "built three times before"). */
        envVersions: [{
          id: `${env.id}-v1`,
          label: "v1",
          createdAt: now,
          note: "First build from the template.",
          scenarios: scenarioCount,
          changed: ["contract", "seed"],
        }],
        agent: {
          typeId: t?.id,
          values: {},
          via: "seed",
          seeded: true,
          name: "Template baseline",
          connectedAt: now,
        },
        agentVersions: [
          { id: "agent-v1", label: "v1", note: "Shipped with the template.", reach: "seed", createdAt: now },
        ],
        /* The env v1 was derived against agent v1 — so the "agent moved ahead"
           refresh banner only appears once the user adds a newer agent version. */
        envDerivedForAgent: "v1",
        scenarios: seededScenarios.length ? seededScenarios : scenarios,
        scenarioSource: env.twinBacking ? "twin_starter" : "templates",
        /* Start with nothing added — the template's preset evals surface as
           Suggested on the Evaluations tab, and the user adds the ones they
           want to score against (same as the build-from-agent flow). */
        evals: [],
      },
    });

    if (env.twinBacking) {
      setTwinProvisioning(true);
      return;
    }
    navigate(paths.dashboard.simulate.environmentDetail(env.id));
  };

  const finishTwin = () => {
    const now = new Date().toISOString();
    const twin = env.twinBacking;
    dispatch({
      type: "patchEnvState",
      envId: env.id,
      patch: {
        twinBacking: {
          services: twin.services,
          seedPrompt: twin.seedPrompt || "",
          seed: resolveSeedPromptToJson(twin.services, twin.seedPrompt || ""),
          endpoints: Object.fromEntries(twin.services.map((sId) => [
            sId, `https://${sId}.sandbox.futureagi.com/e/${env.id.slice(-6)}`,
          ])),
          activity: Object.fromEntries(twin.services.map((sId) => [sId, { requests: 0, failures: 0 }])),
          provisionedAt: now,
          status: "ready",
        },
      },
    });
    navigate(paths.dashboard.simulate.environmentDetail(env.id));
  };

  return (
    <Stack spacing={2}>
      {showName && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify icon={surface.icon} width={13} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" }}>
              {env.surface}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "m2", fontWeight: 700, mt: 0.5 }}>{env.name}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{env.tagline}</Typography>
        </Box>
      )}

      <SegmentedTabs value={mode} onChange={(_e, next) => setMode(next)} sx={{ alignSelf: "flex-start" }}>
        <Tab
          value="cloud"
          label={(
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Iconify icon="solar:cloud-linear" width={13} />
              <Box component="span">Build here</Box>
            </Stack>
          )}
        />
        <Tab
          value="local"
          label={(
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Iconify icon="solar:laptop-2-linear" width={13} />
              <Box component="span">Build locally</Box>
            </Stack>
          )}
        />
      </SegmentedTabs>

      {mode === "cloud" && (
        <>
          <SectionCard
            title="Build in the cloud"
            subtitle="Spins up in an isolated sandbox — ready to run in seconds."
          >
            <Stack sx={{ p: 2.5 }} spacing={1.75}>
              {[
                { icon: "solar:box-minimalistic-linear", text: "The seeded world, tools and hard rules come up exactly as designed." },
                { icon: "solar:user-speak-rounded-linear", text: "The template's baseline agent is wired in, so you can run it right away." },
                { icon: "solar:magic-stick-3-linear", text: "Add your own agent version afterwards from the environment's Agents tab." },
              ].map((row) => (
                <Stack key={row.text} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon={row.icon} width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{row.text}</Typography>
                </Stack>
              ))}
            </Stack>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
              <Button
                variant="contained" color="primary"
                onClick={buildHere}
                startIcon={<Iconify icon="solar:magic-stick-3-bold" width={16} />}
                sx={{ typography: "s2", fontWeight: 700, whiteSpace: "nowrap" }}
              >
                Build environment
              </Button>
            </Stack>
          </SectionCard>

          <SectionCard title="What this template gives you" subtitle="Already built — you are not deriving it">
            <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
              <Line label="World" value={`${(env.seed?.tables || []).reduce((a, t) => a + t.rows, 0).toLocaleString()} seeded rows`} />
              <Line label="Tools" value={`${env.tools?.length || 0} the world answers`} />
              <Line label="Hard rules" value={`${env.rules?.length || 0} graded on every run`} />
              <Line label="Scenarios" value={`${scenarios.length} ready to run`} />
              <Line label="Evals" value={`${evalCount} suggested`} />
              <Line label="Agent" value="Seeded baseline — swap in yours after" />
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
                Seeded data and test credentials in an isolated sandbox — your deployed systems are never called.
              </Typography>
            </Stack>
          </Box>
        </>
      )}

      {mode === "local" && <LocalScaffoldCard env={env} />}

      {env.twinBacking && (
        <TwinProvisioningModal
          open={twinProvisioning}
          services={env.twinBacking.services || []}
          onDone={finishTwin}
        />
      )}
    </Stack>
  );
}
TemplateBuildPanel.propTypes = { template: PropTypes.object, showName: PropTypes.bool };

/* ── local scaffold — CLI steps for this template ─────────────────────── */

function LocalScaffoldCard({ env }) {
  const slug = env.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const steps = [
    {
      n: 1,
      title: "Initialize",
      body: "Scaffold the environment, its scenario packs and the seeded agent into your repo.",
      cmd: `fai env init ${slug} --template ${env.id}`,
    },
    {
      n: 2,
      title: "Run a simulation",
      body: "Run the core pack locally against the seeded agent — nothing leaves your laptop.",
      cmd: `fai sim run --env ${slug} --pack core`,
    },
    {
      n: 3,
      title: "Deploy",
      body: "Publish when you're ready. Runs execute on our infrastructure and traces land back in the dashboard.",
      cmd: `fai env deploy ${slug}`,
    },
  ];
  return (
    <SectionCard
      title="Develop locally"
      subtitle="Scaffold this template into your own repo and iterate from your terminal."
    >
      <Stack sx={{ p: 2.5 }} spacing={0}>
        {steps.map((s, i) => (
          <Stack key={s.n} direction="row" spacing={1.75}>
            <Stack alignItems="center" sx={{ flexShrink: 0 }}>
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
                  border: "1px solid", borderColor: "divider",
                  typography: "s3", fontWeight: 700, color: "text.secondary",
                }}
              >
                {s.n}
              </Box>
              {i < steps.length - 1 && (
                <Box sx={{ flex: 1, width: "1px", bgcolor: "divider", my: 0.75, minHeight: 24 }} />
              )}
            </Stack>
            <Box sx={{ flex: 1, minWidth: 0, pb: i < steps.length - 1 ? 2.25 : 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>{s.title}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
                {s.body}
              </Typography>
              <CopyField value={s.cmd} wrap />
            </Box>
          </Stack>
        ))}
      </Stack>
      <Divider />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2.5, py: 1.75 }}>
        <Iconify icon="solar:book-linear" width={14} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          Not installed? <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>pip install futureagi</Box>
        </Typography>
        <Button size="small" sx={{ typography: "s3", fontWeight: 700, color: "text.secondary" }}>
          Docs
        </Button>
      </Stack>
    </SectionCard>
  );
}
LocalScaffoldCard.propTypes = { env: PropTypes.object };

function Line({ label, value }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 96, flexShrink: 0 }}>{label}</Typography>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{value}</Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.node };
