import PropTypes from "prop-types";
import { Box, Stack, Typography, Grid } from "@mui/material";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import {
  CONNECTOR_LABEL,
  DIRECTION_LABEL,
  ENV_SHAPE,
  ENV_STATE_SHAPE,
  MODE_LABEL,
  OVERVIEW_COPY,
  sourceLabel,
  worldTables,
} from "./overview.constants";
import { PARALLELISM_COPY, degradeReasonCopy } from "../../parallelism.constants";
// Manage-versions (the agent "test subject" card + its version drawer) is
// commented out below, to be picked up later — its imports go with it:
// import Iconify from "src/components/iconify";
// import SideDrawer from "../../components/SideDrawer";
// import AgentsPanel from "../agents/AgentsPanel";
// (AGENT_SUMMARY_COPY dropped from the overview.constants import with it)
import { GroupHeading, Fact } from "./OverviewPrimitives";
import AgentSummarySection from "./AgentSummarySection";
import SourceReadCard from "./SourceReadCard";
import AgentRefreshBanner from "./AgentRefreshBanner";
import NextStepsChecklist from "./NextStepsChecklist";
import StateSummary from "./StateSummary";
import { UseCasesCard, AmendmentsCard } from "./OverviewCards";
import { DependsOnCard, SeededDataCard } from "./WorldCards";

/**
 * What this environment is.
 *
 * The page answers three questions in order: what the agent is (its
 * capabilities), what world it acts on, and what decides whether it did well.
 * The facts that used to sit in a tall right-hand card are a strip under the
 * title instead — they are labels, not content.
 *
 * Non-twin path only — the twin-backed sections are not ported. The
 * "agent moved ahead" refresh banner (unlocked envs), the getting-started
 * checklist (envs with no agent and no derived world yet) and the
 * source-to-sandbox map (envs with a derived world) render at the designer's
 * positions. An unlocked env's "Manage versions" opens the AgentsPanel in a
 * side drawer overlaid on this tab (the user never leaves Overview); a locked
 * template env keeps "Fork to edit" instead.
 */
// Map a real §6 contract.dependency ({name, kind, what, used_by}) to the shape
// DependsOnCard renders ({name, kind, provides, usedBy}).
const dependsFromContract = (dependencies) =>
  (Array.isArray(dependencies) ? dependencies : []).map((d) => ({
    name: d?.name,
    kind: d?.kind,
    provides: d?.what,
    usedBy: d?.used_by || [],
  }));

