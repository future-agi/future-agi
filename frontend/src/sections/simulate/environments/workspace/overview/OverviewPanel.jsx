import PropTypes from "prop-types";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { useState } from "react";
import { Box, Stack, Tooltip, Typography, Grid } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import {
  AGENT_SUMMARY_COPY,
  CONNECTOR_LABEL,
  DIRECTION_LABEL,
  ENV_SHAPE,
  ENV_STATE_SHAPE,
  MODE_LABEL,
  OVERVIEW_COPY,
  sourceLabel,
  worldTables,
} from "./overview.constants";
import {
  PARALLELISM_COPY,
  degradeReasonCopy,
} from "../../parallelism.constants";
import SideDrawer from "../../components/SideDrawer";
import AgentVersionsPanel from "../agents/AgentVersionsPanel";
import { GroupHeading, Fact } from "./OverviewPrimitives";
import AgentSummarySection from "./AgentSummarySection";
import { isCodeSource } from "./agentLine";
import SourceReadCard from "./SourceReadCard";
import SourceToSandboxMap from "./SourceToSandboxMap";
import NextStepsChecklist from "./NextStepsChecklist";
import StateSummary from "./StateSummary";
import { UseCasesCard, AmendmentsCard } from "./OverviewCards";
import { DependsOnCard, SeededDataCard, StartingDataCard } from "./WorldCards";
import ConnectOnlyWorld from "../world/ConnectOnlyWorld";
import { hasNoWorld } from "../world/world.constants";
import useApprovedDesign from "./useApprovedDesign";

