import PropTypes from "prop-types";
import { Box, Stack, Typography, Grid } from "@mui/material";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import { ENV_SHAPE, ENV_STATE_SHAPE, OVERVIEW_COPY } from "./overview.constants";
import { PARALLELISM_COPY, degradeReasonCopy } from "../../parallelism.constants";
// Manage-versions (the agent "test subject" card + its version drawer) is
// commented out below, to be picked up later — its imports go with it:
// import Iconify from "src/components/iconify";
// import SideDrawer from "../../components/SideDrawer";
// import AgentsPanel from "../agents/AgentsPanel";
// import AgentSummarySection from "./AgentSummarySection";
// (AGENT_SUMMARY_COPY dropped from the overview.constants import with it)
import { GroupHeading, Fact } from "./OverviewPrimitives";
import AgentRefreshBanner from "./AgentRefreshBanner";
import NextStepsChecklist from "./NextStepsChecklist";
import StateSummary from "./StateSummary";
import { UseCasesCard, AmendmentsCard } from "./OverviewCards";
import { DependsOnCard } from "./WorldCards";

// Display names for the real connector (§1/§6 `settings.agent.connector`,
// surfaced here as `envState.agent.typeId` — the job poll's detected connector).
// Falls back to the raw value for anything unmapped.
const CONNECTOR_LABEL = {
  livekit: "LiveKit",
  vapi: "Vapi",
  retell: "Retell",
  retell_chat: "Retell (chat)",
  phone: "Phone",
  auto: "Auto",
};

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

export default function OverviewPanel({ env, envState, patch, onGo, agentConnected, locked = false, buildMode, counts, backedWorld }) {
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
  const connectorLabel = connector ? CONNECTOR_LABEL[connector] || connector : "—";

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
        divider={<Box sx={{ width: "1px", bgcolor: "divider", alignSelf: "stretch", mx: 2 }} />}
        sx={{ my: 2, py: 1.25, px: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, rowGap: 1 }}
      >
        <Fact label={OVERVIEW_COPY.facts.channel} value={surface.label} />
        <Fact label={OVERVIEW_COPY.facts.connector} value={connectorLabel} />
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

      {/* Manage-versions container (agent "test subject" card + "Manage
          versions") — commented out, to be picked up later.
      <AgentSummarySection
        envState={envState}
        agentConnected={agentConnected}
        locked={locked}
        onManageVersions={() => setAgentDrawerOpen(true)}
      /> */}

      {/* Optional re-derive prompt when the attached agent has moved ahead of
          the world; only an unlocked env can re-derive. */}
      {!locked && <AgentRefreshBanner env={env} envState={envState} patch={patch} />}

      {/* State-of-the-env summary tiles + latest run — the "how's this env doing
          right now?" answer. Each tile jumps to the tab it summarises. For a
          backed env the numbers come from §6 (summaryCounts). */}
      <StateSummary env={env} envState={envState} counts={counts} onGo={onGo} />

      {/* Getting-started checklist for envs that haven't been seeded yet. A
          scratch env arrives carrying a derived world, so the same checklist
          would render every step pre-complete — skip it there. */}
      {!agentConnected && !hasDerivedWorld && (
        <NextStepsChecklist env={env} envState={envState} onGo={onGo} />
      )}

      {/* CapabilityGraph moved to the Contract tab — that's where the shape of
          the environment belongs. Overview keeps the summary, not the definition. */}
      <GroupHeading>{OVERVIEW_COPY.capabilities}</GroupHeading>

      {/* The source-to-sandbox ledger was removed: its origin chips and sandbox
          targets were derived by position and keyword, not reported by ALK. */}

      {/* Tools and Hard rules cards were here — removed as duplicates of the
          Contract tab, which owns the tool inventory and the hard-rule list. */}
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          {/* Prefer the real §6 fields (contract.real_use_cases / amendments)
              when the detail endpoint has authored them; fall back to the
              fixture derivation while it has not (or is disabled). */}
          <UseCasesCard useCases={env.useCases ?? contract.useCases} />
        </Grid>
        <Grid item xs={12} md={5}>
          {!buildMode && (
            <AmendmentsCard
              amendments={backedWorld ? backedWorld.amendments : (env.amendments ?? contract.amendments)}
            />
          )}
        </Grid>
      </Grid>

      {/* The Seeded-data card was removed — the stores now live only in the
          "How the world was built" map above (real §6 world.stores). "What it
          depends on" stays, driven by real §6 contract.dependencies. */}
      <GroupHeading>{OVERVIEW_COPY.world}</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <DependsOnCard
            dependsOn={backedWorld ? dependsFromContract(backedWorld.dependencies) : contract.dependsOn}
          />
        </Grid>
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
  // Real §6 world content for a backed env: { stores, amendments, dependencies }.
  // Undefined for a non-backed env (keeps the client/fixture source).
  backedWorld: PropTypes.shape({
    stores: PropTypes.array,
    amendments: PropTypes.array,
    dependencies: PropTypes.array,
  }),
};
