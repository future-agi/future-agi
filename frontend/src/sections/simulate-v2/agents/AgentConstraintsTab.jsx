import PropTypes from "prop-types";
import { mainEnvOf } from "../_mock/agentLifecycle";
import { manualImprovementFor } from "../_mock/scheduledImprovements";
import { PLATFORM_TESTING } from "../_mock/rsiModel";
import { improvementPath } from "../improvements/improvementNav";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { improvementUrl } from "../improvements/improvementNav";
import {
  Box,
  Button,
  Chip,
  Link,
  MenuItem,
  Radio,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { ceilingLabel, improvementOutcome } from "../_mock/optimizationRuns";
import {
  PROVIDER_LABELS,
  definitionOf,
  sourceKindOf,
} from "../_mock/agentRegistry";
import { improvementRunName } from "../_mock/optimizer";
import { useSimStore } from "../store";
import { heldShortOf } from "../improvements/improvementRows";
import {
  COST_ABS_STEPS,
  costModeOf,
  defaultCostAbs,
  dollarsLabel,
  protectablePartsOf,
} from "../_mock/agentConstraints";
import {
  SCHEDULE_DAYS,
  canarySupported,
  lastAutomaticRun,
  measuredCostOf,
  nextScheduledAt,
  scheduleTimeLabel,
  surfaceAvailability,
  useAgentLimits,
} from "./agentLimits";
import { MONO, plural } from "./agentViewUtils";
import ProtectedPartsPicker from "./ProtectedPartsPicker";
import AgentConfigAreasCard from "./AgentConfigAreasCard";
import SettingsSection from "./SettingsSection";
import { SettingsLayoutContext } from "./settingsLayout";
import { areaStatesOf, configAreasFor } from "./configAreas";
import { HintLine } from "./connect/replicationBits";
import { QuietTag } from "./AgentBits";
import { clickable, focusRing } from "../components/clickable";
import { segmentedToggleSx } from "./segmentedToggleSx";
import { creditsLabel } from "../_mock/metering";

const MODELS = ["gpt-4o", "gpt-4.1", "claude-sonnet-4", "gemini-2.5-pro"];
const COST_STEPS = [0, 2, 5, 10, 20];
const LATENCY_STEPS = [0, 5, 10, 20];
const BUDGET_STEPS = [15, 25, 50, 100, 250, 500];
const MINUTE_STEPS = [15, 30, 60, 120, 150];
const HOURS = Array.from(
  { length: 24 },
  (_, h) => `${String(h).padStart(2, "0")}:00`,
);

const selectSx = {
  height: 30,
  minWidth: 140,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.5 },
};
/* The product's segmented control (Evals: Single / Composite). */
const toggleSx = segmentedToggleSx;

/**
 * What an improvement may change on this agent, and what it must not move.
 * Every improvement for it — from the Error Feed, Debug failures or a goal —
 * is held to these: a candidate that breaks one is rejected, however much it
 * helps. Saved as you change them.
 */
/* Code is a switch of its own, listed with the areas. */
const CODE_SURFACES = ["code"];