/**
 * What this environment is.
 *
 * The page answers three questions in order: what the agent is (its
 * capabilities), what world it acts on, and what decides whether it did well.
 * The facts that used to sit in a tall right-hand card are a strip under the
 * title instead — they are labels, not content.
 *
 * Non-twin path only — the twin-backed sections are not ported. The
 * getting-started checklist (envs with no agent and no derived world yet) and the
 * source-to-sandbox map (envs with a derived world) render at the designer's
 * positions. An unlocked env's "Manage versions" opens its agent versions in a
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

export default function OverviewPanel({
  env: builtEnv,
  envState,
  onGo,
  agentConnected,
  locked = false,
  buildMode,
  counts: builtCounts,
  backedWorld,
}) {
  const { env, fromDesign } = useApprovedDesign(builtEnv);
  const counts = fromDesign
    ? { ...builtCounts, hardRules: env.rules.length }
    : builtCounts;
  const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);
  // Agent versions are managed on real data, so only while versioning is on.
  const canManageVersions = !locked && Boolean(env.versioning?.enabled);
  const surface = getSurface(env.surface);
  const contract = contractFor(env);
  // The real connector for a backed env (envState.agent.typeId = the job poll's
  // detected connector, e.g. "livekit"). No transports field is served, so this
  // replaces the old hardcoded transports fact.
  const connector = envState?.agent?.typeId;
  const connectorLabel = connector
    ? CONNECTOR_LABEL[connector] || connector
    : "-";

  const parallelism = env.parallelism;
  const degradeReasons = parallelism?.degrade_reasons || [];

  const seeded = Boolean(backedWorld) && worldTables(env).length > 0;
  const startingData = Boolean(backedWorld) && env.baseEnvironment != null;

  const amendments =
    (backedWorld
      ? backedWorld.amendments
      : env.amendments ?? contract.amendments) || [];
  const showAmendments = !buildMode && amendments.length > 0;
  const useCaseCount = (env.useCases || []).length;
  const scenarioTotal = counts?.scenarios;
  const scenarioPacks =
    useCaseCount && scenarioTotal != null
      ? OVERVIEW_COPY.scenarioPacks(useCaseCount, scenarioTotal)
      : null;

  const hasDerivedWorld =
    (env.rules?.length || 0) > 0 ||
    (counts?.scenarios ?? (envState?.scenarios?.length || 0)) > 0 ||
    (env.tools?.length || 0) > 0;

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "flex-start" }}
        spacing={2}
      >
        <OverflowTooltip title={env.description || ""}>
          <Typography
            noWrap
            sx={{
              typography: "s1",
              color: "text.secondary",
              minWidth: 0,
              flex: 1,
            }}
          >
            {env.description}
          </Typography>
        </OverflowTooltip>
      </Stack>

      {/* Labels, not content — a strip rather than a column of its own. */}
      <Stack
        direction="row"
        flexWrap="wrap"
        divider={<Box sx={{ width: "1px", bgcolor: "divider", mx: 2 }} />}
        sx={{
          my: 2,
          py: 1.25,
          px: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          rowGap: 1,
        }}
      >
        <Fact
          label={OVERVIEW_COPY.facts.channel}
          value={surface?.label ?? "-"}
        />
        {!isCodeSource(env.source) && (
          <Fact label={OVERVIEW_COPY.facts.connector} value={connectorLabel} />
        )}
        {/* Domain, direction, source and mode are real §6 fields; a client-only
            env (template, fork) carries sample values for them, so they show
            for a backed env only. */}
        {backedWorld && env.domain ? (
          <Fact label={OVERVIEW_COPY.facts.domain} value={env.domain} />
        ) : null}
        {/* A call has a direction; a chat doesn't, whatever the contract says. */}
        {backedWorld && surface?.id === "voice" && env.callDirection ? (
          <Fact
            label={OVERVIEW_COPY.facts.direction}
            value={DIRECTION_LABEL[env.callDirection] || env.callDirection}
          />
        ) : null}
        {backedWorld && sourceLabel(env.source) ? (
          <Fact
            label={OVERVIEW_COPY.facts.source}
            value={sourceLabel(env.source)}
          />
        ) : null}
        {backedWorld && env.agentMode ? (
          <Fact
            label={OVERVIEW_COPY.facts.mode}
            value={MODE_LABEL[env.agentMode] || env.agentMode}
          />
        ) : null}
        {backedWorld && scenarioPacks ? (
          <Fact
            label={OVERVIEW_COPY.facts.scenarioPacks}
            value={scenarioPacks}
          />
        ) : null}
        {parallelism?.requested ? (
          <Fact
            label={PARALLELISM_COPY.fact}
            value={PARALLELISM_COPY.factValue(parallelism)}
          />
        ) : null}
      </Stack>

      {degradeReasons.length > 0 && (
        <Box
          sx={{
            mb: 2,
            py: 1.25,
            px: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.neutral",
          }}
        >
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>
            {PARALLELISM_COPY.degradedTitle(
              parallelism.effective,
              parallelism.requested,
            )}
          </Typography>
          {degradeReasons.map((reason) => (
            <Typography
              key={reason}
              sx={{ typography: "s1", color: "text.secondary", mt: 0.5 }}
            >
              {degradeReasonCopy(reason, parallelism.effective)}
            </Typography>
          ))}
        </Box>
      )}

      <AgentSummarySection
        subject={env.testSubject}
        source={env.source}
        connector={connector}
        policy={env.agentPolicy}
        noWorld={Boolean(backedWorld) && hasNoWorld(env)}
        systemPrompt={env.systemPrompt}
        systemPromptOrigin={env.systemPromptOrigin}
        onManageVersions={
          canManageVersions ? () => setAgentDrawerOpen(true) : undefined
        }
      />

      {/* State-of-the-env summary tiles + latest run — the "how's this env doing
          right now?" answer. Each tile jumps to the tab it summarises. For a
          backed env the numbers come from §6 (summaryCounts). */}
      <StateSummary env={env} envState={envState} counts={counts} onGo={onGo} />

      {/* Getting-started checklist for envs that haven't been seeded yet. A
          scratch env arrives carrying a derived world, so the same checklist
          would render every step pre-complete — skip it there. */}
      {!agentConnected && !hasDerivedWorld && (
        <NextStepsChecklist
          env={env}
          envState={envState}
          onGo={onGo}
          scenarioCount={counts?.scenarios}
        />
      )}

      <GroupHeading>{OVERVIEW_COPY.capabilities}</GroupHeading>

      {backedWorld ? (
        <SourceToSandboxMap
          env={env}
          envState={envState}
          stores={env.stores || []}
          backed
        />
      ) : (
        <SourceReadCard tools={env.tools} rules={env.rules} />
      )}

      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        <Grid item xs={12} md={showAmendments ? 7 : 12}>
          {/* A backed env reads only the real §6 fields; a use case the build
              didn't write stays empty rather than being made up. The fixture
              derivation is for client-only envs (templates, forks). */}
          <UseCasesCard
            useCases={
              backedWorld
                ? env.useCases ?? []
                : env.useCases ?? contract.useCases
            }
          />
        </Grid>
        {showAmendments && (
          <Grid item xs={12} md={5}>
            <AmendmentsCard amendments={amendments} />
          </Grid>
        )}
      </Grid>

      <GroupHeading>{OVERVIEW_COPY.world}</GroupHeading>
      {backedWorld && !hasNoWorld(env) && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ px: 0.5, mb: 2 }}
        >
          <Iconify
            icon="solar:lock-keyhole-minimalistic-linear"
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
          <Typography
            sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
          >
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >
              {OVERVIEW_COPY.worldKind.label}
            </Box>
            {OVERVIEW_COPY.worldKind.short}
          </Typography>
          <Tooltip arrow title={OVERVIEW_COPY.worldKind.tip}>
            <Box
              component="span"
              sx={{ display: "inline-flex", flexShrink: 0 }}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={14}
                sx={{ color: "text.disabled" }}
              />
            </Box>
          </Tooltip>
        </Stack>
      )}
      {backedWorld && hasNoWorld(env) ? (
        <Stack spacing={2} sx={{ mb: 3 }}>
          <ConnectOnlyWorld env={env} />
          <DependsOnCard
            dependsOn={dependsFromContract(backedWorld.dependencies)}
            runtimeDependsOn={dependsFromContract(
              backedWorld.runtimeDependencies,
            )}
          />
          {startingData && (
            <StartingDataCard baseEnvironment={env.baseEnvironment} />
          )}
        </Stack>
      ) : (
        <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
          <Grid item xs={12} md={7}>
            {seeded || !startingData ? (
              <SeededDataCard env={env} />
            ) : (
              <StartingDataCard baseEnvironment={env.baseEnvironment} />
            )}
          </Grid>
          <Grid item xs={12} md={5}>
            <DependsOnCard
              dependsOn={
                backedWorld
                  ? dependsFromContract(backedWorld.dependencies)
                  : contract.dependsOn
              }
              runtimeDependsOn={
                backedWorld
                  ? dependsFromContract(backedWorld.runtimeDependencies)
                  : []
              }
            />
          </Grid>
          {seeded && startingData && (
            <Grid item xs={12}>
              <StartingDataCard baseEnvironment={env.baseEnvironment} />
            </Grid>
          )}
        </Grid>
      )}

      {/* Agent version-management drawer, overlaid on Overview so the user
          never leaves the tab. Not kept mounted while closed. */}
      {canManageVersions && (
        <SideDrawer
          open={agentDrawerOpen}
          onClose={() => setAgentDrawerOpen(false)}
          width={{ xs: "100%", sm: 720, md: 880 }}
        >
          <Stack sx={{ height: "100%", minHeight: 0 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 3,
                py: 1.5,
                pr: 6,
                borderBottom: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              <Iconify
                icon="solar:cpu-bolt-linear"
                width={18}
                sx={{ color: "text.secondary" }}
              />
              <Box flex={1} minWidth={0}>
                <Typography
                  component="h2"
                  sx={{ typography: "m1", fontWeight: "fontWeightMedium" }}
                >
                  {AGENT_SUMMARY_COPY.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {OVERVIEW_COPY.manageVersionsSubtitle}
                </Typography>
              </Box>
            </Stack>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <AgentVersionsPanel env={env} />
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
  onGo: PropTypes.func,
  agentConnected: PropTypes.bool,
  locked: PropTypes.bool,
  buildMode: PropTypes.bool,
  // Real §6 summary counts for a backed env (from EnvironmentWorkspace); the
  // client-store fallback in StateSummary covers non-backed envs.
  counts: PropTypes.object,
  // The newest run from the executions list, for the latest-run card.
  // Real §6 world content for a backed env: { stores, amendments, dependencies }.
  // Undefined for a non-backed env (keeps the client/fixture source).
  backedWorld: PropTypes.shape({
    stores: PropTypes.array,
    amendments: PropTypes.array,
    dependencies: PropTypes.array,
    runtimeDependencies: PropTypes.array,
  }),
};