export default function OverviewPanel({ env, envState, patch, onGo, agentConnected, locked = false, buildMode, counts, latestRun, backedWorld }) {
  // Manage-versions drawer state — commented with the card + drawer below,
  // to be picked up later.
  // const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);
  // const [nestedAgentDrawer, setNestedAgentDrawer] = useState(false);
  const surface = getSurface(env.surface);
  const contract = contractFor(env);
  // The real connector for a backed env (envState.agent.typeId = the job poll's
  // detected connector, e.g. "livekit"). No transports field is served, so this
  // replaces the old hardcoded transports fact.
  const connector = envState?.agent?.typeId;
  const connectorLabel = connector ? CONNECTOR_LABEL[connector] || connector : "-";

  const parallelism = env.parallelism;
  const degradeReasons = parallelism?.degrade_reasons || [];

  const hasDerivedWorld = (env.rules?.length || 0) > 0
    || (envState?.scenarios?.length || 0) > 0
    || (env.tools?.length || 0) > 0;

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-start" }} spacing={2}>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 820 }}>
          {env.description}
        </Typography>
      </Stack>

      {/* Labels, not content — a strip rather than a column of its own. */}
      <Stack
        direction="row"
        flexWrap="wrap"
        divider={<Box sx={{ width: "1px", bgcolor: "divider", mx: 2 }} />}
        sx={{ my: 2, py: 1.25, px: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, rowGap: 1, width: "fit-content", maxWidth: "100%" }}
      >
        <Fact label={OVERVIEW_COPY.facts.channel} value={surface?.label ?? "-"} />
        <Fact label={OVERVIEW_COPY.facts.connector} value={connectorLabel} />
        {/* Domain, direction, source and mode are real §6 fields; a client-only
            env (template, fork) carries sample values for them, so they show
            for a backed env only. */}
        {backedWorld && env.domain ? <Fact label={OVERVIEW_COPY.facts.domain} value={env.domain} /> : null}
        {/* A call has a direction; a chat doesn't, whatever the contract says. */}
        {backedWorld && surface?.id === "voice" && env.callDirection ? (
          <Fact
            label={OVERVIEW_COPY.facts.direction}
            value={DIRECTION_LABEL[env.callDirection] || env.callDirection}
          />
        ) : null}
        {backedWorld && sourceLabel(env.source) ? (
          <Fact label={OVERVIEW_COPY.facts.source} value={sourceLabel(env.source)} />
        ) : null}
        {backedWorld && env.agentMode ? (
          <Fact label={OVERVIEW_COPY.facts.mode} value={MODE_LABEL[env.agentMode] || env.agentMode} />
        ) : null}
        {parallelism?.requested ? (
          <Fact label={PARALLELISM_COPY.fact} value={PARALLELISM_COPY.factValue(parallelism)} />
        ) : null}
      </Stack>

      {degradeReasons.length > 0 && (
        <Box sx={{ mb: 2, py: 1.25, px: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.neutral" }}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
            {PARALLELISM_COPY.degradedTitle(parallelism.effective, parallelism.requested)}
          </Typography>
          {degradeReasons.map((reason) => (
            <Typography key={reason} sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
              {degradeReasonCopy(reason, parallelism.effective)}
            </Typography>
          ))}
        </Box>
      )}

      <AgentSummarySection subject={env.testSubject} />

      {/* Optional re-derive prompt when the attached agent has moved ahead of
          the world; only an unlocked env can re-derive. */}
      {!locked && <AgentRefreshBanner env={env} envState={envState} patch={patch} />}

      {/* State-of-the-env summary tiles + latest run — the "how's this env doing
          right now?" answer. Each tile jumps to the tab it summarises. For a
          backed env the numbers come from §6 (summaryCounts). */}
      <StateSummary env={env} envState={envState} counts={counts} latestRun={latestRun} onGo={onGo} />

      {/* Getting-started checklist for envs that haven't been seeded yet. A
          scratch env arrives carrying a derived world, so the same checklist
          would render every step pre-complete — skip it there. */}
      {!agentConnected && !hasDerivedWorld && (
        <NextStepsChecklist env={env} envState={envState} onGo={onGo} />
      )}

      {/* CapabilityGraph moved to the Contract tab — that's where the shape of
          the environment belongs. Overview keeps the summary, not the definition. */}
      <GroupHeading>{OVERVIEW_COPY.capabilities}</GroupHeading>

      <SourceReadCard tools={env.tools} rules={env.rules} />

      {/* Tools and Hard rules cards were here — removed as duplicates of the
          Contract tab, which owns the tool inventory and the hard-rule list. */}
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          {/* A backed env reads only the real §6 fields; a use case the build
              didn't write stays empty rather than being made up. The fixture
              derivation is for client-only envs (templates, forks). */}
          <UseCasesCard useCases={backedWorld ? env.useCases ?? [] : env.useCases ?? contract.useCases} />
        </Grid>
        <Grid item xs={12} md={5}>
          {!buildMode && (
            <AmendmentsCard
              amendments={backedWorld ? backedWorld.amendments : (env.amendments ?? contract.amendments)}
            />
          )}
        </Grid>
      </Grid>

      {/* The world the agent works in: the seeded tables (real §6
          world.stores, shown only when there are any) and what it depends on
          (real §6 contract.dependencies). */}
      <GroupHeading>{OVERVIEW_COPY.world}</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <DependsOnCard
            dependsOn={backedWorld ? dependsFromContract(backedWorld.dependencies) : contract.dependsOn}
          />
        </Grid>
        {backedWorld && worldTables(env).length > 0 && (
          <Grid item xs={12} md={5}>
            <SeededDataCard env={env} />
          </Grid>
        )}
      </Grid>

      {/* Agent version-management drawer — commented out with the "Manage
          versions" card above, to be picked up later. When revived, restore the
          imports (Iconify, SideDrawer, AgentsPanel, AGENT_SUMMARY_COPY), the
          agentDrawerOpen / nestedAgentDrawer state, and the shared SideDrawer
          that mounted a version-history header over the AgentsPanel for an
          unlocked env. Full markup is in git history for this file. */}
    </Box>
  );
}

OverviewPanel.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  agentConnected: PropTypes.bool,
  locked: PropTypes.bool,
  buildMode: PropTypes.bool,
  // Real §6 summary counts for a backed env (from EnvironmentWorkspace); the
  // client-store fallback in StateSummary covers non-backed envs.
  counts: PropTypes.object,
  // The newest run from the executions list, for the latest-run card.
  latestRun: PropTypes.object,
  // Real §6 world content for a backed env: { stores, amendments, dependencies }.
  // Undefined for a non-backed env (keeps the client/fixture source).
  backedWorld: PropTypes.shape({
    stores: PropTypes.array,
    amendments: PropTypes.array,
    dependencies: PropTypes.array,
  }),
};
