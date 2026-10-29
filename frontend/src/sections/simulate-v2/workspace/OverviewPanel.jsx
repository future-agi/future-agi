import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useTheme } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  Grid,
  Chip,
  Tooltip,
  CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface, getDomain } from "../_mock/surfaces";
import { twinById, liveSandboxContentFor } from "../_mock/twins";
import TwinLogo from "./../components/TwinLogo";
import SlackSandboxMock from "../twins/SlackSandboxMock";
import NotionSandboxMock from "../twins/NotionSandboxMock";
import GmailSandboxMock from "../twins/GmailSandboxMock";
import SalesforceSandboxMock from "../twins/SalesforceSandboxMock";
import GenericSandboxMock from "../twins/GenericSandboxMock";
import { OpenApiDialog, OpenSurfaceDialog } from "../twins/TwinSandboxDialogs";
import TwinConnectDialog from "../twins/TwinConnectDialog";

const SANDBOX_MOCKS = {
  slack: SlackSandboxMock,
  notion: NotionSandboxMock,
  gmail: GmailSandboxMock,
  salesforce: SalesforceSandboxMock,
};
import { goalLabel, packStats } from "../_mock/scenarios";
import { contractUseCasesOf, effectiveEnv } from "../_mock/rlContract";
import { contractFor } from "../_mock/contract";
import { provenanceFor } from "../_mock/provenance";
import { OriginChip, SectionCard, EmptyState } from "../components/primitives";
import CapabilityGraph from "./CapabilityGraph";
import ToolFitBanner from "./ToolFitBanner";
import { isConnectOnly, rulesOf } from "../_mock/envModel";
import { currentEnvVersion, scenariosInSet } from "../_mock/versions";
import { runListCount } from "../_mock/runList";
import { toolSettingOf } from "../_mock/toolEnv";
import { unexplainedMoveOf } from "../_mock/resultDrift";
import ConnectOnlyWorld, {
  ResultsMovedNote,
  EnvironmentKindNote,
} from "./ConnectOnlyWorld";
import { BlockingGapsInline } from "./SetupGapsPanel";
import EnvAgentLine from "./EnvAgentLine";
import useEnvViewAs from "./useEnvViewAs";
import { agentNameOf } from "../_mock/agentRegistry";
import { clickable, focusRing } from "../components/clickable";

/**
 * What this environment is.
 *
 * The page answers three questions in order, and is grouped that way rather
 * than as a wall of equal cards: what the agent is (its contract), what world
 * it acts on, and what decides whether it did well. The facts that used to sit
 * in a tall card on the right are a strip under the title instead — they are
 * labels, not content, and they were taking a column from things that are.
 */
const seedBlurb = (rows) =>
  `${rows.toLocaleString()} rows that fill this environment before your agent arrives — the world it actually works in. Rebuilt for every task, so nothing carries over.`;

