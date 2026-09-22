import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Grid } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface, getDomain } from "src/api/simulate-environments/_fixtures/surfaces";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import SideDrawer from "../../components/SideDrawer";
import AgentsPanel from "../agents/AgentsPanel";
import { ENV_SHAPE, ENV_STATE_SHAPE, OVERVIEW_COPY, AGENT_SUMMARY_COPY, effectiveEnv, packStatsFor } from "./overview.constants";
import { GroupHeading, Fact } from "./OverviewPrimitives";
import AgentSummarySection from "./AgentSummarySection";
import AgentRefreshBanner from "./AgentRefreshBanner";
import NextStepsChecklist from "./NextStepsChecklist";
import SourceToSandboxMap from "./SourceToSandboxMap";
import StateSummary from "./StateSummary";
import { ToolsCard, HardRulesCard, UseCasesCard, AmendmentsCard } from "./OverviewCards";
import { SeededDataCard, DependsOnCard } from "./WorldCards";

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
export default function OverviewPanel({ env, envState, patch, onGo, agentConnected, locked = false, buildMode }) {
  const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);
  // AgentsPanel opens its own "Add new version" drawer; while it is open the
  // outer drawer closes so the two don't stack. keepMounted preserves the
  // AgentsPanel state underneath.
  const [nestedAgentDrawer, setNestedAgentDrawer] = useState(false);
  const surface = getSurface(env.surface);
  const domain = getDomain(env.domain);
  const shown = effectiveEnv(env, envState);
  const stats = packStatsFor(shown);
  const contract = contractFor(env);

  const hasDerivedWorld = (env.rules?.length || 0) > 0
    || (envState?.scenarios?.length || 0) > 0
    || (env.tools?.length || 0) > 0;
  const showRichOverview = agentConnected || hasDerivedWorld;
  const scenarioCount = envState?.scenarios?.length || stats.scenarios;

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
        <Fact label={OVERVIEW_COPY.facts.domain} value={domain?.label || "—"} />
        <Fact label={OVERVIEW_COPY.facts.transports} value={surface.transports.join(" · ")} />
        <Fact label={OVERVIEW_COPY.facts.scenarioPacks} value={`${stats.packs} packs · ${scenarioCount} scenarios`} />
      </Stack>

      <AgentSummarySection
        envState={envState}
        agentConnected={agentConnected}
        locked={locked}
        onManageVersions={() => setAgentDrawerOpen(true)}
      />

      {/* Optional re-derive prompt when the attached agent has moved ahead of
          the world; only an unlocked env can re-derive. */}
      {!locked && <AgentRefreshBanner env={env} envState={envState} patch={patch} />}

      {/* State-of-the-env summary tiles + latest run — the "how's this env doing
          right now?" answer. Each tile jumps to the tab it summarises. */}
      <StateSummary env={env} envState={envState} onGo={onGo} />

      {/* Getting-started checklist for envs that haven't been seeded yet. A
          scratch env arrives carrying a derived world, so the same checklist
          would render every step pre-complete — skip it there. */}
      {!agentConnected && !hasDerivedWorld && (
        <NextStepsChecklist env={env} envState={envState} onGo={onGo} />
      )}

      {/* CapabilityGraph moved to the Contract tab — that's where the shape of
          the environment belongs. Overview keeps the summary, not the definition. */}
      <GroupHeading>{OVERVIEW_COPY.capabilities}</GroupHeading>

      {/* The reviewability record: every derived fact with its origin and its
          sandbox target, side by side, with unresolved rows carrying an inline
          resolve control. */}
      {showRichOverview && <SourceToSandboxMap env={env} envState={envState} patch={patch} />}

      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <ToolsCard env={env} agentConnected={agentConnected} />
        </Grid>
        <Grid item xs={12} md={5}>
          <HardRulesCard env={env} />
        </Grid>
        <Grid item xs={12} md={7}>
          {/* Prefer the real §6 fields (contract.real_use_cases / amendments)
              when the detail endpoint has authored them; fall back to the
              fixture derivation while it has not (or is disabled). */}
          <UseCasesCard useCases={env.useCases ?? contract.useCases} />
        </Grid>
        <Grid item xs={12} md={5}>
          {!buildMode && (
            <AmendmentsCard amendments={env.amendments ?? contract.amendments} />
          )}
        </Grid>
      </Grid>

      <GroupHeading>{OVERVIEW_COPY.world}</GroupHeading>
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <SeededDataCard env={env} />
        </Grid>
        <Grid item xs={12} md={5}>
          <DependsOnCard dependsOn={contract.dependsOn} />
        </Grid>
      </Grid>

      {/*
        Agent version-management drawer. Uses the shared SideDrawer so it matches
        every other drawer in this feature (transparent backdrop, background.paper
        surface, subtle shadow) and overlays Overview so the user never leaves the
        tab. While AgentsPanel's own "Add new version" drawer is open this outer
        one closes (keepMounted preserves its state) so the two never stack.
      */}
      {/* Only unlocked envs manage versions; a locked template never mounts it. */}
      {!locked && (
        <SideDrawer
          open={agentDrawerOpen && !nestedAgentDrawer}
          onClose={() => setAgentDrawerOpen(false)}
          width={{ xs: "100%", sm: 720, md: 880 }}
          keepMounted
        >
          <Stack sx={{ height: "100%", minHeight: 0 }}>
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ px: 2.5, py: 1.5, pr: 6, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
            >
              <Iconify icon="solar:cpu-bolt-linear" width={18} sx={{ color: "text.secondary" }} />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>
                  {AGENT_SUMMARY_COPY.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {OVERVIEW_COPY.manageVersionsSubtitle}
                </Typography>
              </Box>
            </Stack>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <AgentsPanel envState={envState} patch={patch} onNestedDrawerChange={setNestedAgentDrawer} />
            </Box>
          </Stack>
        </SideDrawer>
      )}
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
};
