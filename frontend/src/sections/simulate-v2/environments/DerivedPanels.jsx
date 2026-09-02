import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Tooltip, Box, Stack, Typography, Tab } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import DerivingAnimation from "./DerivingAnimation";
import PipelineChecks from "./PipelineChecks";
import { pipelineStatus } from "../_mock/buildPipeline";
import { setupGaps } from "../_mock/setupGaps";

import OverviewPanel from "../workspace/OverviewPanel";
import AgentsPanel from "../workspace/AgentsPanel";
import RlContractPanel from "../workspace/RlContractPanel";
import ScenariosStep from "../workspace/ScenariosStep";
import PersonasPanel from "../workspace/PersonasPanel";
import ActorsPanel from "../workspace/ActorsPanel";
import EvalsStep from "../workspace/EvalsStep";

/**
 * The environment, brought forward to the build screen.
 *
 * Only the panels that matter *before* the first run: what the environment
 * is (Overview), how the agent is wired (Agent), the derived contract, the
 * three world dimensions (Scenarios, Personas, Actors), the graders (Evals),
 * and the setup gaps that need a human. Post-run surfaces — Runs,
 * Optimizations, Instances, Files, RL interface, Settings, How this was
 * built — belong to the environment workspace: they either have nothing to
 * show yet, or they answer questions that only exist after a run has landed.
 * Bringing them here would fill the screen with empty panes.
 *
 * Horizontal tabs, not a rail, because the pane is already narrow and a
 * vertical rail would leave the panel content in a column that couldn't hold
 * a scenario row without wrapping.
 */

const TABS = [
  { id: "overview",  label: "Overview",         needs: null },
  { id: "agent",     label: "Agents",           needs: null },
  { id: "contract",  label: "Contract",         needs: "understand" },
  { id: "scenarios", label: "Scenarios",        needs: "scenarios", badge: "scenarios" },
  { id: "personas",  label: "Personas",         needs: "scenarios", badge: "personas" },
  { id: "actors",    label: "Actors",           needs: "scenarios", badge: "actors" },
  { id: "evals",     label: "Evaluations",      needs: null,        badge: "evals" },
];

/*
  Setup-gap areas map to the tabs that own the underlying work, so the
  "needs your input" tab is redundant — any tab with a blocking gap
  shows a small amber dot in its label. The dot is only about *what is
  still missing*, so it disappears the moment the user answers it.
*/
/*
  Only route gaps to tabs where the user can actually resolve them
  inside this workspace. The agent was already connected on the
  previous screen — nothing on the Agents tab lets you fill in the
  sandbox secret or configure tool behavior, so those gaps don't
  belong there. Contract-shape and grading gaps stay because the
  Contract and Evaluations tabs are where they get answered.
*/
const GAP_AREA_TO_TAB = {
  Contract: "contract",
  Grading: "evals",
};

function firstReadyTab(done) {
  const t = TABS.find((tab) => !tab.needs || done.includes(tab.needs));
  return t?.id || "overview";
}