export default function OverviewPanel({
  buildMode,
  env,
  envState,
  patch,
  onGo,
  agentConnected,
  locked = false,
  onFork,
  contractProgress,
  buildDone = null,
}) {
  /* The agent lives in Agents; "Connect agent" here opens the picker in place. */
  const [pick, setPick] = useState(0);
  const go = (step) => (step === "agent" ? setPick(Date.now()) : onGo?.(step));
  /* ?connect=agent (a sample run's "Connect my agent") opens the picker. */
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("connect") === "agent")
      setPick(Date.now());
  }, []);
  /* Viewing an older version (?viewAs=) is read-only: no rebuilds, switches
     or answers from here — those belong to the version in use. */
  const { viewAs } = useEnvViewAs(env, envState);
  const readOnly = locked || !!viewAs;

  const surface = getSurface(env.surface);
  const domain = getDomain(env.domain);
  const totalRows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  /* Depth is editable on the Scenarios step, so Overview must report what is
     in force rather than what the template shipped with. */
  const shown = effectiveEnv(env, envState);
  const stats = packStats(shown);
  const contract = contractFor(env);
  /* The use cases as they stand — corrections included, the same list the Contract tab shows. */
  const useCases = contractUseCasesOf(env, envState).map((u) => u.text);
  /* Whether the environment already carries a world — either read
     from a connected agent, or (for scratch envs) derived from the
     flow description on adopt. Drives whether we show the capability
     graph + the full downstream layout even before an agent is
     wired, so a scratch env's Overview reads like every other env's
     rather than a getting-started stub. */
  const hasDerivedWorld =
    (env.rules?.length || 0) > 0 ||
    (envState?.scenarios?.length || 0) > 0 ||
    (env.tools?.length || 0) > 0;
  const showRichOverview = agentConnected || hasDerivedWorld;
  /* Rules carry where they were found, and a rule found in prose is held back
     rather than graded — the badge is the only place that is visible here, so
     the card links through to the review queue. */
  const ruleProv = provenanceFor(env).rules;
  const held = ruleProv.filter(
    (r) => r.held && !(envState?.confirmedRules || []).includes(r.id),
  );
  /* A connect-only agent has no world, and a result can move for reasons
     outside everything we version — the account it books under. */
  const connectOnly = isConnectOnly(env);
  const moved = useMemo(
    () => (connectOnly ? unexplainedMoveOf(env, envState) : null),
    [connectOnly, env, envState],
  );
  /* While it builds, the page fills in the order the build learns things:
     the agent first, then what it is as its tools are read, the capabilities
     as they stream, use cases once rules are found, the world once stores
     are, and the counts when the build is done. */
  const inBuild =
    !!buildMode && Array.isArray(buildDone) && !buildDone.includes("build");
  const prog = contractProgress || {};
  const understood = !!buildDone?.includes("understand");
  const showIntro = !inBuild || (prog.tools || 0) > 0 || understood;
  const showUseCases = !inBuild || (prog.rules || 0) > 0 || understood;
  const showWorld = !inBuild || (prog.stores || 0) > 0;
  const showSummary = !inBuild;

  return (
    <Box sx={{ p: 2 }}>
      {/* TwinSandboxSection + CapabilityGraph moved to the Contract tab.
          Overview now answers "how's this env doing right now?" — state,
          not definition. */}

      {/*
        "Connect your agent": an environment built from a description has
        no agent until one is connected — say so here, in the review step
        as well as the workspace. Hidden only while the build is still
        running. (Building from an agent attaches it before this mounts,
        so it never shows there.)
      */}
      {!agentConnected && !inBuild && (
        <Box
          sx={{
            p: 2,
            mb: 2,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={2}
          >
            <Box flex={1}>
              {/* Its agent was deleted from Agents: say so, and that nothing here went with it. */}
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                {envState?.removedAgent
                  ? `${envState.removedAgent.name || "Its agent"} was deleted — connect an agent to run again`
                  : "Connect your agent to start testing"}
              </Typography>
              <Typography
                sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
              >
                {envState?.removedAgent
                  ? "Scenarios, evaluations and past runs are all still here."
                  : `${surface.blurb} We handle the ${surface.transports.join(", ")} side — you point us at your agent.`}
              </Typography>
            </Box>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => go("agent")}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
              sx={{ flexShrink: 0 }}
            >
              Connect agent
            </Button>
          </Stack>
        </Box>
      )}

      {/*
        Difficulty sits here rather than in the strip below: that strip is a
        row of read-only facts, and dropping one interactive cell into it broke
        both the alignment and the pattern. As a labelled select beside the
        description it reads as what it is — a setting you change before a run.
      */}
      {showIntro && (
        <FadeIn>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "flex-start" }}
            spacing={2}
          >
            {/* One line; the full description on hover. */}
            <Tooltip arrow title={env.description || ""} enterDelay={400}>
              <Typography
                noWrap
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  minWidth: 0,
                  flex: 1,
                }}
              >
                {env.description}
              </Typography>
            </Tooltip>
          </Stack>

          {/* Labels, not content — a strip rather than a column of its own. */}
          <Stack
            direction="row"
            flexWrap="wrap"
            divider={
              <Box
                sx={{
                  width: "1px",
                  bgcolor: "divider",
                  alignSelf: "stretch",
                  mx: 1.5,
                }}
              />
            }
            sx={{
              my: 1.5,
              rowGap: 1,
            }}
          >
            <Fact label="Channel" value={surface.label} />
            <Fact label="Domain" value={domain?.label || "—"} />
            <Fact label="Transports" value={surface.transports.join(" · ")} />
            <Fact
              label="Scenario packs"
              value={`${stats.packs} packs · ${envState?.scenarios?.length || stats.scenarios} scenarios`}
            />
          </Stack>
        </FadeIn>
      )}

      {/*
        What holds the run, answered where it's read (PRD v6 Story 3). The
        full inbox — optional and settled items too — opens from the header.
        "Connect an agent" is skipped while the banner above is saying it.
      */}
      {!buildMode && (
        <BlockingGapsInline
          env={env}
          envState={envState}
          patch={readOnly ? null : patch}
          onGo={go}
          skip={agentConnected ? [] : ["no-agent"]}
          sx={{ mb: 2 }}
        />
      )}

      {/* The agent this environment tests, in one line — its detail is the Agent tab. */}
      <EnvAgentLine
        env={env}
        envState={envState}
        patch={patch}
        locked={readOnly}
        onFork={onFork}
        openPick={pick}
      />

      {!readOnly && (
        <AgentRefreshBanner env={env} envState={envState} patch={patch} />
      )}

      {/* State-of-the-env summary tiles + latest run — the "how's this
          env doing right now?" answer that used to be missing. Each
          tile jumps to the tab it summarises. */}
      {showSummary && (
        <FadeIn>
          <StateSummary env={env} envState={envState} onGo={go} />
        </FadeIn>
      )}
      <ResultsMovedNote move={moved} sx={{ mt: -1.5, mb: 3 }} />

      {/*
        Getting-started checklist for envs that haven't been seeded
        yet (no agent, no derived rules/scenarios). Scratch envs come
        in already carrying derived world + scenarios + eval
        suggestions, so the same checklist would be misleading —
        every step would render pre-complete. Skip it there and let
        the CapabilityGraph + downstream sections do the talking.
      */}
      {!agentConnected && !envState?.twinBacking && !hasDerivedWorld && (
        <NextStepsChecklist env={env} envState={envState} onGo={go} />
      )}

      {/*
        Twin-backed envs get their own set of sections below —
        Capabilities/The world are read from a source repo, and twin
        envs don't have one. Skipping this whole block prevents empty
        "0 tools · 0 rules · 0 rows" cards from filling the screen.
      */}
      {envState?.twinBacking ? (
        <TwinBackedSections env={env} envState={envState} onGo={go} />
      ) : (
        <>
          {/*
        Formerly "The contract" — collided with the dedicated Contract
        tab (which holds the RL contract: adapter, spaces, dynamics,
        reward). This group is a capability inventory of the source
        agent, so the label now reflects that and the collision is
        gone.
      */}
          <GroupHeading>Capabilities</GroupHeading>
          {/* CapabilityGraph moved to the Contract tab — that's where the
          shape of the environment belongs. Overview keeps the summary,
          not the definition. */}
          {/*
        The reviewability record — how the reader mapped source to
        sandbox. Every derived fact carries an origin and a sandbox
        target, side by side. This is the artifact someone comes
        back to three weeks later to see which decisions a human
        made and which the reader made itself.
      */}
          {(showRichOverview || contractProgress?.streaming) && (
            <SourceToSandboxMap
              env={env}
              envState={envState}
              progress={contractProgress}
            />
          )}
          {showUseCases && (
            <FadeIn>
              <Grid
                container
                spacing={2}
                alignItems="flex-start"
                sx={{ mb: 3 }}
              >
                <Grid item xs={12} md={7}>
                  <SectionCard
                    dense
                    title="Use cases"
                    subtitle="What it is actually for"
                  >
                    <Stack sx={{ p: 1.5 }} spacing={1}>
                      {useCases.map((u) => (
                        <Stack
                          key={u}
                          direction="row"
                          spacing={1.25}
                          alignItems="flex-start"
                        >
                          <Box
                            sx={{
                              width: 4,
                              height: 4,
                              borderRadius: "50%",
                              bgcolor: "text.tertiary",
                              flexShrink: 0,
                              mt: "7px",
                            }}
                          />
                          <Typography
                            sx={{ typography: "s2", color: "text.secondary" }}
                          >
                            {goalLabel(u)}
                          </Typography>
                        </Stack>
                      ))}
                    </Stack>
                  </SectionCard>
                </Grid>

                <Grid item xs={12} md={5}>
                  {!buildMode && (
                    <SectionCard
                      dense
                      title="Amendments"
                      subtitle="Changed after reading, each with its reason"
                    >
                      <Stack sx={{ p: 1.5 }} spacing={1.25}>
                        {contract.amendments.map((a) => (
                          <Box
                            key={a.subject}
                            sx={{
                              p: 1.5,
                              borderRadius: 1,
                              border: "1px solid",
                              borderColor: "divider",
                            }}
                          >
                            <Typography
                              sx={{
                                typography: "s2",
                                fontWeight: 500,
                                fontFamily: "ui-monospace, Menlo, monospace",
                              }}
                            >
                              {a.subject}
                            </Typography>
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.secondary",
                                mt: 0.25,
                              }}
                            >
                              {a.note}
                            </Typography>
                          </Box>
                        ))}
                      </Stack>
                    </SectionCard>
                  )}
                </Grid>
              </Grid>
            </FadeIn>
          )}

          {/* ── the world it acts on ── */}
          {showWorld && (
            <FadeIn>
              <GroupHeading>The world</GroupHeading>
              {/* Sealed or Live (RSI PRD) — said where the world is. */}
              <EnvironmentKindNote
                env={env}
                envState={envState}
                sx={{ mb: 2 }}
              />
              {connectOnly ? (
                <ConnectOnlyWorld env={env} sx={{ mb: 3 }} />
              ) : (
                <Grid
                  container
                  spacing={2}
                  alignItems="flex-start"
                  sx={{ mb: 3 }}
                >
                  <Grid item xs={12} md={7}>
                    <SectionCard
                      dense
                      title="Seeded data"
                      subtitle={seedBlurb(totalRows)}
                    >
                      <Stack
                        divider={
                          <Box
                            sx={{
                              borderBottom: "1px solid",
                              borderColor: "divider",
                            }}
                          />
                        }
                      >
                        {env.seed.tables.map((t) => (
                          <Stack
                            key={t.name}
                            direction="row"
                            alignItems="center"
                            spacing={2}
                            sx={{ px: 2.5, py: 1 }}
                          >
                            <Iconify
                              icon="solar:database-linear"
                              width={16}
                              sx={{ color: "text.tertiary", flexShrink: 0 }}
                            />
                            <Box flex={1} minWidth={0}>
                              <Typography
                                sx={{
                                  typography: "s2",
                                  fontWeight: 500,
                                  fontFamily: "ui-monospace, Menlo, monospace",
                                }}
                              >
                                {t.name}
                              </Typography>
                              {t.note && (
                                <Typography
                                  noWrap
                                  sx={{
                                    typography: "s3",
                                    color: "text.tertiary",
                                  }}
                                >
                                  {t.note}
                                </Typography>
                              )}
                            </Box>
                            <Typography
                              sx={{
                                typography: "s2",
                                fontWeight: 600,
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {t.rows.toLocaleString()}
                            </Typography>
                          </Stack>
                        ))}
                      </Stack>
                    </SectionCard>
                  </Grid>

                  <Grid item xs={12} md={5}>
                    <SectionCard
                      dense
                      title="What it depends on"
                      subtitle="Built and torn down with the environment"
                    >
                      <Stack
                        divider={
                          <Box
                            sx={{
                              borderBottom: "1px solid",
                              borderColor: "divider",
                            }}
                          />
                        }
                      >
                        {contract.dependsOn.map((d) => (
                          <Box key={d.name} sx={{ px: 2.5, py: 1 }}>
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={1}
                            >
                              <Typography
                                sx={{
                                  typography: "s2",
                                  fontWeight: 500,
                                  fontFamily: "ui-monospace, Menlo, monospace",
                                }}
                              >
                                {d.name}
                              </Typography>
                              <Typography
                                sx={{
                                  typography: "s3",
                                  color: "text.tertiary",
                                }}
                              >
                                {d.kind}
                              </Typography>
                            </Stack>
                            <Typography
                              sx={{
                                typography: "s2",
                                color: "text.secondary",
                                mt: 0.25,
                              }}
                            >
                              {d.provides}
                            </Typography>
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                mt: 0.5,
                                fontFamily: "ui-monospace, Menlo, monospace",
                              }}
                            >
                              used by {d.usedBy}
                            </Typography>
                          </Box>
                        ))}
                      </Stack>
                    </SectionCard>
                  </Grid>
                </Grid>
              )}
            </FadeIn>
          )}
        </>
      )}
    </Box>
  );
}

