import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Tooltip, Box, Stack, Typography, Tab } from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { setupGaps } from "../_mock/setupGaps";

import OverviewPanel from "../workspace/OverviewPanel";
import RlContractPanel from "../workspace/RlContractPanel";
import WorldPanel from "../workspace/WorldPanel";
import { withPinnedWorld } from "../_mock/toolFit";
import ScenariosStep from "../workspace/ScenariosStep";
import EvalsStep from "../workspace/EvalsStep";
import SettingsPanel from "../workspace/SettingsPanel";

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

/*
  Personas and Actors used to be their own tabs. Personas already
  live inside every scenario they belong to — the standalone tab
  was a second entry point to the same rows. Actors got folded into
  the Contract tab because they describe the pressure the
  environment injects, which is part of the contract the agent is
  graded against.
*/
/*
  Nikhil's env-first feedback: env is the reusable asset, agent is
  one attribute. Agent details + version management live inside
  Overview via AgentSummarySection — clicking "Manage versions"
  opens a drawer overlaying Overview so the user stays on the tab
  they came from. No standalone Agents / Test-subject tab.
*/
const TABS = [
  { id: "overview", label: "Overview", needs: null },
  /* Open from the start of a build — each shows its own part being written. */
  { id: "world", label: "World", needs: null },
  { id: "contract", label: "Contract", needs: null },
  { id: "scenarios", label: "Scenarios", needs: "build", badge: "scenarios" },
  { id: "evals", label: "Evaluations", needs: null, badge: "evals" },
  { id: "settings", label: "Settings", needs: null },
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
  Tools: "world",
};

function firstReadyTab(done) {
  const t = TABS.find((tab) => !tab.needs || done.includes(tab.needs));
  return t?.id || "overview";
}

/*
  While the build runs, the panel follows whatever it is producing right now:
  the contract once the agent has been read, the scenarios once they start
  being written. Once the user picks a tab, it stays theirs.
*/
function liveTab(done) {
  if (done.includes("build")) return "scenarios";
  /* The world is built once the agent has been read. */
  if (done.includes("understand")) return "world";
  /* Until then what's being produced is the contract — it fills in on the
     Overview's build table as the agent is read. */
  return "overview";
}

