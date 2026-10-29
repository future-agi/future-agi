import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  CircularProgress,
  Divider,
  MenuItem,
  Select,
} from "@mui/material";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { useConnectedAgents } from "../store";
import { uniqueEnvName } from "../_mock/envNames";
import { versionNameOf } from "../_mock/codeRefs";
import { SectionCard } from "../components/primitives";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { HintLine } from "../agents/connect/replicationBits";
import { WhereOption } from "../agents/connect/connectBits";
import LocalSteps from "./LocalSteps";
import WorldAgentPicker from "./AgentChoice";
import {
  buildableAgentsOf,
  sampleAgentOf,
  templateWorldSource,
  useBuildForAgent,
  useTryWorld,
  worldChannelOf,
  worldPacksOf,
} from "./worldSource";
import { generatedPool } from "../_mock/scenarios";
import { estimateRunPlan, rangeLabel } from "../_mock/pricing";
import { resolveEval } from "../_mock/evals";
import { replicationOf } from "../_mock/replication";

/**
 * A prebuilt world: try it now, or use it with your own agent.
 *
 * Try it now — the world exactly as designed, with its sample agent and its
 * own proven scenarios, ready to run. Nothing to connect, nothing to build.
 * The sample agent is in Agents, tagged Sample, so the environment still
 * belongs to an agent; connecting your agent later takes its place.
 *
 * Use it with your agent — the world as the world source of one agent's
 * environment:
 *
 * Every environment belongs to exactly one agent in Agents (RSI PRD), so a
 * prebuilt world is never built on its own or with a stand-in: the user picks
 * one of their agents (or connects one here), then the builder builds that
 * agent's environment starting from this world's tables, tools and rules —
 * reading the agent, checking its tools against the world, proving the
 * scenarios, then running the suite once.
 *
 * Where it runs is the person's choice. In Future AGI (our hosted sandbox,
 * the default): the same Build environment dialog the agent page uses (name,
 * version, 10–1,000 scenarios) opens first. On their own machine: no dialog —
 * the CLI steps show with the defaults (production version, 10 scenarios),
 * the CLI builds the world there and streams results back over a secure
 * tunnel, and Open the build follows once the machine checks in, so the
 * environment still shows up in this workspace.
 *
 * Rendered in the world drawer, so it takes a base `template`.
 */