export default function AgentConstraintsTab({
  agent,
  files,
  model,
  mustPass,
  speech,
  onBuildEnv,
  embedded = false,
}) {
  const { state } = useSimStore();
  const { limits, set, reset, custom } = useAgentLimits(agent.key);
  const can = useMemo(() => surfaceAvailability(agent), [agent]);
  const parts = useMemo(() => protectablePartsOf(files || []), [files]);
  const measured = useMemo(() => measuredCostOf(state, agent), [state, agent]);
  const costMode = costModeOf(limits);
  const costAbs = limits.costCeilingAbs ?? defaultCostAbs(measured);
  /* An amount set elsewhere (the connect step takes any) stays selectable. */
  const absSteps = [...new Set([...COST_ABS_STEPS, costAbs])].sort(
    (a, b) => a - b,
  );

  const areaCount = useMemo(() => {
    const st = areaStatesOf(limits, can);
    const areas = configAreasFor(agent);
    const codeOn = CODE_SURFACES.filter(
      (id) => can[id]?.ok && limits.allowed.includes(id),
    ).length;
    return areas.filter((x) => st[x.id]).length + codeOn;
  }, [agent, limits, can]);

  return (
    <SettingsLayoutContext.Provider value={embedded ? "stacked" : "split"}>
      <Stack spacing={0}>
        <Stack direction="row" alignItems="center" spacing={2} sx={{ pb: 2 }}>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
          >
            <HintLine hint="A proposed change that breaks one is rejected, however much it helps — from the Error Feed, Debug failures or a goal.">
              {embedded
                ? `${agent.name || "This agent"}'s rules — every improvement for it is held to these. A change here applies to all of them.`
                : "Every improvement is held to these. Nothing ships without approval."}
            </HintLine>
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
          >
            {embedded
              ? `Saved to ${agent.name || "the agent"} as you change them`
              : "Saved as you change them"}
          </Typography>
          {custom && !embedded && (
            <Button
              size="small"
              onClick={reset}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            >
              Reset to safe defaults
            </Button>
          )}
        </Stack>

        <SettingsSection
          title="What it may change"
          sub={`${areaCount} on · switch an area off to keep it as it is.`}
        >
          <AgentConfigAreasCard
            agent={agent}
            limits={limits}
            can={can}
            onChange={set}
            speech={speech}
          >
            <Box
              sx={{
                px: 2,
                py: 1.125,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={2}>
                <Box sx={{ width: { xs: "auto", md: 190 }, flexShrink: 0 }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Never edited
                  </Typography>
                </Box>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                    display: { xs: "none", md: "block" },
                  }}
                >
                  Files, tools or prompt sections no proposed change may touch
                </Typography>
              </Stack>
              <Box sx={{ mt: 0.75 }}>
                <ProtectedPartsPicker
                  value={limits.protected}
                  parts={parts}
                  onChange={(next) => set({ protected: next })}
                />
              </Box>
            </Box>
            <ProtectedMembers
              agent={agent}
              value={limits.protectedMembers || []}
              onChange={(next) => set({ protectedMembers: next })}
            />
          </AgentConfigAreasCard>
        </SettingsSection>

        <SettingsSection
          title="Guardrails"
          sub="A proposed change that breaks one is rejected."
        >
          <Card>
            <Row
              title="Model"
              body={
                limits.model.mode === "approved"
                  ? "May switch to an approved model"
                  : `Stays ${model || "as it is"}`
              }
              hint={
                limits.model.mode === "approved"
                  ? "Held to the same cost and latency limits as any change. Never to a model off this list."
                  : "A fix never switches the model."
              }
            >
              <ToggleButtonGroup
                size="small"
                exclusive
                value={limits.model.mode}
                onChange={(_, v) =>
                  v && set({ model: { ...limits.model, mode: v } })
                }
                sx={toggleSx}
              >
                <ToggleButton value="locked">Locked</ToggleButton>
                <ToggleButton value="approved">Approved list</ToggleButton>
              </ToggleButtonGroup>
            </Row>
            {limits.model.mode === "approved" && (
              <Stack
                direction="row"
                flexWrap="wrap"
                gap={0.75}
                sx={{ px: 2, pb: 1.25, mt: -0.5 }}
              >
                {MODELS.map((m) => {
                  const on = m === model || limits.model.approved.includes(m);
                  return (
                    <Chip
                      key={m}
                      size="small"
                      label={m}
                      clickable={m !== model}
                      onClick={
                        m === model
                          ? undefined
                          : () =>
                              set({
                                model: {
                                  ...limits.model,
                                  approved: on
                                    ? limits.model.approved.filter(
                                        (x) => x !== m,
                                      )
                                    : [...limits.model.approved, m],
                                },
                              })
                      }
                      icon={
                        on ? (
                          <Iconify icon="solar:check-linear" width={14} />
                        ) : undefined
                      }
                      sx={{
                        fontFamily: MONO,
                        color: "text.primary",
                        "& .MuiChip-icon": { color: "text.primary" },
                        bgcolor: "transparent",
                        border: "1px solid",
                        borderColor: on ? "text.primary" : "divider",
                      }}
                    />
                  );
                })}
              </Stack>
            )}
            <Row
              title="Cost per conversation"
              body={
                costMode === "abs"
                  ? measured != null
                    ? `Today ≈ ${dollarsLabel(measured)}`
                    : "A fixed ceiling"
                  : "Compared with today's agent"
              }
              hint={
                costMode === "abs"
                  ? "Measured on every proposed change — none may cost more than this, whatever today's agent costs."
                  : "Measured on every proposed change against today's agent."
              }
            >
              <Stack direction="row" alignItems="center" spacing={1}>
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={costMode}
                  onChange={(_, v) =>
                    v &&
                    set(
                      v === "abs"
                        ? { costMode: v, costCeilingAbs: costAbs }
                        : { costMode: v },
                    )
                  }
                  sx={toggleSx}
                >
                  <ToggleButton value="pct" aria-label="Percentage above today">
                    %
                  </ToggleButton>
                  <ToggleButton
                    value="abs"
                    aria-label="Amount per conversation"
                  >
                    $
                  </ToggleButton>
                </ToggleButtonGroup>
                {costMode === "abs" ? (
                  <Select
                    size="small"
                    value={costAbs}
                    onChange={(e) => set({ costCeilingAbs: e.target.value })}
                    sx={{ ...selectSx, fontVariantNumeric: "tabular-nums" }}
                  >
                    {absSteps.map((n) => (
                      <MenuItem
                        key={n}
                        value={n}
                        sx={{
                          typography: "s2",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >{`At most ${dollarsLabel(n)}`}</MenuItem>
                    ))}
                  </Select>
                ) : (
                  <Select
                    size="small"
                    value={limits.costCeiling}
                    onChange={(e) => set({ costCeiling: e.target.value })}
                    sx={selectSx}
                  >
                    {COST_STEPS.map((n) => (
                      <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                        {ceilingLabel(n)}
                      </MenuItem>
                    ))}
                  </Select>
                )}
              </Stack>
            </Row>
            <Row title="Latency" body="Response time can't get worse than this">
              <Select
                size="small"
                value={limits.latencyCeiling}
                onChange={(e) => set({ latencyCeiling: e.target.value })}
                sx={selectSx}
              >
                {LATENCY_STEPS.map((n) => (
                  <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                    {ceilingLabel(n)}
                  </MenuItem>
                ))}
              </Select>
            </Row>
            <MustPass mustPass={mustPass} />
          </Card>
        </SettingsSection>

        {/* Embedded in an improvement's setup: the budget and time are that
          improvement's own, and the schedule isn't a limit on one run. */}
        {!embedded && (
          <>
            <SettingsSection
              title="Run budget"
              sub="The most one improvement run may use."
            >
              <Card>
                <Row
                  title="Most it may spend"
                  body="Stops here with the best it found"
                >
                  <Select
                    size="small"
                    value={limits.budget.dollars}
                    onChange={(e) =>
                      set({
                        budget: { ...limits.budget, dollars: e.target.value },
                      })
                    }
                    sx={selectSx}
                  >
                    {BUDGET_STEPS.map((n) => (
                      <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                        {creditsLabel(n)}
                      </MenuItem>
                    ))}
                  </Select>
                </Row>
                <Row
                  title="Longest it may take"
                  body="Stops early at this cap"
                  hint="Voice runs take longer — the same cap stops them early."
                >
                  <Select
                    size="small"
                    value={limits.budget.minutes}
                    onChange={(e) =>
                      set({
                        budget: { ...limits.budget, minutes: e.target.value },
                      })
                    }
                    sx={selectSx}
                  >
                    {MINUTE_STEPS.map((n) => (
                      <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                        {n < 60
                          ? `${n} minutes`
                          : `${n / 60} hour${n === 60 ? "" : "s"}`}
                      </MenuItem>
                    ))}
                  </Select>
                </Row>
              </Card>
            </SettingsSection>

            <SettingsSection
              title="Automatic improvements"
              sub="When one starts without anyone starting it."
            >
              <AutomaticCard
                agent={agent}
                schedule={limits.schedule}
                onBuildEnv={onBuildEnv}
                onChange={(patch) => {
                  const next = { ...limits.schedule, ...patch };
                  /* A new day, time or mode books the next slot from now. */
                  const timing =
                    "mode" in patch || "day" in patch || "time" in patch;
                  set({
                    schedule: timing
                      ? {
                          ...next,
                          nextAt:
                            next.mode === "weekly"
                              ? nextScheduledAt(next).toISOString()
                              : undefined,
                        }
                      : next,
                  });
                }}
              />
            </SettingsSection>
          </>
        )}
      </Stack>
    </SettingsLayoutContext.Provider>
  );
}

AgentConstraintsTab.propTypes = {
  agent: PropTypes.object.isRequired,
  files: PropTypes.array,
  model: PropTypes.string,
  mustPass: PropTypes.object,
  speech: PropTypes.object,
  embedded: PropTypes.bool,
  onBuildEnv: PropTypes.func,
};

/*
  Must-pass scenarios — one line each, the same scenario across personas
  counted once. A link opens it on its environment's Scenarios tab.
*/
const MUST_PASS_SHOWN = 4;
function MustPass({ mustPass }) {
  const navigate = useNavigate();
  const [all, setAll] = useState(false);
  const groups = useMemo(() => {
    const by = new Map();
    (mustPass.items || []).forEach((m) => {
      const k = `${m.envId}:${m.name}`;
      const g = by.get(k) || { ...m, personas: [] };
      if (m.persona) g.personas.push(m.persona);
      by.set(k, g);
    });
    return [...by.values()];
  }, [mustPass.items]);
  const shown = all ? groups : groups.slice(0, MUST_PASS_SHOWN);
  return (
    <>
      <Row
        title="Must-pass scenarios"
        body={
          mustPass.total
            ? `${mustPass.total} across ${plural(mustPass.envs, "environment")} — any that breaks disqualifies`
            : "None marked yet"
        }
        hint="Mark them on each environment's Scenarios tab."
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {mustPass.total}
        </Typography>
      </Row>
      {groups.length > 0 && (
        <Box sx={{ px: 2, pb: 1.25, mt: -0.5 }}>
          {shown.map((m) => (
            <Stack
              key={`${m.envId}:${m.name}`}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ py: 0.375, minWidth: 0 }}
            >
              <Iconify
                icon="solar:shield-check-linear"
                width={14}
                sx={{ color: "text.subtitle", flexShrink: 0 }}
              />
              <Link
                component="button"
                underline="hover"
                onClick={() =>
                  navigate(
                    `${paths.dashboard.simulate.environmentDetail(m.envId)}/scenarios?scenario=${encodeURIComponent(m.id)}`,
                  )
                }
                sx={{
                  typography: "s3",
                  color: "text.primary",
                  textAlign: "left",
                  flex: 1,
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {m.name}
              </Link>
              <Typography
                noWrap
                title={m.personas.join(", ")}
                sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
              >
                {m.personas.length > 1
                  ? `${m.personas.length} personas`
                  : m.personas[0] || m.envName}
              </Typography>
            </Stack>
          ))}
          {groups.length > MUST_PASS_SHOWN && (
            <Typography
              {...clickable(() => setAll((v) => !v), { expanded: all })}
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.secondary",
                cursor: "pointer",
                mt: 0.5,
                width: "fit-content",
                "&:hover": { color: "text.primary" },
                ...focusRing,
              }}
            >
              {all ? "Show fewer" : `Show all ${groups.length}`}
            </Typography>
          )}
        </Box>
      )}
    </>
  );
}
MustPass.propTypes = { mustPass: PropTypes.object.isRequired };

/*
  Members of a squad, flow or multi-agent app — a protected member is never
  changed by an improvement. Stored as limits.protectedMembers = [ids].
*/
function ProtectedMembers({ agent, value, onChange }) {
  const members = definitionOf(agent).members || [];
  if (!members.length) return null;
  const toggle = (id) =>
    onChange(
      value.includes(id) ? value.filter((x) => x !== id) : [...value, id],
    );
  return (
    <Box
      sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
        <HintLine
          hint={`${members.length} agents work as this one. A protected member is never changed; a change to a part they all share says which members it reaches.`}
        >
          Protected members
        </HintLine>
      </Typography>
      <Stack direction="row" flexWrap="wrap" gap={0.75}>
        {members.map((m) => {
          const on = value.includes(m.id);
          return (
            <Chip
              key={m.id}
              size="small"
              label={m.name}
              clickable
              onClick={() => toggle(m.id)}
              icon={
                on ? (
                  <Iconify
                    icon="solar:lock-keyhole-minimalistic-linear"
                    width={14}
                  />
                ) : undefined
              }
              sx={{
                color: "text.primary",
                "& .MuiChip-icon": { color: "text.primary" },
                bgcolor: "transparent",
                border: "1px solid",
                borderColor: on ? "text.primary" : "divider",
              }}
            />
          );
        })}
      </Stack>
      {/* Protecting every member leaves only what they share to change. */}
      {members.every((m) => value.includes(m.id)) && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
        >
          Every member is protected — improvements can only change the parts
          they share.
        </Typography>
      )}
    </Box>
  );
}
ProtectedMembers.propTypes = {
  agent: PropTypes.object.isRequired,
  value: PropTypes.array,
  onChange: PropTypes.func.isRequired,
};