export default function DerivedPanels({
  env, envState, patch, source, done, running, onBuilderTurn,
}) {
  const [tab, setTab] = useState(() => firstReadyTab(done));
  const [touched, setTouched] = useState(false);

  /*
    Derivation is complete when the builder reaches the "scenarios" stage. We
    also wait for the store to have caught up (envState + patch) — the panels
    read from the store and rendering them a frame early meant the tabs looked
    empty even after derivation finished. Only when both are true do we swap
    from the illustrated loading state to the real editable panels.
  */
  const derivationDone = done.includes("scenarios");
  const primed = !!envState && !!patch;
  const isLoading = !derivationDone || !primed;

  /* Follow the derivation until the user picks something. */
  useEffect(() => {
    if (touched) return;
    if (isLoading) return;
    setTab(firstReadyTab(done));
  }, [done, touched, isLoading]);

  const current = TABS.find((t) => t.id === tab) || TABS[0];

  /*
    All in-panel CTAs (RlContractPanel, PersonasPanel, ActorsPanel,
    OverviewPanel) call `onGo(tabId)` to move between tabs. Passing
    `noop` used to silently break every one of them — "Go to
    scenarios", "Open inbox", "See personas" all clicked into
    nothing. Wire it to the tab setter that already exists.
  */
  const go = (tabId) => {
    if (!TABS.some((tt) => tt.id === tabId)) return;
    setTab(tabId);
    setTouched(true);
  };

  const rendered = useMemo(() => renderPanel(current.id, {
    env, envState, patch, source, onBuilderTurn, onGo: go,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [current.id, env, envState, patch, source, onBuilderTurn]);

  if (!env) return null;

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── tabs ── */}
      <Box
        sx={{
          flexShrink: 0, borderBottom: "1px solid", borderColor: "divider",
          /* Muted while loading — tabs are visible so you know what's coming,
             but not competing with the illustration below for attention. */
          opacity: isLoading ? 0.5 : 1,
          transition: "opacity 0.3s ease",
          pointerEvents: isLoading ? "none" : "auto",
        }}
      >
        <CustomTabs
          value={tab}
          onChange={(_, v) => { setTab(v); setTouched(true); }}
          variant="scrollable"
          scrollButtons={false}
          sx={{ minHeight: 42, px: 1, "& .MuiTab-root": { typography: "s2", minHeight: 42 } }}
        >
          {(() => {
            /*
              Collect this env's blocking setup gaps once so every tab
              can ask "do you own any of the missing pieces?" in
              constant time. Blocking gaps get an amber dot; the tab
              tooltip lists what's missing so the user knows what to
              open the tab for.
            */
            const gaps = primed ? setupGaps(env, envState) : [];
            const gapsByTab = {};
            gaps.forEach((g) => {
              if (g.status !== "blocking") return;
              const tabId = GAP_AREA_TO_TAB[g.area];
              if (!tabId) return;
              (gapsByTab[tabId] = gapsByTab[tabId] || []).push(g);
            });
            return TABS.map((t) => {
              const count = primed ? badgeCountFor(t.badge, envState) : null;
              const gapItems = gapsByTab[t.id];
              const tabLabel = (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography component="span" sx={{ typography: "s2", color: "inherit" }}>
                    {t.label}
                  </Typography>
                  {count != null && count > 0 && (
                    <Typography component="span" sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      {count}
                    </Typography>
                  )}
                  {gapItems?.length > 0 && (
                    /*
                      Soft-tinted numeric badge — same "unread count"
                      semantics, calmer look: red-on-red-tint rather
                      than a solid red pill. Enough presence to catch
                      the eye without shouting.
                    */
                    <Box
                      sx={{
                        display: "grid", placeItems: "center", flexShrink: 0,
                        minWidth: 16, height: 16, px: "5px",
                        borderRadius: "8px",
                        bgcolor: (th) => alpha("#DC2626", th.palette.mode === "dark" ? 0.2 : 0.12),
                        color: "#DC2626",
                        typography: "s3", fontWeight: 700, lineHeight: 1,
                        fontVariantNumeric: "tabular-nums",
                        fontSize: 10,
                      }}
                    >
                      {gapItems.length}
                    </Box>
                  )}
                </Stack>
              );
              return (
                <Tab
                  key={t.id}
                  value={t.id}
                  sx={{ minHeight: 42 }}
                  label={gapItems?.length > 0 ? (
                    <Tooltip
                      arrow
                      title={
                        <Box>
                          <Typography sx={{ typography: "s3", fontWeight: 700, mb: 0.375 }}>
                            Needs your input before you can run:
                          </Typography>
                          {gapItems.map((g) => (
                            <Typography key={g.id} sx={{ typography: "s3", opacity: 0.9 }}>
                              · {g.title}
                            </Typography>
                          ))}
                        </Box>
                      }
                    >
                      {tabLabel}
                    </Tooltip>
                  ) : tabLabel}
                />
              );
            });
          })()}
        </CustomTabs>
      </Box>

      {/* ── body ── */}
      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
        {isLoading ? (
          /*
            The loading screen: the illustrated hero across the whole body,
            regardless of the selected tab. Users asked for it back — it was
            being suppressed for tabs whose `needs` were null (Overview, Agent,
            Evals, Gaps), which meant the animation only showed up sometimes.
          */
          <>
            <DerivingAnimation
              label={
                !done.includes("understand")
                  ? "Reading your agent — extracting tools and rules"
                  : !done.includes("build")
                    ? "Building the world — seeding data and wiring handlers"
                    : !done.includes("scenarios")
                      ? "Writing scenarios — proving each one solvable"
                      : "Loading the editor for what we derived"
              }
            />
            <PipelineChecks pipeline={pipelineStatus(done, running, "setup")} />
          </>
        ) : (
          <>
            {isEditable(current.id) && <EditableHint />}
            {rendered}
          </>
        )}
      </Box>
    </Stack>
  );
}

DerivedPanels.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  source: PropTypes.object,
  done: PropTypes.array,
  running: PropTypes.bool,
  scenarios: PropTypes.array,
  evalIds: PropTypes.array,
  onAddEvals: PropTypes.func,
  onBuilderTurn: PropTypes.func,
};

/* Which panels are meaningful to edit before a run. */
function isEditable(id) {
  return ["scenarios", "personas", "actors", "evals", "agent"].includes(id);
}

function badgeCountFor(kind, envState) {
  if (!envState) return null;
  if (kind === "scenarios") return envState.scenarios?.length ?? 0;
  if (kind === "personas") {
    /* Personas are derived from scenarios now — count the unique
       archetypes actually in play, not a separate list. */
    const seen = new Set();
    (envState.scenarios || []).forEach((s) => {
      if (s.persona) seen.add(s.persona.slug || s.persona.name);
    });
    return seen.size;
  }
  if (kind === "actors") return envState.actors?.length ?? 0;
  if (kind === "evals") return envState.evals?.length ?? 0;
  return null;
}

function renderPanel(id, ctx) {
  const { env, envState, patch, source, onBuilderTurn, onGo } = ctx;
  switch (id) {
    case "agent":     return <AgentsPanel env={env} envState={envState} patch={patch} onGo={onGo} buildMode onBuilderTurn={onBuilderTurn} />;
    case "contract":  return <RlContractPanel env={env} envState={envState} onGo={onGo} buildMode />;
    case "scenarios": return <ScenariosStep env={env} envState={envState} patch={patch} onGo={onGo} buildMode />;
    case "personas":  return <PersonasPanel env={env} envState={envState} patch={patch} onGo={onGo} />;
    case "actors":    return <ActorsPanel env={env} envState={envState} patch={patch} onGo={onGo} />;
    case "evals":     return <EvalsStep env={env} envState={envState} patch={patch} onGo={onGo} buildMode />;
    default:          return <OverviewPanel env={env} envState={envState} patch={patch} onGo={onGo} agentConnected={!!envState?.agent} source={source} buildMode />;
  }
}

/*
  One caption above editable panels: chat on the left drives the same changes.
*/
function EditableHint() {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{
        px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <Iconify icon="solar:pen-linear" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        Edit directly, or ask the builder on the left — “add a scenario where the caller is aggressive”.
      </Typography>
    </Stack>
  );
}