/* A block arriving while the environment builds. */
function FadeIn({ children }) {
  return (
    <Box
      sx={{
        animation: "ov-in 360ms ease both",
        "@keyframes ov-in": {
          from: { opacity: 0, transform: "translateY(4px)" },
          to: { opacity: 1, transform: "none" },
        },
      }}
    >
      {children}
    </Box>
  );
}
FadeIn.propTypes = { children: PropTypes.node };

OverviewPanel.propTypes = {
  /* Build stages finished so far (build screen only) — paces the reveal. */
  buildDone: PropTypes.array,
  buildMode: PropTypes.bool,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  agentConnected: PropTypes.bool,
  locked: PropTypes.bool,
  onFork: PropTypes.func,
  /* While a build is reading the agent: how many tools, rules and stores it
     has found so far — the build table fills in as they land. */
  contractProgress: PropTypes.object,
};

/**
 * StateSummary — the state-of-the-env answer that Overview should lead with.
 *
 * Four tiles (Scenarios, Evaluations, Runs, Rules) each clickable to jump to
 * the relevant tab. Every number comes from the same selector the tab and
 * the version bar use — scenarios in the set (scenariosInSet), the Runs
 * list's own count, rules with their edits — so the summary and the detail
 * can't drift.
 */
function StateSummary({ env, envState, onGo }) {
  const scenarioCount = scenariosInSet(
    envState,
    currentEnvVersion(env, envState)?.label,
  ).length;
  const evalCount = envState?.evals?.length || 0;
  const runCount = runListCount(env, envState);
  const ruleCount = rulesOf(env, envState).length;

  const tiles = [
    {
      id: "scenarios",
      label: "Scenarios",
      value: scenarioCount,
      icon: "solar:layers-minimalistic-linear",
      to: "scenarios",
    },
    {
      id: "evals",
      label: "Evaluations",
      value: evalCount,
      icon: "solar:shield-check-linear",
      to: "evals",
    },
    {
      id: "runs",
      label: "Runs",
      value: runCount,
      icon: "solar:play-circle-linear",
      to: "runs",
      disabled: runCount === 0,
    },
    {
      id: "rules",
      label: "Rules",
      value: ruleCount,
      icon: "solar:shield-keyhole-linear",
      to: "contract",
    },
  ];

  /* One quiet line of counts, each opening its tab — not four boxes. */
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const words = {
    scenarios: "scenario",
    evals: "evaluation",
    runs: "run",
    rules: "rule",
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      sx={{ mb: 2.5, typography: "s2", color: "text.tertiary" }}
    >
      {tiles.map((t, i) => (
        <Box key={t.id} component="span" sx={{ display: "inline-flex" }}>
          {i > 0 && (
            <Box component="span" sx={{ mx: 1, color: "text.disabled" }}>
              ·
            </Box>
          )}
          <Box
            component="span"
            {...clickable(t.disabled ? undefined : () => onGo?.(t.to), {
              role: "link",
            })}
            sx={{
              ...focusRing,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
              cursor: t.disabled ? "default" : "pointer",
              "&:hover": t.disabled
                ? {}
                : { color: "text.primary", textDecoration: "underline" },
            }}
          >
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: 500 }}
            >
              {t.value}
            </Box>
            {` ${plural(t.value, words[t.id]).replace(/^\d+ /, "")}`}
          </Box>
        </Box>
      ))}
    </Stack>
  );
}
StateSummary.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onGo: PropTypes.func,
};

function GroupHeading({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 500,
        color: "text.secondary",
        textTransform: "none",
        mb: 1,
      }}
    >
      {children}
    </Typography>
  );
}
GroupHeading.propTypes = { children: PropTypes.node };

function Fact({ label, value, color }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{
          typography: "s2",
          fontWeight: 500,
          color: color || "text.primary",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
Fact.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  color: PropTypes.string,
};

/* ── agent-moved-ahead banner ────────────────────────────────────────────────
   When the active agent version is newer than the version the environment was
   derived against, offer to re-derive. It's the user's choice: refresh (mint a
   new env version that re-reads the contract/scenarios/evals against the latest
   agent — it then shows in the env-version dropdown up top), or just run the
   new agent version against the existing world as-is (scenarios are shared
   across agent versions, so nothing forces a refresh). */
function AgentRefreshBanner({ env, envState, patch }) {
  return (
    <ToolFitBanner
      env={env}
      envState={envState}
      patch={patch}
      sx={{ mt: -0.5, mb: 2 }}
      deferToCard
    />
  );
}
AgentRefreshBanner.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
};

/* ── twin backing surface panel ───────────────────────────────────────────── */

