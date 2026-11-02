import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Grid } from "@mui/material";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import {
  AGENT_SUMMARY_COPY,
  CONNECTOR_LABEL,
  DIRECTION_LABEL,
  ENV_SHAPE,
  ENV_STATE_SHAPE,
  OVERVIEW_COPY,
  worldTables,
} from "./overview.constants";
import { PARALLELISM_COPY, degradeReasonCopy } from "../../parallelism.constants";
import { GroupHeading, Fact } from "./OverviewPrimitives";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import SoonChip from "../../components/SoonChip";
import useUiSample from "../../hooks/useUiSample";
import { RUN_MODE_CHIP } from "../workspace.constants";
import AgentLine from "./AgentLine";
import ConnectAgentBanner from "./ConnectAgentBanner";
import ChangeAgentDrawer from "../agent/ChangeAgentDrawer";
import SideDrawer from "../../components/SideDrawer";
import AgentVersionsPanel from "../agents/AgentVersionsPanel";
import SourceToSandboxMap from "./SourceToSandboxMap";
import StateSummary from "./StateSummary";
import { UseCasesCard, AmendmentsCard } from "./OverviewCards";
import { DependsOnCard, SeededDataCard } from "./WorldCards";

/**
 * What this environment is, in Vel's order: the description and four facts,
 * the agent line (or a Connect your agent banner), the four tiles and the
 * latest run, the capabilities map, use cases and amendments, then the world.
 * Facts the API doesn't have are soon chips; the map's origins and targets
 * fill in only with ?uiSample=world. While versioning is on, an unlocked
 * env's "Manage versions" opens its agent versions in a side drawer overlaid
 * on this tab. Twin-backed sections are not ported.
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

// How a run reaches the agent, said under The world (Vel's proof note). Only
// Live has a real source today (an agent connected where it runs).
const WORLD_NOTE = {
  connect_only: "runs call a test copy in your account; its tools reach your real systems",
};

export default function OverviewPanel({ env, envState, onGo, locked = false, buildMode, counts, latestRun, backedWorld }) {
  const sampleWorld = useUiSample("world");
  const [changingAgent, setChangingAgent] = useState(false);
  const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);
  // Agent versions are managed on real data, so only while versioning is on.
  const canManageVersions = !locked && Boolean(env.versioning?.enabled);
  const manageVersions = canManageVersions
    ? () => setAgentDrawerOpen(true)
    : undefined;
  const surface = getSurface(env.surface);
  const contract = contractFor(env);
  // The real connector for a backed env (envState.agent.typeId = the job poll's
  // detected connector, e.g. "livekit"). No transports field is served, so this
  // replaces the old hardcoded transports fact.
  const connector = envState?.agent?.typeId;
  const connectorLabel = connector ? CONNECTOR_LABEL[connector] || connector : "-";

  const parallelism = env.parallelism;
  const degradeReasons = parallelism?.degrade_reasons || [];

  const transports = [
    connector ? connectorLabel : null,
    surface?.id === "voice" && env.callDirection ? DIRECTION_LABEL[env.callDirection] || env.callDirection : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const worldNote = WORLD_NOTE[env.agentMode];

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-start" }} spacing={2}>
        {/* One line; the whole description on hover, as in Vel's Overview. */}
        <CustomTooltip show={!!env.description} size="small" arrow title={env.description || ""}>
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary", minWidth: 0, whiteSpace: "nowrap" }}>
            {env.description}
          </Typography>
        </CustomTooltip>
      </Stack>

      {/* Labels, not content — a strip rather than a column of its own. */}
      <Stack
        direction="row"
        flexWrap="wrap"
        divider={<Box sx={{ width: "1px", bgcolor: "divider", mx: 2 }} />}
        sx={{ my: 2, py: 1.25, px: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, rowGap: 1, width: "fit-content", maxWidth: "100%" }}
      >
        <Fact label={OVERVIEW_COPY.facts.channel} value={surface?.label ?? "-"} />
        <Fact label={OVERVIEW_COPY.facts.domain} value={(backedWorld && env.domain) || "-"} />
        <Fact label={OVERVIEW_COPY.facts.transports} value={transports || "-"} />
        <Fact
          label={OVERVIEW_COPY.facts.packs}
          value={<SoonChip label="Packs" detail="Ready-made scenario sets (for example red-team or safety) added to the environment." what="Scenario packs" plural size="tiny" sx={{ mt: 0.25 }} />}
        />
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

      {env.testSubject ? (
        <AgentLine
          subject={env.testSubject}
          agentMode={env.agentMode}
          envId={env.id}
          versioningEnabled={Boolean(env.versioning?.enabled)}
          onChangeAgent={locked ? undefined : () => setChangingAgent(true)}
          onManageVersions={manageVersions}
        />
      ) : (
        <ConnectAgentBanner
          onConnect={() => setChangingAgent(true)}
          onManageVersions={manageVersions}
        />
      )}
      <ChangeAgentDrawer open={changingAgent} onClose={() => setChangingAgent(false)} env={env} />

      {/* State-of-the-env summary tiles + latest run — the "how's this env doing
          right now?" answer. Each tile jumps to the tab it summarises. For a
          backed env the numbers come from §6 (summaryCounts). */}
      <StateSummary env={env} envState={envState} counts={counts} latestRun={latestRun} onGo={onGo} />

      {/* CapabilityGraph moved to the Contract tab — that's where the shape of
          the environment belongs. Overview keeps the summary, not the definition. */}
      <GroupHeading>{OVERVIEW_COPY.capabilities}</GroupHeading>

      <SourceToSandboxMap
        env={env}
        envState={envState}
        stores={backedWorld ? backedWorld.stores ?? [] : undefined}
        sample={sampleWorld}
      />

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
      {worldNote && (
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1.5, mt: -0.5 }}>
          <Iconify icon={RUN_MODE_CHIP[env.agentMode]?.icon} width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
              {RUN_MODE_CHIP[env.agentMode]?.label}
            </Box>
            {`: ${worldNote}`}
          </Typography>
        </Stack>
      )}
      <Grid container spacing={2} alignItems="flex-start" sx={{ mb: 3 }}>
        {backedWorld && worldTables(env).length > 0 && (
          <Grid item xs={12} md={7}>
            <SeededDataCard env={env} />
          </Grid>
        )}
        <Grid item xs={12} md={5}>
          <DependsOnCard
            dependsOn={backedWorld ? dependsFromContract(backedWorld.dependencies) : contract.dependsOn}
          />
        </Grid>
      </Grid>

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
                px: 2.5,
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
                  sx={{ typography: "s1", fontWeight: "fontWeightBold" }}
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
  latestRun: PropTypes.object,
  // Real §6 world content for a backed env: { stores, amendments, dependencies }.
  // Undefined for a non-backed env (keeps the client/fixture source).
  backedWorld: PropTypes.shape({
    stores: PropTypes.array,
    amendments: PropTypes.array,
    dependencies: PropTypes.array,
  }),
};