export default function DerivedPanels({
  env,
  envState,
  patch,
  source,
  done,
  running,
  onBuilderTurn,
  onTabChange,
  onBuilderPrompt,
  onStartRun,
  scenarioProgress,
  contractProgress,
}) {
  const [tab, setTab] = useState(() => firstReadyTab(done));
  const [touched, setTouched] = useState(false);

  /*
    Notify the parent whenever the active tab changes so it can react
    to context (e.g. the review layout swaps the assistant's suggested
    chips based on which tab you're looking at). Fires once on mount
    with the initial pick, and after every subsequent change.
  */
  useEffect(() => {
    onTabChange?.(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  /*
    The panels open as soon as there is something real to show — the contract
    and world once the agent has been read — instead of waiting behind the
    illustration for the whole build. Each tab unlocks when the stage it
    depends on finishes; Scenarios fills row by row while they are written.
    We also wait for the store to have caught up (envState + patch) — the
    panels read from the store, and rendering them a frame early made them
    look empty.
  */
  const primed = !!envState && !!patch;
  /* No illustration: the tabs show the build live from the first frame. Only
     the frame before the store catches up renders nothing. */
  const isLoading = !primed;
  const ready = (t) => !t.needs || done.includes(t.needs);

  /* Follow the derivation until the user picks something. */
  useEffect(() => {
    if (touched) return;
    if (isLoading) return;
    setTab(liveTab(done));
  }, [done, touched, isLoading]);

  /* If the currently-selected tab has been removed from TABS (e.g.
     Contract is hidden for the demo), snap back to the first ready
     tab so the user doesn't get stranded on a tab whose entry point
     no longer exists in the nav. */
  useEffect(() => {
    if (!TABS.some((t) => t.id === tab)) {
      setTab(firstReadyTab(done));
      setTouched(false);
    }
  }, [tab, done]);

  const current = TABS.find((t) => t.id === tab && ready(t)) || TABS[0];

  /*
    All in-panel CTAs (RlContractPanel, OverviewPanel) call
    `onGo(tabId)` to move between tabs. Passing `noop` used to
    silently break every one of them — "Go to
    scenarios", "Open inbox", "See personas" all clicked into
    nothing. Wire it to the tab setter that already exists.
  */
  const go = (tabId) => {
    const target = TABS.find((tt) => tt.id === tabId);
    if (!target || !ready(target)) return;
    setTab(tabId);
    setTouched(true);
  };

  /* Panels see the world of the pinned environment version. */
  const worldEnv = useMemo(
    () => withPinnedWorld(env, envState),
    [env, envState],
  );
  const rendered = useMemo(
    () =>
      renderPanel(current.id, {
        env: worldEnv,
        envState,
        patch,
        source,
        onBuilderTurn,
        onGo: go,
        onBuilderPrompt,
        onStartRun,
        contractProgress,
        done,
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }),
    [
      current.id,
      worldEnv,
      envState,
      patch,
      source,
      onBuilderTurn,
      onBuilderPrompt,
      onStartRun,
      contractProgress,
      done,
    ],
  );

  if (!env) return null;

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── tabs ── */}
      <Box
        sx={{
          flexShrink: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
          /* Muted while loading — tabs are visible so you know what's coming,
             but not competing with the illustration below for attention. */
          opacity: isLoading ? 0.5 : 1,
          transition: "opacity 0.2s ease",
          pointerEvents: isLoading ? "none" : "auto",
        }}
      >
        <Box sx={{ px: 1 }}>
          <CustomTabs
            value={current.id}
            onChange={(_, v) => {
              setTab(v);
              setTouched(true);
            }}
            variant="scrollable"
            scrollButtons={false}
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
                /* Hide the numeric tab badges while the builder is
                 streaming stages — the counts are moving targets
                 mid-build (scenarios are being generated *right now*)
                 so surfacing them reads as if the env is done. Badges
                 come back once running settles to false. */
                /* Scenarios is the exception: its count is the progress of the
                 stage writing them, so it ticks up live. */
                const count =
                  primed && (!running || t.badge === "scenarios")
                    ? badgeCountFor(t.badge, envState)
                    : null;
                const locked = !ready(t);
                const gapItems = gapsByTab[t.id];
                const tabLabel = (
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography
                      component="span"
                      sx={{ typography: "s2", color: "inherit" }}
                    >
                      {t.label}
                    </Typography>
                    {count != null && count > 0 && (
                      <Typography
                        component="span"
                        sx={{
                          typography: "s3",
                          color: "text.subtitle",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
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
                          display: "grid",
                          placeItems: "center",
                          flexShrink: 0,
                          minWidth: 16,
                          height: 16,
                          px: "5px",
                          borderRadius: "8px",
                          bgcolor: "status.danger.bg",
                          color: "status.danger.fg",
                          typography: "s3",
                          fontWeight: 600,
                          lineHeight: 1,
                          fontVariantNumeric: "tabular-nums",
                          fontSize: 12,
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
                    disabled={locked}
                    label={
                      gapItems?.length > 0 ? (
                        <Tooltip
                          classes={{ tooltip: "ds-hovercard" }}
                          arrow
                          title={
                            <Box>
                              <Typography sx={{ typography: "s3", mb: 0.375 }}>
                                Needs your input before you can run:
                              </Typography>
                              {gapItems.map((g) => (
                                <Typography
                                  key={g.id}
                                  sx={{ typography: "s3", opacity: 0.9 }}
                                >
                                  · {g.title}
                                </Typography>
                              ))}
                            </Box>
                          }
                        >
                          {tabLabel}
                        </Tooltip>
                      ) : (
                        tabLabel
                      )
                    }
                  />
                );
              });
            })()}
          </CustomTabs>
        </Box>
      </Box>

      {/* ── body ── */}
      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
        {isLoading ? null : (
          <>
            {current.id === "scenarios" && scenarioProgress?.streaming && (
              <ScenarioStreamStrip progress={scenarioProgress} />
            )}
            {isEditable(current.id) && !scenarioProgress?.streaming && (
              <EditableHint />
            )}
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
  onTabChange: PropTypes.func,
  onBuilderPrompt: PropTypes.func,
  onStartRun: PropTypes.func,
  scenarioProgress: PropTypes.object,
  contractProgress: PropTypes.object,
};

/*
  "How many are we writing, and how far along are we" — the count the builder
  committed to, how many are written, and how many of those have passed their
  checks. Each scenario is checked as it's written, so the two bars move
  together, the proved one a step behind.
*/
function ScenarioStreamStrip({ progress }) {
  const { shown = 0, total = 0, proved = 0, rewritten = 0 } = progress || {};
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  return (
    <Box sx={{ pt: 2, pb: 0.5 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} flexWrap="wrap">
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`Writing scenarios · ${shown} of ${total}`}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${proved} proved${rewritten ? ` · ${rewritten} rewritten` : ""}`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {
            "Each is checked as it's written — ready, solvable, not vacuous — and kept only once it passes."
          }
        </Typography>
      </Stack>
      <Box
        sx={{
          mt: 1,
          height: 3,
          borderRadius: 2,
          bgcolor: "divider",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <Box
          sx={{
            position: "absolute",
            inset: 0,
            width: `${pct(shown)}%`,
            bgcolor: "text.disabled",
            opacity: 0.45,
            transition: "width 0.2s ease",
          }}
        />
        <Box
          sx={{
            position: "absolute",
            inset: 0,
            width: `${pct(proved)}%`,
            bgcolor: "text.secondary",
            transition: "width 0.2s ease",
          }}
        />
      </Box>
    </Box>
  );
}
ScenarioStreamStrip.propTypes = { progress: PropTypes.object };

/* Which panels are meaningful to edit before a run. */
function isEditable(id) {
  return ["scenarios", "evals"].includes(id);
}

function badgeCountFor(kind, envState) {
  if (!envState) return null;
  if (kind === "scenarios") return envState.scenarios?.length ?? 0;
  if (kind === "evals") return envState.evals?.length ?? 0;
  return null;
}

function renderPanel(id, ctx) {
  const {
    env,
    envState,
    patch,
    source,
    onGo,
    onBuilderPrompt,
    onStartRun,
    contractProgress,
    done,
  } = ctx;
  switch (id) {
    case "world":
      return (
        <WorldPanel
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          buildProgress={contractProgress}
          buildDone={done}
        />
      );
    case "contract":
      return (
        <RlContractPanel
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          buildMode
          buildProgress={contractProgress}
          buildDone={done}
        />
      );
    case "scenarios":
      return (
        <ScenariosStep
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          buildMode
          onBuilderPrompt={onBuilderPrompt}
          onStartRun={onStartRun}
        />
      );
    case "evals":
      return (
        <EvalsStep
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          buildMode
        />
      );
    case "settings":
      return <SettingsPanel env={env} envState={envState} patch={patch} />;
    default:
      return (
        <OverviewPanel
          env={env}
          envState={envState}
          patch={patch}
          onGo={onGo}
          agentConnected={!!envState?.agent}
          source={source}
          buildMode
          contractProgress={contractProgress}
          buildDone={done}
        />
      );
  }
}

/*
  One caption above editable panels: chat on the left drives the same changes.
*/
function EditableHint() {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <Iconify
        icon="solar:pen-linear"
        width={14}
        sx={{ color: "text.subtitle", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        Edit directly, or ask the builder on the left — “add a scenario where
        the caller is aggressive”.
      </Typography>
    </Stack>
  );
}