/*
  Activity derivation. In production the twin runtime streams every
  read/write; here we materialise the same shape from the run
  history so the counters read as real usage of the sandbox rather
  than static fixtures. Stable per (envState, service) so a
  refresh doesn't renumber them, and grows monotonically with
  run count so the surface feels alive without needing a websocket.
*/
function activityForService(envState, service, index, totalServices) {
  const runs = envState?.runs || [];
  const totalRuns = runs.length;
  const seed = hash(service) % 7;
  const writesPerRun = 2 + ((seed + index) % 4);
  const readsPerRun = 4 + ((seed + index * 3) % 5);
  const totalWrites = totalRuns * writesPerRun;
  const totalReads = totalRuns * readsPerRun;
  /*
    Last-touched marker — the service that would have been written most
    recently in the last run. Rotates across services by run index so
    the live pulse doesn't sit on the same lane forever.
  */
  const lastTouched =
    totalRuns > 0 && index === (totalRuns - 1) % Math.max(1, totalServices);
  const lastAt = totalRuns > 0 ? runs[0]?.finishedAt : null;
  const trend = Array.from({ length: 6 }, (_, i) => {
    if (totalRuns === 0) return 0;
    const runIdx = Math.max(0, totalRuns - 6 + i);
    return runIdx < totalRuns ? (hash(`${service}-${runIdx}`) % 5) + 1 : 0;
  });
  return { writes: totalWrites, reads: totalReads, lastTouched, lastAt, trend };
}

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function timeAgo(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * Twin sandbox preview — the star of the Overview tab for twin-backed
 * envs. Renders the same service mock (Slack, Notion, Gmail,
 * Salesforce, generic fallback) the standalone Twin detail page shows,
 * plus header chrome (service chips for multi-service twins, endpoint
 * URL, OpenAPI/Open surface buttons). This is what makes a twin-backed
 * env feel like a real running product on the Overview, not a wall of
 * metadata cards.
 */
export function TwinSandboxSection({ env, envState }) {
  const backing = envState.twinBacking;
  const services = backing.services || [];
  const [activeServiceId, setActiveServiceId] = useState(services[0]);
  const [openApiOpen, setOpenApiOpen] = useState(false);
  const [surfaceOpen, setSurfaceOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const activeTwin = twinById(activeServiceId);
  const SandboxMock = SANDBOX_MOCKS[activeServiceId] || null;

  return (
    <Box sx={{ mb: 2 }}>
      {/* service chip tabs — only shown when multi-service */}
      {services.length > 1 && (
        <Stack
          direction="row"
          spacing={0.75}
          sx={{ mb: 1.5 }}
          flexWrap="wrap"
          useFlexGap
        >
          {services.map((sId) => {
            const t = twinById(sId);
            const on = sId === activeServiceId;
            return (
              <Chip
                key={sId}
                size="small"
                onClick={() => setActiveServiceId(sId)}
                icon={
                  <TwinLogo twin={t} width={11} sx={{ ml: "6px !important" }} />
                }
                label={t?.name || sId}
                /*
                  Hover was inheriting MUI's default background darken/
                  lighten, which on dark theme flashed a near-white pill
                  and washed out the multicolor logos. Explicit hover
                  bgcolor keeps the pill's contrast against both the
                  panel and the logo underneath. Active state uses the
                  darker end of the tinted-purple range on dark so a
                  Notion "N" or Docs-blue icon still reads.
                */
                sx={{
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: on ? "status.discovery.border" : "divider",
                  bgcolor: on ? "status.discovery.bg" : "background.paper",
                  color: on ? "status.discovery.fg" : "text.primary",
                  "&:hover": {
                    bgcolor: on ? "status.discovery.bg" : "action.hover",
                    borderColor: on
                      ? "status.discovery.border"
                      : "text.disabled",
                  },
                }}
              />
            );
          })}
        </Stack>
      )}

      {/* sub-header — service name + endpoint + OpenAPI / Open surface */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ mb: 1.25 }}
      >
        <TwinLogo twin={activeTwin} width={22} />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "s1_2", fontWeight: 600 }}>
              {activeTwin?.name || "Sandbox"}
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Box
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  bgcolor: "status.success.solid",
                }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "status.success.fg",
                }}
              >
                Serving
              </Typography>
            </Stack>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontFamily: "ui-monospace, Menlo, monospace",
              }}
              noWrap
            >
              {backing.endpoints?.[activeServiceId] || "—"}
            </Typography>
            {/*
              API surface badge — reads at a glance whether this twin
              also exposes a browsable UI or is API-only. Drives the
              conditional Open-surface render below so we never offer
              a UI on a twin that has none.
            */}
            <ApiSurfaceBadge apiLevel={activeTwin?.apiLevel} />
          </Stack>
        </Box>
        {/*
          Agent connection state: the composer captures the agent
          before provisioning, so this sub-header only shows the
          connection state — it doesn't re-offer connecting. Clicking
          the pill opens the connect dialog for reconnection.
          The bare "Connect agent" button only appears for envs that
          arrived here without a connected agent (legacy adopt path).
        */}
        {/* Connected = a real agent from Agents (not a template's placeholder);
            a SaaS-sandbox agent has no endpoint of its own, so that isn't the test. */}
        {envState?.agent && !envState.agent.seeded ? (
          <Tooltip
            arrow
            title={`Connected: ${envState.agent.name || agentNameOf(envState.agent) || "your agent"}${envState.agent.values?.sdkEndpoint ? ` · ${envState.agent.values.sdkEndpoint}` : ""}`}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              {...clickable(() => setConnectOpen(true))}
              sx={{
                ...focusRing,
                px: 1,
                height: 28,
                borderRadius: 999,
                cursor: "pointer",
                border: (t) => `1px solid ${t.palette.status.success.border}`,
                bgcolor: "status.success.bg",
                color: "status.success.fg",
                "&:hover": {
                  bgcolor: "status.success.bg",
                },
              }}
            >
              <Iconify icon="solar:check-circle-linear" width={12} />
              <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                Agent connected
              </Typography>
            </Stack>
          </Tooltip>
        ) : (
          <Button
            variant="contained"
            size="small"
            onClick={() => setConnectOpen(true)}
            startIcon={<Iconify icon="solar:link-circle-linear" width={14} />}
          >
            Connect agent
          </Button>
        )}
        <Button
          variant="outlined"
          size="small"
          onClick={() => setOpenApiOpen(true)}
          startIcon={<Iconify icon="solar:code-square-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          OpenAPI
        </Button>
        {activeTwin?.apiLevel === "api+ui" && (
          <Button
            variant="outlined"
            size="small"
            onClick={() => setSurfaceOpen(true)}
            startIcon={
              <Iconify icon="solar:square-top-down-linear" width={14} />
            }
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Open surface
          </Button>
        )}
      </Stack>

      <SandboxActivityRibbon envState={envState} serviceId={activeServiceId} />

      {/*
        Render logic:
          · api+ui twin with a bespoke mock → the mock
          · api+ui twin without one → generic browser-chrome mock
          · api-only twin → an "API-only" panel that doesn't lie
            about a browsable surface. Reinforces the badge above.
      */}
      {activeTwin?.apiLevel === "api" ? (
        <ApiOnlySandboxPanel
          twin={activeTwin}
          endpoint={backing.endpoints?.[activeServiceId] || ""}
          envState={envState}
        />
      ) : SandboxMock ? (
        <SandboxMock workspace={env.name} envState={envState} />
      ) : (
        <GenericSandboxMock twin={activeTwin} />
      )}

      <OpenApiDialog
        open={openApiOpen}
        onClose={() => setOpenApiOpen(false)}
        twin={activeTwin}
        endpoint={backing.endpoints?.[activeServiceId] || ""}
      />
      <OpenSurfaceDialog
        open={surfaceOpen}
        onClose={() => setSurfaceOpen(false)}
        twin={activeTwin}
        env={env}
        SandboxMock={SandboxMock}
      />
      <TwinConnectDialog
        open={connectOpen}
        onClose={() => setConnectOpen(false)}
        twin={activeTwin}
        endpoint={backing.endpoints?.[activeServiceId] || ""}
        sessionId={`sess_${(env?.id || "").slice(-8) || "01ABC"}`}
      />
    </Box>
  );
}
TwinSandboxSection.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
};