export default function TemplateBuildPanel({
  template,
  showName = false,
  initialMode = "try",
  entry = null,
  blocked = null,
}) {
  const navigate = useNavigate();
  /* Starter pack by default; a named pack or the full world on request. */
  const [pack, setPack] = useState("starter");
  /* "try" — the world with its sample agent; "agent" — with one of yours. */
  const [mode, setMode] = useState(initialMode);
  const tryWorld = useTryWorld();
  const { agents: all, state: store } = useConnectedAgents();
  const agents = useMemo(() => buildableAgentsOf(all), [all]);
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const [dialogOpen, setDialogOpen] = useState(false);
  const build = useBuildForAgent();
  /* "cloud" — our hosted sandbox; "local" — the person's own machine. */
  const [where, setWhere] = useState("cloud");
  /* On their machine there's nothing to build here: the CLI steps show as
     soon as the agent is picked, we wait for the machine to check in, and
     the build opens once it has. */
  const [ranUp, setRanUp] = useState(false);
  const [checkedIn, setCheckedIn] = useState(false);
  useEffect(() => {
    setRanUp(false);
    setCheckedIn(false);
  }, [agentId, where]);
  useEffect(() => {
    if (!ranUp) return undefined;
    /* Demo: the machine checks in a moment after its last command is copied. */
    const t = setTimeout(() => setCheckedIn(true), 4000);
    return () => clearTimeout(t);
  }, [ranUp]);
  const startBuild = (choice, runsOn) =>
    build(agent, choice, {
      ...templateWorldSource(template),
      ...(runsOn === "local" && { runsOn: "local" }),
    });

  if (!template) return null;
  const env = template;
  const surface = getSurface(env.surface);
  const rows = (env.seed?.tables || []).reduce((a, t) => a + t.rows, 0);
  const sample = sampleAgentOf(template);
  const packs = worldPacksOf(template);
  const picked = packs.find((p) => p.id === pack) || packs[0];
  const scenarioCount = picked.count;
  const release = entry?.release?.version || null;
  /* An unedited sample copy of this release already exists: open it again
     rather than making another — Make another copy is still offered. */
  const existing = (store.myEnvironments || []).find((e) => {
    const es = store.byEnv?.[e.id];
    return (
      e.templateId === template.id &&
      (e.release || null) === release &&
      es?.agent?.sample &&
      (es.envVersions || []).length <= 1 &&
      !es.pendingChanges?.length
    );
  });
  const tryIt = () => tryWorld(template, { pack, release });
  const evalCount = (template.evalPreset || []).length;
  /* What a first run of it costs — the run plan's estimate, at 3 attempts. */
  const tryEstimate = estimateRunPlan({
    env: { surface: template.surface },
    scenarios: scenarioCount,
    attempts: 3,
    evals: (template.evalPreset || []).map(resolveEval).filter(Boolean),
  });

  return (
    <Stack spacing={2}>
      {showName && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon={surface.icon}
              width={14}
              sx={{ color: "text.subtitle" }}
            />
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontWeight: 600,
                letterSpacing: 0.5,
                textTransform: "none",
              }}
            >
              {env.surface}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "m2", fontWeight: 600, mt: 0.5 }}>
            {env.name}
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {env.tagline}
          </Typography>
        </Box>
      )}

      <Box
        sx={{
          display: "grid",
          gap: 1,
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
        }}
      >
        <WhereOption
          icon="solar:play-circle-linear"
          label="Try it now"
          on={mode === "try"}
          onClick={() => setMode("try")}
          short="With the world's sample agent · ready in seconds"
          hint="The world exactly as designed — its sample agent, scenarios and evaluations. Nothing to connect or build."
        />
        <WhereOption
          icon="solar:link-circle-linear"
          label="Use it with your agent"
          on={mode === "agent"}
          onClick={() => setMode("agent")}
          short="Build this world for an agent you've connected"
          hint="Your agent is read and its tools are checked against this world. It takes the world's own proven scenarios, unchanged."
        />
      </Box>

      {mode === "try" ? (
        <SectionCard
          title="Try it now"
          subtitle="The world exactly as designed, tested with its sample agent."
        >
          <Stack sx={{ p: 2.5 }} spacing={1.25}>
            {[
              {
                icon: "solar:user-speak-rounded-linear",
                text: `${sample.name} — the agent this world was designed and proven with.`,
                hint: "It's in Agents, tagged Sample. Connect your own agent from the environment's Overview when you're ready — it takes the sample agent's place.",
              },
              {
                icon: "solar:checklist-minimalistic-linear",
                text: `${picked.name} · ${scenarioCount} proved scenarios, and ${evalCount} evaluation${evalCount === 1 ? "" : "s"}.`,
              },
              {
                icon: "solar:play-circle-linear",
                text: "Ready to run in seconds — nothing to connect, nothing to build.",
              },
              {
                icon: "solar:wallet-money-linear",
                text: `Opening it is free. A run of ${scenarioCount} × 3 attempts ≈ ${rangeLabel(tryEstimate.platform.low, tryEstimate.platform.high, tryEstimate.platform.unknown)}.`,
                hint: "Confirmed in the run plan before anything starts, with its limit. The sample agent's own model calls aren't charged to you. A free run shows only if your account has one.",
              },
            ].map((row) => (
              <Stack
                key={row.text}
                direction="row"
                spacing={1.25}
                alignItems="flex-start"
              >
                <Iconify
                  icon={row.icon}
                  width={16}
                  sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
                />
                <Typography
                  component="div"
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {row.hint ? (
                    <HintLine hint={row.hint}>{row.text}</HintLine>
                  ) : (
                    row.text
                  )}
                </Typography>
              </Stack>
            ))}
          </Stack>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {/* Which pack it runs — the starter pack unless picked. */}
            <Select
              size="small"
              value={pack}
              onChange={(e) => setPack(e.target.value)}
              inputProps={{ "aria-label": "Scenario pack" }}
              sx={{ typography: "s2", minWidth: 180 }}
            >
              {packs.map((p) => (
                <MenuItem key={p.id} value={p.id} sx={{ typography: "s2" }}>
                  {`${p.name} · ${p.count}`}
                </MenuItem>
              ))}
            </Select>
            <Box flex={1} />
            {existing && !blocked && (
              <Button
                size="small"
                color="inherit"
                onClick={tryIt}
                sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
              >
                Make another copy
              </Button>
            )}
            <Button
              variant="contained"
              color="primary"
              disabled={!!blocked}
              onClick={() =>
                existing
                  ? navigate(
                      paths.dashboard.simulate.environmentDetail(existing.id),
                    )
                  : tryIt()
              }
              startIcon={<Iconify icon="solar:play-bold" width={16} />}
              sx={{ whiteSpace: "nowrap" }}
            >
              {existing ? "Run again" : "Try it now"}
            </Button>
          </Stack>
          {existing && !blocked && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                px: 2.5,
                pb: 1.75,
                mt: -1,
              }}
            >
              {`You already have an unedited copy of this release — ${existing.name}. Run again opens it.`}
            </Typography>
          )}
        </SectionCard>
      ) : (
        <SectionCard
          title="Build an agent's environment from this world"
          subtitle="The world is the starting point; the agent is what's tested."
        >
          <Stack sx={{ p: 2.5 }} spacing={2}>
            <WorldAgentPicker
              value={agentId}
              onChange={setAgentId}
              surface={worldChannelOf(template)}
            />
            {agent && replicationOf(agent).proofLabel === "Live" && (
              <Stack
                direction="row"
                spacing={1.25}
                alignItems="flex-start"
                sx={{
                  px: 1.5,
                  py: 1.25,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                }}
              >
                <Iconify
                  icon="solar:info-circle-linear"
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0, mt: "1px" }}
                />
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {`${agent.name} is tested live and calls its own tools; this world only grades it. Test a sealed copy for simulated tools.`}
                </Typography>
              </Stack>
            )}
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                Where it runs
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gap: 1,
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                }}
              >
                <WhereOption
                  icon="solar:cloud-linear"
                  label="In Future AGI"
                  on={where === "cloud"}
                  onClick={() => setWhere("cloud")}
                  short="Our hosted sandbox · ready to run in seconds"
                  hint="Built in an isolated sandbox from this world's tables, tools and rules. Runs are metered by the minute."
                />
                <WhereOption
                  icon="solar:laptop-2-linear"
                  label="On your machine"
                  on={where === "local"}
                  onClick={() => setWhere("local")}
                  short="Through our CLI · the world stays on your laptop"
                  hint="Builds the world on your machine; runs still show up here. Good for regulated or offline code."
                />
              </Box>
            </Box>
            {where === "cloud" && (
              <Stack spacing={1.25}>
                {[
                  {
                    icon: "solar:box-minimalistic-linear",
                    text: "Seeded with this world's tables, tools and hard rules.",
                  },
                  {
                    icon: "solar:user-speak-rounded-linear",
                    text: "Your agent is read and its tools are checked against the world.",
                    hint: "A tool the world doesn't answer is named before anything runs.",
                  },
                  {
                    icon: "solar:play-circle-linear",
                    text: `The world's ${scenarioCount} proven scenarios come over unchanged — the same tests the sample agent runs — then you run them once to see where your agent stands.`,
                  },
                ].map((row) => (
                  <Stack
                    key={row.text}
                    direction="row"
                    spacing={1.25}
                    alignItems="flex-start"
                  >
                    <Iconify
                      icon={row.icon}
                      width={16}
                      sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
                    />
                    <Typography
                      component="div"
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      {row.hint ? (
                        <HintLine hint={row.hint}>{row.text}</HintLine>
                      ) : (
                        row.text
                      )}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}
          </Stack>
          {where === "local" &&
            (agent ? (
              <LocalRun
                template={template}
                agent={agent}
                choice={localChoiceOf(template, agent, store.myEnvironments)}
                ranUp={ranUp}
                checkedIn={checkedIn}
                onRanUp={() => setRanUp(true)}
                onOpen={() =>
                  startBuild(
                    localChoiceOf(template, agent, store.myEnvironments),
                    "local",
                  )
                }
              />
            ) : (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  px: 2.5,
                  py: 2,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                Pick the agent this environment is for — the commands to run on
                your machine show here.
              </Typography>
            ))}
          {where === "cloud" && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2.5,
                py: 2,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Button
                variant="contained"
                color="primary"
                disabled={!agent || !!blocked}
                onClick={() => setDialogOpen(true)}
                startIcon={
                  <Iconify icon="solar:magic-stick-3-bold" width={16} />
                }
                sx={{ whiteSpace: "nowrap" }}
              >
                Build environment
              </Button>
              {!agent && (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  Pick the agent this environment is for
                </Typography>
              )}
            </Stack>
          )}
        </SectionCard>
      )}

      <SectionCard title="What this world gives you">
        <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
          <Line label="World" value={`${rows.toLocaleString()} seeded rows`} />
          <Line
            label="Tools"
            value={`${env.tools?.length || 0} the world answers`}
          />
          <Line
            label="Rules"
            value={`${env.rules?.length || 0} graded on every run`}
          />
          {mode === "try" ? (
            <>
              <Line label="Scenarios" value={`${scenarioCount} proven`} />
              <Line label="Agent" value={sample.name} />
            </>
          ) : (
            <>
              <Line
                label="Agent"
                value={agent ? agent.name : "Not picked yet"}
                muted={!agent}
              />
              <Line
                label="Runs"
                value={where === "local" ? "On your machine" : "In Future AGI"}
              />
            </>
          )}
        </Stack>
      </SectionCard>

      <Box
        sx={{
          p: 2,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "status.success.border",
          bgcolor: "status.success.bg",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify
            icon="solar:shield-keyhole-linear"
            width={16}
            sx={{ color: "status.success.fg", flexShrink: 0, mt: "1px" }}
          />
          <Typography
            component="div"
            sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
          >
            <HintLine hint="The world's tools answer from seeded data in a sandbox.">
              The world never touches production.
            </HintLine>
          </Typography>
        </Stack>
      </Box>

      {agent && (
        <BuildEnvironmentDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          agent={agent}
          defaultVersion={agent.production || agent.latest}
          onBuild={(choice) => {
            setDialogOpen(false);
            startBuild(choice, "cloud");
          }}
        />
      )}
    </Stack>
  );
}
TemplateBuildPanel.propTypes = {
  template: PropTypes.object,
  showName: PropTypes.bool,
  initialMode: PropTypes.oneOf(["try", "agent"]),
  /* The catalogue task (V01…M10): release and readiness. */
  entry: PropTypes.object,
  /* Why it can't be adopted — Try it now and Build are off. */
  blocked: PropTypes.string,
};

/* What the commands build on the machine, and what Open the build builds: the
   agent's production version and 10 scenarios. The prototype can't read an
   edited command, so these are the defaults both use. The name is the one the
   build will keep (a taken name gets the next number), so the CLI slug matches. */
function localChoiceOf(template, agent, envs = []) {
  return {
    name: uniqueEnvName(`${agent.name} · ${template.name}`, envs),
    version: agent.production || agent.latest,
    count: 10,
  };
}

/* On their machine: the CLI steps for this world and agent, then the wait
   for the machine to check in, then the build — as it is in Future AGI. */
function LocalRun({
  template,
  agent,
  choice,
  ranUp,
  checkedIn,
  onRanUp,
  onOpen,
}) {
  const slug = choice.name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  /* A code agent's version is a commit — named and pinned as one. */
  const shown = versionNameOf(agent, choice.version);
  const agentRef = `"${agent.name}@${shown.ref ? shown.ref.sha : choice.version}"`;
  const steps = [
    {
      title: "Install the CLI",
      body: "Once per machine.",
      cmd: "pip install agent-learning-kit && agent-learn auth login",
    },
    {
      title: "Pull this world for your agent",
      body: `This world's tables, tools and rules, and ${agent.name} at ${shown.main}.`,
      hint: `The world's own scenarios, for ${shown.main}. For another version or size, build it In Future AGI.`,
      cmd: `agent-learn env adopt ${template.id} --agent ${agentRef} --name ${slug}\nagent-learn env pull ${slug}`,
    },
    {
      title: "Run it on your machine",
      body: "Runs your agent and this world locally; results stay on your machine until you export them.",
      hint: "--mode hybrid keeps your agent local and runs the world here instead.",
      cmd: `agent-learn run --env ${slug} --mode local --attempts 3 --budget 100 --budget-unit credits`,
      onCopy: onRanUp,
    },
  ];
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Box sx={{ px: 2.5, pt: 2, pb: 2.5 }}>
        <LocalSteps steps={steps} />
      </Box>
      <Divider />
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 2.5, py: 1.75 }}
      >
        {checkedIn ? (
          <Iconify
            icon="solar:check-circle-bold"
            width={16}
            sx={{ color: "status.success.fg", flexShrink: 0 }}
          />
        ) : ranUp ? (
          <CircularProgress
            size={14}
            thickness={5}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
        ) : (
          <Iconify
            icon="solar:laptop-2-linear"
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            typography: "s2",
            color: checkedIn ? "text.primary" : "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {checkedIn
            ? "Your machine checked in — follow the build here."
            : ranUp
              ? "Waiting for your machine to check in…"
              : "Run these on your machine — it shows here when it checks in."}
        </Typography>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!checkedIn}
          onClick={onOpen}
          sx={{ whiteSpace: "nowrap" }}
        >
          Open the build
        </Button>
      </Stack>
    </Box>
  );
}
LocalRun.propTypes = {
  template: PropTypes.object.isRequired,
  agent: PropTypes.object.isRequired,
  choice: PropTypes.object.isRequired,
  ranUp: PropTypes.bool,
  checkedIn: PropTypes.bool,
  onRanUp: PropTypes.func.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function Line({ label, value, muted = false }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 96,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: muted ? "text.subtitle" : "text.primary",
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Line.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  muted: PropTypes.bool,
};