function Card({ children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          "& > .constraint-row + .constraint-row, & > .constraint-row + *, & > * + .constraint-row":
            { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
Card.propTypes = { children: PropTypes.node };

/* One setting: name, one short line (the rest behind ⓘ), its control. */
function Row({ title, body, hint, children }) {
  return (
    <Stack
      className="constraint-row"
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 2, py: 1.125 }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        {body && (
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.subtitle" }}
          >
            <HintLine hint={hint}>{body}</HintLine>
          </Typography>
        )}
      </Box>
      {children}
    </Stack>
  );
}
Row.propTypes = {
  title: PropTypes.node,
  body: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};

/*
  Where an automatic run's change goes when it may open a draft — said for
  this agent, not as a list of every route.
*/
const draftWhere = (agent) => {
  const kind = sourceKindOf(agent);
  const provider = PROVIDER_LABELS[agent?.values?.provider];
  const platform = PLATFORM_TESTING[agent?.values?.provider];
  if (kind === "repo" || (kind === "framework" && definitionOf(agent).repoUrl))
    return "Opens a draft pull request on its repository";
  if (kind === "upload")
    return "Writes the change up as a patch for your upload — someone applies it";
  /* What each platform calls a draft: a Vapi copy, an ElevenLabs or Bland
     branch, an unpublished Retell version. */
  if (kind === "platform" && platform)
    return `${provider ? `On ${provider}, creates` : "Creates"} ${platform.ships.charAt(0).toLowerCase()}${platform.ships.slice(1)}`;
  if (kind === "builtin") return "Saves a draft Future AGI version";
  return "Writes the change up as a patch — we don't hold this agent, so someone applies it";
};

/**
 * Automatic improvements — when an improvement starts without anyone
 * starting it, and the most it may do on its own once it finds a change.
 * The levels are ordered; each includes the ones before it. Releasing to
 * every call always waits for a person.
 */
function AutomaticCard({ agent, schedule, onChange, onBuildEnv }) {
  /* commit, not dispatch: Run now leaves Agents for the improvement page, a
     different store — the record has to be saved before this one unmounts. */
  const { state, commit } = useSimStore();
  const navigate = useNavigate();
  const inEnv = (agent.usedIn || []).length > 0;
  const canary = canarySupported(agent);
  const provider = PROVIDER_LABELS[agent.values?.provider] || "This platform";
  /* Scheduled runs only (RSI PRD) — an older "new issue" choice reads as off. */
  const mode = inEnv && schedule.mode === "weekly" ? "weekly" : "off";
  /* A canary sends real calls to a change, so a person starts it — an
     automatic run stops at drafts (RSI PRD: "A person always approves").
     A saved choice of "canary" reads as drafts. */
  const autonomy =
    schedule.autonomy === "canary" ? "drafts" : schedule.autonomy;
  const last = useMemo(() => lastAutomaticRun(state, agent), [state, agent]);
  /* "Run now": the improvement the schedule starts — in the agent's main
     environment, measuring production's version first — started by this person, so
     it's theirs to review: nothing is drafted on its own. */
  const main = useMemo(() => mainEnvOf(agent, state), [agent, state]);
  const runNow = () => {
    const started = manualImprovementFor(agent, state);
    if (!started) return;
    if (started.today)
      commit({ type: "recordRun", envId: started.env.id, run: started.today });
    commit({
      type: "patchEnvState",
      envId: started.env.id,
      patch: {
        optimizations: [...(started.es.optimizations || []), started.record],
      },
    });
    navigate(improvementPath(started.env.id, started.record.id));
  };
  const canRunNow = inEnv && !!main?.env;

  const levels = [
    {
      id: "propose",
      title: "Propose only",
      body: "Waits in Improvements for a review",
      hint: "Runs and waits — the proven change sits in Improvements until someone reviews it.",
    },
    {
      id: "drafts",
      title: "Open drafts",
      body: "Nothing goes live",
      hint: `${draftWhere(agent)} as soon as a change is proven. Nothing goes live.`,
    },
  ];

  const lastLine = (() => {
    if (!last) return null;
    const { record, es } = last;
    const outcome = improvementOutcome(record);
    const held = heldShortOf(outcome);
    const draft = es?.handoffDrafts?.[record.id];
    return [
      improvementRunName(es, record),
      record.createdAt ? scheduleTimeLabel(record.createdAt) : null,
      outcome.ending.key === "ready" && held ? held.text : outcome.ending.label,
      /* What "Open drafts" did with it — or why it couldn't. */
      record.autoDraft?.where
        ? `draft opened · ${record.autoDraft.where}`
        : draft
          ? `draft opened on ${draft.where}`
          : null,
      record.autoDraft?.skipped
        ? `no draft — ${record.autoDraft.skipped}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
  })();

  return (
    <Card>
      <Row
        title="Schedule"
        body={
          !inEnv ? (
            onBuildEnv ? (
              <>
                {"Needs an environment — "}
                <Box
                  component="span"
                  {...clickable(() => onBuildEnv())}
                  sx={{
                    color: "text.primary",
                    fontWeight: 600,
                    cursor: "pointer",
                    "&:hover": { textDecoration: "underline" },
                    ...focusRing,
                  }}
                >
                  build one
                </Box>
              </>
            ) : (
              "Needs an environment — build one first"
            )
          ) : (
            {
              off: "Only when someone starts one",
              weekly: "Every week, on the newest run's scenarios",
            }[mode]
          )
        }
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          disabled={!inEnv}
          onChange={(_, v) => v && onChange({ mode: v })}
          sx={toggleSx}
        >
          <ToggleButton value="off">Off</ToggleButton>
          <ToggleButton value="weekly">Weekly</ToggleButton>
        </ToggleButtonGroup>
        {canRunNow && (
          <Button
            size="small"
            variant="outlined"
            onClick={runNow}
            startIcon={<Iconify icon="solar:play-linear" width={14} />}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Run now
          </Button>
        )}
      </Row>

      {mode === "weekly" && (
        <Row
          title="Every"
          body={`Next run · ${scheduleTimeLabel(schedule.nextAt || nextScheduledAt(schedule))}`}
        >
          <Stack direction="row" spacing={1}>
            <Select
              size="small"
              value={schedule.day}
              onChange={(e) => onChange({ day: e.target.value })}
              sx={{ ...selectSx, minWidth: 120 }}
            >
              {SCHEDULE_DAYS.map((d) => (
                <MenuItem key={d.id} value={d.id} sx={{ typography: "s2" }}>
                  {d.label}
                </MenuItem>
              ))}
            </Select>
            <Select
              size="small"
              value={schedule.time}
              onChange={(e) => onChange({ time: e.target.value })}
              MenuProps={{ PaperProps: { sx: { maxHeight: 280 } } }}
              sx={{
                ...selectSx,
                minWidth: 88,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {HOURS.map((h) => (
                <MenuItem
                  key={h}
                  value={h}
                  sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
                >
                  {h}
                </MenuItem>
              ))}
            </Select>
          </Stack>
        </Row>
      )}

      {mode !== "off" && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Box sx={{ px: 2, pt: 1.25, pb: 0.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              <HintLine hint="Each level includes the ones before it. Nothing reaches real calls on its own — a person always approves a release.">
                Most it may do on its own
              </HintLine>
            </Typography>
          </Box>
          {levels.map((l) => {
            const on = autonomy === l.id;
            return (
              <Stack
                key={l.id}
                direction="row"
                alignItems="flex-start"
                spacing={1}
                onClick={l.off ? undefined : () => onChange({ autonomy: l.id })}
                sx={{
                  px: 2,
                  py: 0.75,
                  cursor: l.off ? "default" : "pointer",
                  opacity: l.off ? 0.55 : 1,
                  "&:hover": l.off ? undefined : { bgcolor: "action.hover" },
                }}
              >
                <Radio
                  size="small"
                  checked={on}
                  disabled={l.off}
                  sx={{ p: 0.25, mt: "1px" }}
                />
                <Box minWidth={0}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {l.title}
                    </Typography>
                    {l.tag && <QuietTag>{l.tag}</QuietTag>}
                  </Stack>
                  <Typography
                    component="div"
                    sx={{ typography: "s3", color: "text.subtitle" }}
                  >
                    <HintLine hint={l.hint}>{l.body}</HintLine>
                  </Typography>
                </Box>
              </Stack>
            );
          })}
          {/* Canaries reach real calls, so a person starts them. */}
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              px: 2,
              pt: 0.5,
              pb: 1,
            }}
          >
            {canary
              ? `Canary releases on ${provider} send some real calls to the draft — a person starts them, never a schedule.`
              : `${provider} can't split traffic between versions, so a canary release isn't available for this agent.`}
          </Typography>
        </Box>
      )}

      {last && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Row title="Last automatic run" body={lastLine}>
            <Button
              size="small"
              onClick={() =>
                navigate(
                  improvementUrl(
                    last.envId,
                    last.record,
                    state.byEnv?.[last.envId],
                  ),
                )
              }
              sx={{ color: "text.secondary", flexShrink: 0 }}
            >
              Open
            </Button>
          </Row>
        </Box>
      )}
    </Card>
  );
}
AutomaticCard.propTypes = {
  agent: PropTypes.object.isRequired,
  schedule: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  onBuildEnv: PropTypes.func,
};