function TwinBackingPanel({ backing, envState }) {
  const services = backing.services || [];
  const totalRuns = envState?.runs?.length || 0;
  const anyActivity = totalRuns > 0;

  return (
    <Box sx={{ mb: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Iconify
          icon="solar:server-square-linear"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.primary",
          }}
        >
          Copied services
        </Typography>
        <Chip
          size="small"
          label={anyActivity ? "Active" : "Ready"}
          sx={{
            bgcolor: "status.success.bg",
            color: "status.success.fg",
            border: "1px solid",
            borderColor: "status.success.border",
          }}
        />
        <Box flex={1} />
        {anyActivity && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {totalRuns} run{totalRuns === 1 ? "" : "s"} against this sandbox
          </Typography>
        )}
      </Stack>
      {backing.seedPrompt && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}>
          Seeded from prompt:{" "}
          <Box
            component="span"
            sx={{ color: "text.primary", fontStyle: "italic" }}
          >
            &ldquo;{backing.seedPrompt}&rdquo;
          </Box>
        </Typography>
      )}
      <Stack spacing={1}>
        {services.map((sId, index) => {
          const t = twinById(sId);
          if (!t) return null;
          const activity = activityForService(
            envState,
            sId,
            index,
            services.length,
          );
          return (
            <Box
              key={sId}
              sx={{
                p: 1.5,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: activity.lastTouched
                  ? "status.success.border"
                  : "divider",
                bgcolor: "background.paper",
                position: "relative",
                transition: "border-color 200ms ease",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Box
                  sx={{
                    position: "relative",
                    flexShrink: 0,
                    width: 30,
                    height: 30,
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  <TwinLogo twin={t} width={22} />
                  {activity.lastTouched && (
                    /*
                      Live-pulse dot on the service that took the most
                      recent write. Purely presentational — the ring
                      pulses via keyframes rather than a JS interval so
                      it costs nothing and stays smooth even while the
                      user is interacting with the surface.
                    */
                    <Box
                      sx={{
                        position: "absolute",
                        top: -2,
                        right: -2,
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        bgcolor: "status.success.solid",
                        "&::after": {
                          content: '""',
                          position: "absolute",
                          inset: -3,
                          borderRadius: "50%",
                          border: "2px solid",
                          borderColor: "status.success.solid",
                          opacity: 0.4,
                          animation: "twin-pulse 1.8s ease-out infinite",
                        },
                        "@keyframes twin-pulse": {
                          "0%": { transform: "scale(0.85)", opacity: 0.5 },
                          "80%": { transform: "scale(1.9)", opacity: 0 },
                          "100%": { transform: "scale(1.9)", opacity: 0 },
                        },
                      }}
                    />
                  )}
                </Box>
                <Box flex={1} minWidth={0}>
                  <Stack direction="row" alignItems="baseline" spacing={0.75}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {t.name}
                    </Typography>
                    {activity.lastTouched && (
                      <Typography
                        sx={{
                          typography: "s3",
                          fontWeight: 600,
                          color: "status.success.fg",
                          textTransform: "none",
                          letterSpacing: 0.4,
                        }}
                      >
                        just now
                      </Typography>
                    )}
                  </Stack>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}
                  >
                    {backing.endpoints?.[sId] || "—"}
                  </Typography>
                </Box>
                {anyActivity && (
                  <>
                    <ActivityMetric
                      label="writes"
                      value={activity.writes}
                      tint="primary.main"
                    />
                    <ActivityMetric label="reads" value={activity.reads} />
                    <TrendSparkline points={activity.trend} tint={t.color} />
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        minWidth: 56,
                        textAlign: "right",
                        flexShrink: 0,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {timeAgo(activity.lastAt)}
                    </Typography>
                  </>
                )}
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={
                    <Iconify icon="solar:code-square-linear" width={12} />
                  }
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  OpenAPI
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={
                    <Iconify icon="solar:square-top-down-linear" width={12} />
                  }
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Open surface
                </Button>
              </Stack>
            </Box>
          );
        })}
      </Stack>
      {!anyActivity && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            mt: 1,
            fontStyle: "italic",
          }}
        >
          Activity counters and per-service sparklines light up as soon as your
          first run lands.
        </Typography>
      )}
    </Box>
  );
}
TwinBackingPanel.propTypes = {
  backing: PropTypes.object,
  envState: PropTypes.object,
};

function ActivityMetric({ label, value, tint }) {
  return (
    <Box sx={{ minWidth: 44, flexShrink: 0, textAlign: "right" }}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: tint || "text.primary",
          fontVariantNumeric: "tabular-nums",
          lineHeight: 1.1,
        }}
      >
        {value}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          textTransform: "none",
          letterSpacing: 0.3,
          lineHeight: 1.1,
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}
ActivityMetric.propTypes = {
  label: PropTypes.string,
  value: PropTypes.number,
  tint: PropTypes.string,
};

/**
 * Six-point sparkline of writes per run over the last six runs. SVG
 * kept inline — a fixed 60×22 box, path built from the values on
 * every render (cheap). Preferred over Recharts here because we need
 * this to render dozens of times on one page without layout thrash.
 */
function TrendSparkline({ points, tint }) {
  const theme = useTheme();
  const stroke = tint || theme.palette.primary.main;
  const w = 60;
  const h = 22;
  const max = Math.max(1, ...points);
  const step = w / Math.max(1, points.length - 1);
  const d = points
    .map(
      (v, i) =>
        `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`,
    )
    .join(" ");
  return (
    <Box
      sx={{
        flexShrink: 0,
        width: w,
        height: h,
        display: { xs: "none", md: "block" },
      }}
    >
      <svg width={w} height={h}>
        <path
          d={d}
          fill="none"
          stroke={stroke}
          strokeWidth={1.4}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {points.map((v, i) => (
          <circle
            key={i}
            cx={i * step}
            cy={h - (v / max) * (h - 4) - 2}
            r={i === points.length - 1 ? 2 : 1.2}
            fill={stroke}
          />
        ))}
      </svg>
    </Box>
  );
}
TrendSparkline.propTypes = { points: PropTypes.array, tint: PropTypes.string };

/*
  Overview sections for twin-backed envs — replace the source-derived
  Capabilities and "The world" groups. Focused on what actually exists
  in a twin env: the twinned services, the starting state each run
  begins from, what evals can inspect on the end state, and the agent
  wired to the sandbox.
*/
function TwinBackedSections({ env, envState, onGo }) {
  const backing = envState.twinBacking;
  const services = backing?.services || [];
  const seedShape = backing?.seed || {};
  const agent = envState?.agent;

  /*
    Human-readable summary of what the seed prompt actually produced,
    per service. Falls back to "Empty starting state" if no seed
    entities were resolved for that twin.
  */
  const seedSummary = (sId) => {
    const shape = seedShape[sId] || {};
    const parts = [];
    Object.entries(shape).forEach(([key, val]) => {
      if (Array.isArray(val) && val.length > 0) {
        parts.push(`${val.length} ${key}`);
      }
    });
    return parts.length ? parts.join(" · ") : "Empty starting state";
  };

  const evalPresets = env?.evalPreset || [];
  const evalLabels = {
    task_success: {
      title: "Task success",
      body: "Did the run reach the expected outcome?",
    },
    twin_end_state_match: {
      title: "End-state match",
      body: "Did the correct rows / messages / records actually land in the cloned services?",
    },
    twin_no_extra_writes: {
      title: "No extra writes",
      body: "Did the agent write only what the task required — no spurious side effects?",
    },
  };

  return (
    <>
      <GroupHeading>Sandboxed apps</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        {/* ── services list ── */}
        <Grid item xs={12} md={7}>
          <SectionCard
            dense
            title="Sandboxed apps"
            subtitle={`${services.length} sandbox${services.length === 1 ? "" : "es"} provisioned for this environment`}
          >
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {services.map((sId) => {
                const twin = twinById(sId);
                return (
                  <Stack
                    key={sId}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{ px: 2.5, py: 1 }}
                  >
                    <TwinLogo twin={twin} width={18} />
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {twin?.name || sId}
                      </Typography>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          fontFamily: "ui-monospace, Menlo, monospace",
                        }}
                      >
                        {backing?.endpoints?.[sId] || "—"}
                      </Typography>
                    </Box>
                    <ApiSurfaceBadge apiLevel={twin?.apiLevel} />
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>

        {/* ── connected agent ── */}
        <Grid item xs={12} md={5}>
          <SectionCard
            dense
            title="Connected agent"
            subtitle="Where the sandbox calls your agent"
          >
            <Stack sx={{ p: 1.5 }} spacing={1.25}>
              {/* Connected = a real agent from Agents, named; its endpoint
                  only when it has one (a SaaS-sandbox agent doesn't). */}
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify
                  icon="solar:link-circle-linear"
                  width={14}
                  sx={{ color: "primary.main" }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {agent && !agent.seeded
                    ? agent.name || agentNameOf(agent) || "Your agent"
                    : "Not connected"}
                </Typography>
              </Stack>
              {agent && !agent.seeded && agent.values?.sdkEndpoint && (
                <>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}
                  >
                    {agent.values.sdkEndpoint}
                  </Typography>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Iconify
                      icon="solar:key-linear"
                      width={14}
                      sx={{ color: "text.tertiary" }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {agent.values.authToken
                        ? "Auth token set"
                        : "No auth header — endpoint is open"}
                    </Typography>
                  </Stack>
                </>
              )}
              {agent?.connectedAt && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Connected {timeAgo(agent.connectedAt)}
                </Typography>
              )}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>

      <GroupHeading>Starting state</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        {/* ── seed prompt + resolved shape ── */}
        <Grid item xs={12} md={7}>
          <SectionCard
            dense
            title="Seed prompt"
            subtitle="Natural-language description of what the sandbox starts with each run"
          >
            <Box sx={{ p: 1.5 }}>
              {backing?.seedPrompt ? (
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    fontStyle: "italic",
                    p: 1.5,
                    borderRadius: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  &ldquo;{backing.seedPrompt}&rdquo;
                </Typography>
              ) : (
                <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                  No seed prompt. Each service starts empty; scenarios can seed
                  per-run.
                </Typography>
              )}
            </Box>
          </SectionCard>
        </Grid>

        {/* ── resolved shape per service ── */}
        <Grid item xs={12} md={5}>
          <SectionCard
            dense
            title="Resolves to"
            subtitle="Concrete state per service after seeding"
          >
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {services.map((sId) => {
                const twin = twinById(sId);
                return (
                  <Stack
                    key={sId}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{ px: 2.5, py: 1 }}
                  >
                    <TwinLogo twin={twin} width={14} />
                    <Typography
                      sx={{ typography: "s3", fontWeight: 600, minWidth: 80 }}
                    >
                      {twin?.name || sId}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                        flex: 1,
                        minWidth: 0,
                      }}
                      noWrap
                    >
                      {seedSummary(sId)}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>

      <GroupHeading>What evals can check</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12}>
          <SectionCard
            dense
            title="End-state graders"
            subtitle="Sandbox evals inspect what actually landed after each run — not just whether a tool was called"
            action={
              <Button size="small" onClick={() => onGo?.("evals")}>
                Configure
              </Button>
            }
          >
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {evalPresets.map((k) => {
                const meta = evalLabels[k];
                if (!meta) return null;
                return (
                  <Stack
                    key={k}
                    direction="row"
                    spacing={1.5}
                    alignItems="flex-start"
                    sx={{ px: 2.5, py: 1.5 }}
                  >
                    <Iconify
                      icon="solar:target-linear"
                      width={14}
                      sx={{ color: "primary.main", mt: "3px", flexShrink: 0 }}
                    />
                    <Box flex={1}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {meta.title}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {meta.body}
                      </Typography>
                    </Box>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.tertiary",
                        textTransform: "none",
                        letterSpacing: 0.3,
                        flexShrink: 0,
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}
                    >
                      {k}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>
    </>
  );
}
TwinBackedSections.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onGo: PropTypes.func,
};

/*
  Small badge that reads "API" or "API + UI". Purple when the twin
  also exposes a UI (this is the higher-value tier — the sandbox
  can be inspected visually mid-run); neutral when API-only (still
  useful, just no browsable surface). Sits next to the endpoint URL
  so it's read in the same glance.
*/
function ApiSurfaceBadge({ apiLevel }) {
  const isUi = apiLevel === "api+ui";
  const label = isUi ? "API + UI" : "API only";
  const role = isUi ? "discovery" : "neutral";
  return (
    <Tooltip
      arrow
      title={
        isUi
          ? "This sandbox exposes both an API for the agent to call and a browsable UI you can open to inspect state."
          : "This sandbox exposes an API only. Inspect state via the OpenAPI viewer or the Raw requests tab in a run."
      }
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          letterSpacing: 0.3,
          px: 0.625,
          py: 0.125,
          borderRadius: "4px",
          color: `status.${role}.fg`,
          bgcolor: `status.${role}.bg`,
          border: "1px solid",
          borderColor: `status.${role}.border`,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
    </Tooltip>
  );
}
ApiSurfaceBadge.propTypes = { apiLevel: PropTypes.string };

/*
  Replacement for the browser-chrome sandbox mock when the twin is
  API-only (Jira, HubSpot, Linear, Stripe, QuickBooks). We do not
  fake a UI these services expose only via API — that would misread
  as "our sandbox has this UI" and drift from Arga's own model.
  Instead: a data-panel showing the base URL, the modelled surfaces,
  and pointers to the two ways to inspect state (OpenAPI + Raw
  requests during a run).
*/
function ApiOnlySandboxPanel({ twin, endpoint, envState }) {
  const requests = envState?.twinBacking?.activity?.[twin?.id]?.requests || 0;
  return (
    <Box
      sx={{
        borderRadius: "8px",
        overflow: "hidden",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        height: 800,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* header */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <TwinLogo twin={twin} width={22} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {twin?.name} copy — API only
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}
            noWrap
          >
            {endpoint}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Box
            sx={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              bgcolor: "status.success.solid",
            }}
          />
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "status.success.fg",
            }}
          >
            Serving
          </Typography>
        </Stack>
      </Stack>
      {/* body */}
      <Stack sx={{ flex: 1, p: 3, gap: 2.5, overflow: "auto" }}>
        <Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              mb: 1,
            }}
          >
            Modelled surfaces
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {(twin?.depth || []).map((d) => (
              <Typography
                key={d}
                sx={{
                  px: 1,
                  py: 0.375,
                  borderRadius: "4px",
                  typography: "s2",
                  fontWeight: 600,
                  bgcolor: "background.neutral",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                {d}
              </Typography>
            ))}
          </Stack>
        </Box>
        <Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              mb: 1,
            }}
          >
            How to inspect state
          </Typography>
          <Stack spacing={0.75}>
            <InspectRow
              icon="solar:code-square-linear"
              title="OpenAPI viewer"
              body="Above · every endpoint the sandbox serves, with request/response shapes."
            />
            <InspectRow
              icon="solar:list-linear"
              title="Raw requests tab"
              body="On any run drawer · chronological HTTP log with request + response payloads."
            />
            <InspectRow
              icon="solar:pulse-2-linear"
              title="Sandbox state timeline"
              body="On any run drawer · semantic view of what the agent wrote to this sandbox."
            />
          </Stack>
        </Box>
        <Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              mb: 1,
            }}
          >
            Activity this session
          </Typography>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {requests} request{requests === 1 ? "" : "s"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Resets when you reset the sandbox state.
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
ApiOnlySandboxPanel.propTypes = {
  twin: PropTypes.object,
  endpoint: PropTypes.string,
  envState: PropTypes.object,
};

function InspectRow({ icon, title, body }) {
  return (
    <Stack
      direction="row"
      spacing={1.25}
      alignItems="flex-start"
      sx={{ py: 0.5 }}
    >
      <Iconify
        icon={icon}
        width={14}
        sx={{ color: "primary.main", mt: "3px", flexShrink: 0 }}
      />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {body}
        </Typography>
      </Box>
    </Stack>
  );
}
InspectRow.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.string,
};

/*
  Recent-activity ribbon that sits between the sub-header and the
  sandbox mock. Reads the run history and reports what the agent
  actually wrote to this twin during the session — "3 writes this
  session · last: Agent posted to #support-urgent". Gives the
  sandbox a live-state feel without every mock needing bespoke
  render code for what the agent did.
*/
function SandboxActivityRibbon({ envState, serviceId }) {
  const items = liveSandboxContentFor(envState, serviceId);
  if (!items.length) return null;
  const last = items[0];
  const location =
    last.channel || last.page || last.thread || last.record || "sandbox";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        mb: 1.25,
        px: 1.25,
        py: 0.75,
        borderRadius: "6px",
        border: (t) => `1px solid ${t.palette.status.discovery.border}`,
        bgcolor: "status.discovery.bg",
      }}
    >
      <Iconify
        icon="solar:pulse-2-linear"
        width={14}
        sx={{ color: "primary.main", flexShrink: 0 }}
      />
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "primary.main",
          letterSpacing: 0.3,
          flexShrink: 0,
        }}
      >
        {items.length} write{items.length === 1 ? "" : "s"} this session
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.tertiary", flex: 1, minWidth: 0 }}
      >
        · Latest: {last.author} — {location}
      </Typography>
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
      >
        {last.runLabel}
      </Typography>
    </Stack>
  );
}
SandboxActivityRibbon.propTypes = {
  envState: PropTypes.object,
  serviceId: PropTypes.string,
};

/*
  Getting-started checklist for a freshly minted env (usually the
  scratch flow, but also legacy envs where no agent was ever wired).
  Each row is one concrete step the user takes to move the env from
  "created" to "first run": connect an agent, add scenarios, add
  evals, run. Done rows show a green tick + strike; the current
  actionable row gets a prominent button. Reads as an onboarding
  card and fills the vertical space the empty capabilities sections
  would otherwise leave grey.
*/
function NextStepsChecklist({ env, envState, onGo }) {
  const scenarioCount = envState?.scenarios?.length || 0;
  const evalCount = envState?.evals?.length || 0;
  const runCount = envState?.runs?.length || 0;

  const steps = [
    {
      id: "created",
      title: "Environment created",
      body: `${env.name} is set up and ready for configuration.`,
      done: true,
      action: null,
    },
    {
      id: "agent",
      title: "Connect an agent",
      body: "Wire your agent so it can act against the environment. This flow lets you connect one after the fact.",
      done: false,
      cta: "Connect agent",
      onClick: () => onGo?.("agent"),
      icon: "solar:link-circle-linear",
    },
    {
      id: "scenarios",
      title: "Add scenarios",
      body: "Tasks the agent has to complete. Add a few concrete cases the run will grade against.",
      done: scenarioCount > 0,
      cta:
        scenarioCount > 0
          ? `${scenarioCount} added — add more`
          : "Add scenarios",
      onClick: () => onGo?.("scenarios"),
      icon: "solar:list-check-linear",
    },
    {
      id: "evals",
      title: "Add evaluations",
      body: "Graders that decide whether each run passed. Pick from the library or author your own.",
      done: evalCount > 0,
      cta: evalCount > 0 ? `${evalCount} added — add more` : "Add evaluations",
      onClick: () => onGo?.("evals"),
      icon: "solar:target-linear",
    },
    {
      id: "run",
      title: "Run your first simulation",
      /* This list only shows while no agent is connected, and a run needs one —
         so the step says that instead of offering a button that can't work. */
      body: "Needs a connected agent. Then Run simulation in the header starts it — transcripts, tool calls and eval verdicts land as it runs.",
      done: runCount > 0,
      cta: null,
      icon: "solar:play-circle-linear",
    },
  ];

  const doneCount = steps.filter((s) => s.done).length;
  const currentIdx = steps.findIndex((s) => !s.done);

  return (
    <SectionCard
      dense
      title="Next steps"
      subtitle={`${doneCount} of ${steps.length} complete — ${steps.length - doneCount} left to run your first simulation`}
      sx={{ mb: 3 }}
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {steps.map((s, i) => {
          const isCurrent = i === currentIdx;
          return (
            <Stack
              key={s.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{
                px: 2.5,
                py: 1.75,
                bgcolor: isCurrent ? "action.hover" : "transparent",
              }}
            >
              <Box
                sx={{
                  width: 26,
                  height: 26,
                  borderRadius: "50%",
                  flexShrink: 0,
                  display: "grid",
                  placeItems: "center",
                  bgcolor: s.done ? "status.success.bg" : "action.selected",
                  color: s.done ? "status.success.fg" : "text.tertiary",
                }}
              >
                <Iconify
                  icon={
                    s.done
                      ? "solar:check-circle-bold"
                      : s.icon || "solar:circle-linear"
                  }
                  width={14}
                />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    color: s.done ? "text.tertiary" : "text.primary",
                    textDecoration: s.done ? "line-through" : "none",
                  }}
                >
                  {s.title}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {s.body}
                </Typography>
              </Box>
              {!s.done && s.cta && (
                <Button
                  variant={isCurrent ? "contained" : "outlined"}
                  color="primary"
                  size="small"
                  disabled={s.disabled}
                  onClick={s.onClick}
                  sx={{
                    flexShrink: 0,
                    ...(isCurrent
                      ? {}
                      : {
                          color: "text.primary",
                          borderColor: "divider",
                          "&:hover": { borderColor: "text.disabled" },
                        }),
                  }}
                >
                  {s.cta}
                </Button>
              )}
            </Stack>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
NextStepsChecklist.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onGo: PropTypes.func,
};

/* ─── Source → sandbox mapping ─────────────────────────────────────────────
 *
 * A two-column ledger: what was read from source, and what it became in
 * the sandbox. Rows sit on a CSS grid so every origin chip lines up in
 * one vertical band and every sandbox target aligns to the same left
 * edge — the alignment IS the readability.
 * ─────────────────────────────────────────────────────────────────────── */

/* Shared grid template so column headers, group headers and every row
   land on the same tracks. Using min-content for the chip column means
   all chips right-align to their widest peer instead of floating
   wherever a tool name happens to end. */
const MAP_GRID = "minmax(0, 1fr) min-content 24px minmax(0, 1fr)";
const MAP_PX = 2;

function SourceToSandboxMap({ env, envState, progress }) {
  /*
    While the build is still reading the agent, the table is the contract
    being written: tools appear as their signatures are read, then rules as
    they're separated from the prompt, then the stores the world is seeded
    with — each group counting what it has found so far. Once the build is
    done it's the full record, as before.
  */
  const streaming = !!progress?.streaming;
  const allTools = env?.tools || [];
  const allRules = provenanceFor(env).rules;
  const allTables = env?.seed?.tables || [];
  const tools = streaming ? allTools.slice(0, progress.tools || 0) : allTools;
  const ruleProv = streaming
    ? allRules.slice(0, progress.rules || 0)
    : allRules;
  const tables = streaming
    ? allTables.slice(0, progress.stores || 0)
    : allTables;
  /* A group's count line: how far along it is while reading, what it holds after. */
  const found = (shown, total, done) =>
    streaming && shown < total ? `${shown} of ${total} found` : done;

  /*
    Each tool's row reads its tier off the fidelity dial (PRD v6 Story 5) —
    the default from its side-effect class, or what someone set on the World
    tab. A connect-only agent's tools are answered by its own systems.
  */
  const connectOnly = isConnectOnly(env);
  const targetForTier = (tier) =>
    ({
      stub: "stub · a canned response",
      replay: "replay · a recorded response",
      stateful: "stateful · reads and writes the world",
      live: "live · the real tool, sandboxed",
    })[tier] || "stateful · reads and writes the world";

  /*
    Every tool renders with its tier — the side-effect default fills in the
    common case; a choice on the World tab wins. The "Needs your answer"
    inline card that used to live below one row was homework the reader
    shouldn't be handing back to the user in the build flow; the dial now
    lives on the World tab's tools card, next to the code.
  */
  const toolRows = tools.map((t) => {
    const setting = toolSettingOf(t, envState);
    return {
      key: t.name,
      name: t.name,
      /* A tool added from an MCP server says so — it wasn't read from the agent's config. */
      origin: t.via?.kind === "mcp" ? "mcp" : "config",
      via: t.via,
      target: connectOnly
        ? "answered by your systems"
        : targetForTier(setting.tier),
      confirmed: !connectOnly && setting.confirmed,
    };
  });

  /* Built from a description: nothing has been read from an agent yet, so
     every row says where it really came from — or that it's still to come. */
  const fromDescription = env?.builtFrom?.kind === "scratch";
  /* Tools that came from an MCP server, not from reading the agent. */
  const viaMcp = allTools.filter((t) => t.via?.kind === "mcp").length;
  const surface = getSurface(env.surface);
  const actors = [
    {
      key: "transfer",
      name: "transfer target",
      origin: "prompt",
      target: "Supervisor persona",
      mapped: true,
    },
    {
      key: "external",
      name: tools[0]?.name || "external_service",
      origin: "callGraph",
      target: `${env?.domain || "Backend"} service`,
      mapped: true,
    },
    {
      key: "caller",
      name: null,
      origin: null,
      target: `Caller — ${surface.blurb.split(" ")[0] || "the user"}`,
      mapped: false,
    },
    { key: "clock", name: null, origin: null, target: "Clock", mapped: false },
  ];
  const actorMapped = actors.filter((a) => a.mapped).length;

  return (
    <Box
      sx={{
        mb: 3,
        borderRadius: "8px",
        overflow: "hidden",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      {/* Title band */}
      <Box
        sx={{
          px: MAP_PX,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {streaming ? "Writing the contract" : "How the world was built"}
          </Typography>
          {streaming && (
            <CircularProgress
              size={12}
              thickness={5}
              sx={{ color: "text.disabled" }}
            />
          )}
        </Stack>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            mt: 0.25,
            maxWidth: 720,
          }}
        >
          {streaming
            ? "Filling in as the builder reads your agent — tools first, then the rules, then the data the world is seeded with. Each carries where it was read from."
            : "Every derived fact carries where it was read from and what it became in the sandbox."}
        </Typography>
      </Box>

      {/* Column headers, on the same tracks as rows */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: MAP_GRID,
          columnGap: 2,
          alignItems: "center",
          px: MAP_PX,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <ColHead>Read from source</ColHead>
        <Box />
        <Box />
        <ColHead>In the sandbox world</ColHead>
      </Box>

      {/* Groups */}
      <MapGroup
        label="Tools"
        hint={`${fromDescription ? (envState?.toolsRead ? `read from ${envState.toolsRead.from} when it was connected` : "read when your agent is connected") : "from the agent config & call-graph"}${viaMcp ? ` · ${viaMcp} added from MCP` : ""}`}
        right={found(tools.length, allTools.length, `${tools.length} mapped`)}
        first
      >
        {streaming && tools.length < allTools.length && (
          <ReadingRow what="tool signatures" />
        )}
        {toolRows.map((r, i) => (
          <MapRow
            key={r.key}
            index={i}
            name={r.name}
            origin={r.origin}
            target={r.target}
            mono
            trailing={r.confirmed && <ConfirmedChip />}
          />
        ))}
      </MapGroup>

      <MapGroup
        label="Rules"
        hint={
          fromDescription
            ? "from your description"
            : "from policy.yaml, enforced"
        }
        right={found(
          ruleProv.length,
          allRules.length,
          `${ruleProv.length} mapped`,
        )}
      >
        {streaming && ruleProv.length < allRules.length && (
          <ReadingRow
            what={
              tools.length < allTools.length
                ? "after the tools"
                : "rules from the code and prompt"
            }
            waiting={tools.length < allTools.length}
          />
        )}
        {ruleProv.map((r, i) => (
          <MapRow
            key={r.id}
            index={i}
            name={r.subject}
            origin={
              fromDescription
                ? "description"
                : r.origin === "code"
                  ? "policy"
                  : r.origin
            }
            target={ruleTarget(r.subject, r.origin, connectOnly)}
          />
        ))}
      </MapGroup>

      <MapGroup
        label="Stores"
        hint={
          fromDescription
            ? "none yet — the world starts with no data"
            : "from your fixtures"
        }
        right={found(
          tables.length,
          allTables.length,
          `${tables.length} mapped`,
        )}
      >
        {streaming && tables.length < allTables.length && (
          <ReadingRow
            what={
              ruleProv.length < allRules.length
                ? "once the rules are in"
                : "the data the world is seeded with"
            }
            waiting={ruleProv.length < allRules.length}
          />
        )}
        {tables.map((t, i) => (
          <MapRow
            key={t.name}
            index={i}
            name={`${t.name} · ${t.rows.toLocaleString()}`}
            origin="fixture"
            target={`${t.name} (${t.rows.toLocaleString()})`}
            mono
          />
        ))}
      </MapGroup>

      {!streaming && !fromDescription && (
        <MapGroup
          label="Other parties"
          hint="from the call-graph & prompt"
          right={`${actorMapped} mapped · ${actors.length - actorMapped} derived`}
        >
          {actors.map((a, i) => (
            <MapRow
              key={a.key}
              index={i}
              name={a.name}
              origin={a.origin}
              target={a.target}
              mono={!!a.name}
              derived={!a.mapped}
            />
          ))}
        </MapGroup>
      )}
    </Box>
  );
}
SourceToSandboxMap.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  progress: PropTypes.object,
};

/* The row a group shows while its items are still being read — or, for a
   group whose turn hasn't come, what it's waiting on. */
function ReadingRow({ what, waiting }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: MAP_PX, py: 0.75 }}
    >
      {waiting ? (
        <Iconify
          icon="solar:clock-circle-linear"
          width={14}
          sx={{ color: "text.disabled" }}
        />
      ) : (
        <CircularProgress
          size={11}
          thickness={5}
          sx={{ color: "text.disabled" }}
        />
      )}
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        {waiting ? `Next — ${what}` : `Reading ${what}…`}
      </Typography>
    </Stack>
  );
}
ReadingRow.propTypes = { what: PropTypes.string, waiting: PropTypes.bool };

function ColHead({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 500,
        textTransform: "none",
        color: "text.tertiary",
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node };

function ConfirmedChip() {
  return (
    <Chip
      size="small"
      label="you confirmed"
      sx={{
        color: "primary.main",
        bgcolor: "status.discovery.bg",
      }}
    />
  );
}

function ruleTarget(subject, origin, live = false) {
  if (origin === "doc") return "held for review · click to accept";
  /* A live environment has no sandbox: the agent's tools reach its real
     systems, so a rule is judged from the conversation, not enforced. */
  if (live) return "judged from the conversation";
  const s = subject.toLowerCase();
  if (s.includes("double") || s.includes("overlap"))
    return "blocks overlapping slots";
  if (s.includes("hour") || s.includes("time"))
    return "blocks off-hours attempts";
  if (s.includes("cancel") || s.includes("window") || s.includes("return"))
    return "blocks late cancels";
  if (
    s.includes("refund") ||
    s.includes("supervisor") ||
    s.includes("approval")
  )
    return "requires supervisor approval";
  if (s.includes("escalat")) return "routes to human handoff";
  if (s.includes("disclose") || s.includes("identity") || s.includes("promise"))
    return "enforced at the sandbox boundary";
  return "enforced at the sandbox boundary";
}

function MapGroup({ label, hint, right, children, first }) {
  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) auto",
          columnGap: 2,
          alignItems: "baseline",
          px: MAP_PX,
          pt: first ? 1.25 : 1.5,
          pb: 0.5,
          borderTop: first ? "none" : "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{ minWidth: 0 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {label}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {hint}
          </Typography>
        </Stack>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
          }}
        >
          {right}
        </Typography>
      </Box>
      <Box sx={{ pb: 0.5 }}>{children}</Box>
    </Box>
  );
}
MapGroup.propTypes = {
  label: PropTypes.string,
  hint: PropTypes.string,
  right: PropTypes.string,
  children: PropTypes.node,
  first: PropTypes.bool,
};

function MapRow({ index, name, origin, target, mono, derived, trailing }) {
  const rowBg = index % 2 === 1 ? "background.neutral" : "transparent";
  return (
    <Box sx={{ position: "relative", bgcolor: rowBg }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: MAP_GRID,
          columnGap: 2.5,
          alignItems: "center",
          minHeight: 32,
          pl: MAP_PX,
          pr: MAP_PX,
        }}
      >
        {name ? (
          <Typography
            noWrap
            sx={{
              // Row title: 12px regular (mono for code identifiers), never bold.
              fontSize: 12,
              lineHeight: "18px",
              fontWeight: 400,
              fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
              color: derived ? "text.tertiary" : "text.primary",
            }}
          >
            {name}
          </Typography>
        ) : (
          <Box />
        )}

        <Box sx={{ justifySelf: "end", display: "flex" }}>
          {origin && <OriginChip origin={origin} showPath={false} />}
        </Box>

        <Iconify
          icon="solar:arrow-right-linear"
          width={14}
          sx={{
            color: derived ? "text.disabled" : "text.tertiary",
            justifySelf: "center",
          }}
        />

        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          {target != null && (
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: derived ? "text.tertiary" : "text.primary",
                fontStyle: derived ? "italic" : "normal",
                minWidth: 0,
              }}
            >
              {target}
            </Typography>
          )}
          {trailing}
        </Stack>
      </Box>
    </Box>
  );
}
MapRow.propTypes = {
  index: PropTypes.number,
  name: PropTypes.node,
  origin: PropTypes.string,
  target: PropTypes.node,
  mono: PropTypes.bool,
  derived: PropTypes.bool,
  trailing: PropTypes.node,
};
