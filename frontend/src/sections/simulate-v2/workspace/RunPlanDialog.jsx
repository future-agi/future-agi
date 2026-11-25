import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  InputAdornment,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { useSimStore } from "../store";
import { monthStart, monthUsage, simMeters } from "../_mock/usageLedger";
import { FREE_TIER, RATE_PLAN } from "../_mock/rates";
import { currentUser } from "../_mock/scenarioProvenance";
import {
  WORKSPACE_POLICY,
  checkRunPlan,
  defaultRunCap,
  estimateRunPlan,
  fmtCredits,
  mergeEstimates,
  projectOf,
  rangeLabel,
  usdRange,
} from "../_mock/pricing";
import { catalogueOfEnv, envReleaseOf } from "../_mock/catalogue";
import {
  RUN_DEADLINES,
  STEP_LIMITS,
  TRIAL_TIMEOUTS,
  minutesLabel,
  runLimitsOf,
} from "../_mock/runLimits";
import { currentAgentVersion, currentEnvVersion } from "../_mock/versions";
import { runPlan } from "../_mock/proofs";
import { currentSuiteLabel, suiteVersionLogOf } from "../_mock/suiteLog";
import { getScenarioSelection } from "../_mock/scenarioSelectionBus";
import {
  initialPickOf,
  pairIdOf,
  scopeRowsOf,
  checkProblemsOf,
  needsCheckOf,
  trialsHelpOf,
} from "../_mock/runPlanScope";
import { validatingOf } from "../_mock/validating";
import ReadyToRun, { KnownLimits } from "../compat/ReadyToRun";
import { StatusDot } from "../components/primitives";
import { readinessOf } from "../_mock/readiness";
import { PAIR_CHECK_LIMIT_MIN } from "../_mock/compatibility";
import { ago } from "../agents/agentViewUtils";
import { SHOW_RUN_COST } from "../_mock/costFlags";
import { evalSlotsLeft } from "../_mock/evals";
import { platformPreviewOf } from "../_mock/platformPreview";
import { assistantsFor } from "../_mock/connectAgent";
import { delegationEvidenceOf } from "../_mock/handoffs";
import { membersOf } from "../_mock/improvementPolicy";
import { useNavigate, useSearchParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import ToolFitRow from "../run/ToolFitRow";
import RunHeldNotes from "./RunHeldNotes";
import { startsFromCode } from "../_mock/connectionCheck";

/* Pre-run cost and spending cap for simulations: hidden for now. */

/*
  The run plan: what a run will do and what it can cost, confirmed before
  anything starts. Scope (scenarios × attempts × agent versions), the estimate
  by component, what's paid outside Future AGI, the allowance left and the
  run's own spending limit. A hold names why and what to do; nothing is
  priced at zero to get past it.
*/

const ALERTS = [0.5, 0.8, 0.9];
const MORE = "__more";

/* One key-value row: label (and its quiet detail) left, value right. */
function Row({ label, detail, value, strong, muted, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={2}
      sx={{
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: 0 },
      }}
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: strong ? 600 : 400,
            color: muted ? "text.secondary" : "text.primary",
          }}
        >
          {label}
        </Typography>
        {detail && (
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {detail}
          </Typography>
        )}
      </Box>
      {children || (
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: strong ? 600 : 400,
            color: muted ? "text.secondary" : "text.primary",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
          }}
        >
          {value}
        </Typography>
      )}
    </Stack>
  );
}
Row.propTypes = {
  label: PropTypes.node,
  detail: PropTypes.node,
  value: PropTypes.node,
  strong: PropTypes.bool,
  muted: PropTypes.bool,
  children: PropTypes.node,
};

const VALUE_SX = { fontSize: 13, lineHeight: "20px", minWidth: 0 };
const LINK_SX = {
  appearance: "none",
  background: "none",
  border: 0,
  p: 0,
  font: "inherit",
  fontSize: 13,
  cursor: "pointer",
  color: "text.secondary",
};

/* A text link on a row's right ("Edit"). */
function TextLink({ onClick, children }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{ ...LINK_SX, "&:hover": { color: "text.primary" } }}
    >
      {children}
    </Box>
  );
}
TextLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

/* One row of the plan: label column, value, an optional right-aligned link,
   and an optional detail that opens under the value. */
function KV({ label, action, detail, children }) {
  return (
    <Box
      className="kv"
      sx={{
        display: "grid",
        gridTemplateColumns: "130px minmax(0, 1fr) auto",
        columnGap: 1.5,
        alignItems: "center",
        minHeight: 38,
        py: 0.5,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ fontSize: 13, color: "text.secondary" }}>
        {label}
      </Typography>
      <Box sx={{ minWidth: 0 }}>{children}</Box>
      <Box>{action}</Box>
      {detail && <Box sx={{ gridColumn: "1 / -1", mt: 0.5 }}>{detail}</Box>}
    </Box>
  );
}
KV.propTypes = {
  label: PropTypes.string,
  action: PropTypes.node,
  detail: PropTypes.node,
  children: PropTypes.node,
};

/* Which scenarios run: a compact checklist with select all / none and the
   count. Rows that can't run on this environment version stay listed,
   disabled, with the reason on hover. */
function ScenarioChecklist({
  rows,
  selected,
  onToggle,
  onAll,
  onNone,
  envLabel,
  fill = false,
}) {
  const okN = rows.filter((r) => r.ok).length;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        /* Fill: the list takes the drawer's remaining height. */
        ...(fill && {
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }),
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          minHeight: 36,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{
            fontSize: 13,
            fontWeight: 600,
            flex: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${selected.length} selected`}
          <Box
            component="span"
            sx={{ fontWeight: 400, color: "text.tertiary", ml: 0.75 }}
          >
            {`· ${okN} can run on environment ${envLabel}`}
          </Box>
        </Typography>
        <Button size="small" onClick={onAll} disabled={selected.length === okN}>
          Select all
        </Button>
        <Button size="small" onClick={onNone} disabled={!selected.length}>
          Select none
        </Button>
      </Stack>
      <Box
        sx={
          fill
            ? { flex: 1, minHeight: 0, overflowY: "auto" }
            : { maxHeight: 224, overflowY: "auto" }
        }
      >
        {rows.map((r) => {
          const row = (
            <Box
              component="label"
              key={r.id}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                minHeight: 32,
                pl: 0.75,
                pr: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:last-of-type": { borderBottom: 0 },
                cursor: r.ok ? "pointer" : "default",
                "&:hover": r.ok ? { bgcolor: "action.hover" } : undefined,
              }}
            >
              <Checkbox
                size="small"
                disabled={!r.ok}
                checked={r.ok && selected.includes(r.id)}
                onChange={() => onToggle(r.id)}
                inputProps={{ "aria-label": r.title }}
                sx={{ p: 0.5 }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s1",
                  flex: 1,
                  minWidth: 0,
                  color: r.ok ? "text.primary" : "text.disabled",
                }}
              >
                {r.title}
                {r.actor && (
                  <Box component="span" sx={{ color: "text.tertiary" }}>
                    {` · ${r.actor}`}
                  </Box>
                )}
              </Typography>
              {r.status && (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <StatusDot status={r.status} size={8} />
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {r.status}
                  </Typography>
                </Stack>
              )}
              {!r.ok && (
                <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                  {`can't run on ${envLabel}`}
                </Typography>
              )}
              {r.notProved && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    px: 0.75,
                    borderRadius: "4px",
                    bgcolor: "action.hover",
                  }}
                >
                  Not proved
                </Typography>
              )}
            </Box>
          );
          return r.ok && !r.notProved ? (
            row
          ) : (
            <Tooltip
              key={r.id}
              arrow
              placement="left"
              title={r.notProved ? "Its proof check fails now" : r.why || ""}
            >
              {row}
            </Tooltip>
          );
        })}
      </Box>
    </Box>
  );
}
ScenarioChecklist.propTypes = {
  rows: PropTypes.array,
  selected: PropTypes.array,
  onToggle: PropTypes.func,
  onAll: PropTypes.func,
  onNone: PropTypes.func,
  envLabel: PropTypes.string,
  fill: PropTypes.bool,
};

const lineValue = (l) => {
  if (l.status === "unpriced") return "no price set";
  if (l.status === "included") return "included";
  if (l.status === "informational") return "not billed";
  return rangeLabel(l.low, l.high);
};

export default function RunPlanDialog({
  open,
  onClose,
  onStart,
  env,
  envState,
  scenarioCount,
  scopeNote,
  initialAttempts,
  versionOptions,
  evals,
  runLocation,
  parallel,
  missingRate,
  alsoCount,
  initialVersion,
  alsoScopes = [],
  breakdown = null,
  /* The environment's saved versions [{ label, current }] and the scope
     breakdown for an older one — run setup picks the environment version
     too (runs.md step 1). */
  envVersionOptions = [],
  breakdownOf = null,
  seedFrom = null,
  /* The readiness report for the picked agent on this environment —
     compatibility belongs to the pair, so it follows the Agent pick. */
  compatOf = null,
  canCheckCompat = null,
  /* (agentKey) => what a fix made in place needs, for that agent. */
  fixKitOf = null,
  /* (jobs, agentKey) => hand builder fixes to the builder chat. */
  onFixInBuilder = null,
  onCheckCompat,
  checkingCompat = false,
  onRepairCompat,
  agentChoices = [],
  /* Another compatible agent to start with (Agent → Add to an existing
     environment). */
  initialAgentKey = null,
  suites = [],
  envLabel = null,
  suggestedEvals = [],
  canAddEvals = true,
  /* Fixes made from the plan (the tool-fit row) write to the environment. */
  patch = null,
  /* Scenario ids ticked when the plan opens (the rows picked on the results
     page, e.g. the flaky ones). */
  initialScenarioIds = null,
  /* { [scenarioId]: "passed" | "flaky" | "failed" } from the source run. */
  scenarioStatus = null,
  /* The run this plan re-runs: { agentKey, agentVersion, envVersion, label }.
     Its pairing needs no new check; any other pairing does. */
  baseline = null,
  /* The environment version to start on (a re-run on the source run's). */
  initialEnvVersion = null,
  /* "failed" | "flaky" | "scenarios": what the title says a re-run re-runs. */
  titleKind = null,
}) {
  const voice = ["voice", "multi"].includes(env?.surface);
  /* Agents we start from code run in our sandbox and take the seed; a
     hosted endpoint or a Vapi/Retell agent runs elsewhere and can't. */
  const seedable = startsFromCode(envState?.agent || {});
  /* Submitting (runs.md): a rejected start says no run was created; a
     start that times out never submits twice — it offers to find the run.
     ?submit=rejected | timeout shows each, as ?price=missing does. */
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const submitDemo = searchParams.get("submit");
  const [submit, setSubmit] = useState(null);
  useEffect(() => {
    if (open) setSubmit(null);
  }, [open]);
  const [attempts, setAttempts] = useState(initialAttempts || 3);
  const [versions, setVersions] = useState(
    initialVersion && versionOptions.some((o) => o.label === initialVersion)
      ? [initialVersion]
      : versionOptions.slice(0, 1).map((o) => o.label),
  );
  const [moreVersions, setMoreVersions] = useState(false);
  /* Run setup: which agent (this environment's, or another compatible one)
     and which suite (every scenario, or a saved suite). */
  const [agentKey, setAgentKey] = useState(initialAgentKey);
  const [suiteId, setSuiteId] = useState("all");
  /* Suggested evaluations score this run only when picked here (evals.md:
     run only selected evaluations); a pick is added to the environment. */
  const [addEvalIds, setAddEvalIds] = useState([]);
  const evalRoom = evalSlotsLeft(envState?.evals || []);
  const pickable = canAddEvals ? suggestedEvals : [];
  const toggleEval = (id) =>
    setAddEvalIds((ids) =>
      ids.includes(id)
        ? ids.filter((x) => x !== id)
        : ids.length < evalRoom
          ? [...ids, id]
          : ids,
    );
  const [otherVersionPick, setOtherVersionPick] = useState(null);
  const otherChoice =
    agentChoices.find((a) => a.key === agentKey && !a.current) || null;
  /* Another agent runs the version picked beside it (#57), production by
     default. */
  const otherAgent = otherChoice
    ? {
        ...otherChoice,
        version:
          otherVersionPick &&
          (otherChoice.versions || []).includes(otherVersionPick)
            ? otherVersionPick
            : otherChoice.version,
      }
    : null;
  const suite = suites.find((x) => x.id === suiteId) || null;
  /* The environment version this run uses: the current one unless picked. */
  const currentEnvLabel =
    envVersionOptions.find((v) => v.current)?.label ||
    envLabel ||
    currentEnvVersion(env, envState)?.label ||
    "v1";
  const [envVer, setEnvVer] = useState(initialEnvVersion || null);
  const pickedEnv = envVer && envVer !== currentEnvLabel ? envVer : null;
  const bd0 = pickedEnv && breakdownOf ? breakdownOf(pickedEnv) : breakdown;
  /* ── scope: which scenarios run (checklist, pre-filled by the caller) ── */
  const scopePlan = useMemo(
    () =>
      env && envState && agentChoices.length
        ? runPlan(env, envState, { envVersion: pickedEnv || undefined })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      env,
      envState?.scenarios,
      envState?.envVersions,
      envState?.activeEnvVersion,
      pickedEnv,
      agentChoices.length,
    ],
  );
  const scopeRows = useMemo(
    () =>
      scopeRowsOf(scopePlan, scenarioStatus || {}, {
        rerun:
          !!baseline?.envVersion &&
          (pickedEnv || currentEnvLabel) === baseline.envVersion,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      scopePlan,
      scenarioStatus,
      baseline?.envVersion,
      pickedEnv,
      currentEnvLabel,
    ],
  );
  const listActive = scopeRows.some((r) => r.ok);
  /* A selection made on the Scenarios tab is what Run opened with when its
     count matches. */
  const wantedIds =
    initialScenarioIds?.length > 0
      ? initialScenarioIds
      : (() => {
          const sel = getScenarioSelection()?.ids || [];
          return sel.length && sel.length === scenarioCount ? sel : null;
        })();
  const defaultPick = useMemo(
    () => initialPickOf(scopeRows, wantedIds),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scopeRows, initialScenarioIds, scenarioCount],
  );
  const [picked, setPicked] = useState(null);
  const okIds = useMemo(
    () => new Set(scopeRows.filter((r) => r.ok).map((r) => r.id)),
    [scopeRows],
  );
  const selIds = (picked ?? defaultPick).filter((id) => okIds.has(id));
  /* Asked-for scenarios that can't run here are said, not dropped quietly:
     "14 flaky · Re-run" opening on "13 selected" read as a bug. */
  const wantedOut = (initialScenarioIds || []).filter((id) => !okIds.has(id));
  const wantedOutWhy = [
    ...new Set(
      wantedOut.map((id) =>
        (
          scopeRows.find((r) => r.id === id)?.why ||
          "no longer in this environment version"
        ).replace(/\.+$/, ""),
      ),
    ),
  ].join("; ");
  const pickChanged =
    picked !== null &&
    (selIds.length !== defaultPick.length ||
      selIds.some((id) => !defaultPick.includes(id)));
  const toggleScenario = (id) =>
    setPicked(
      selIds.includes(id) ? selIds.filter((x) => x !== id) : [...selIds, id],
    );
  /* The scenario version (suite): the current one unless picked. */
  const suiteLabels = envState
    ? [
        ...new Set(
          [
            ...suiteVersionLogOf(envState).map((l) => l.label),
            currentSuiteLabel(envState),
          ].filter(Boolean),
        ),
      ]
    : [];
  const currentSuiteLbl = currentSuiteLabel(envState || {});
  const [suiteVer, setSuiteVer] = useState(null);
  const scenarioVer = suiteVer || currentSuiteLbl;
  /* Shown the way the run header names it ("scenarios v1"), not the
     internal "s1". */
  const verName = (l) => String(l || "").replace(/^s(?=\d)/, "v");
  /* What stale/held notes read from: restricted to the ticked scenarios. */
  const bd =
    listActive && bd0
      ? {
          ...bd0,
          eligible: selIds.length,
          blocked: 0,
          excluded: 0,
          blockedList: [],
          staleIds: (bd0.staleIds || []).filter((id) => selIds.includes(id)),
          stale: (bd0.staleIds || []).filter((id) => selIds.includes(id))
            .length,
        }
      : bd0;
  /* The version's scenarios in all — the count line splits them into
     eligible, blocked and excluded. */
  const scopeCount = listActive
    ? scopeRows.length
    : bd
      ? bd.eligible + bd.blocked + bd.excluded
      : scenarioCount;
  const shownCount = listActive
    ? selIds.length
    : suite
      ? suite.ids.length
      : pickedEnv && bd
        ? bd.eligible
        : scenarioCount;
  /* Ready to run is for exactly what would run: the picked agent, its picked
     version and the picked suite (the pair, and its cases). */
  const pickedKey =
    agentKey || agentChoices.find((a) => a.current)?.key || null;
  const reportFor = (version) =>
    compatOf
      ? compatOf(pickedKey, {
          version,
          ids: listActive ? selIds : suite ? suite.ids : null,
          envVersion: pickedEnv,
        })
      : null;
  /* Several versions compared: one run each, so one Ready to run each —
     the switch above it says which one is shown. */
  const multi = !otherAgent && versions.length > 1;
  const [viewVersion, setViewVersion] = useState(null);
  const shownVersion =
    multi && versions.includes(viewVersion) ? viewVersion : versions[0];
  const currentEnvAgentLabel = currentAgentVersion(envState)?.label || null;
  const compat = reportFor(
    otherAgent ? otherAgent.version : shownVersion || null,
  );
  const versionReports = multi
    ? versions.map((v) => ({ v, report: reportFor(v) }))
    : [];
  /* A multi-agent app runs with its coordinator and workers together. */
  const participants = otherAgent ? [] : membersOf(envState?.agent);
  const participantsEvidence = participants.length
    ? delegationEvidenceOf(envState?.agent)
    : null;
  const [cap, setCap] = useState(null);
  const [capTouched, setCapTouched] = useState(false);
  const [alertAt, setAlertAt] = useState(WORKSPACE_POLICY.alertAt);
  const [showBreakdown, setShowBreakdown] = useState(false);
  /* A re-run uses the source run's limits; a new run the environment's. */
  const startLimits = () => ({
    ...runLimitsOf(env, envState),
    ...(baseline?.turnLimit && { turnLimit: baseline.turnLimit }),
    ...(baseline?.limits || {}),
  });
  const [limits, setLimits] = useState(startLimits);
  const [showLimits, setShowLimits] = useState(false);
  const [showSuggested, setShowSuggested] = useState(false);
  /* Blocked cases are held back, never run (Karthik's run readiness): the
     button names the choice — "Run 6 of 10" — and the 4 left out are
     recorded on the run as not run (TH-8880). */
  const blockedN = compat?.scope?.blocked?.length || 0;
  /* After a failed or timed-out check the whole pairing is refused. */
  const pairFailed = ["failed", "timed_out"].includes(
    compat?.validation?.state,
  );
  const heldAll = (r) => !!r?.scope?.blocked?.length && !r.scope.runnable;
  const allHeld = multi
    ? versionReports.some((x) => heldAll(x.report))
    : heldAll(compat);
  /* A stop that can't be confirmed is said before a paid run (TH-8890). */
  /* What the run takes: the cases not held back. The requested count stays
     the denominator. */
  const runCount = blockedN > 0 ? compat.scope.runnable : shownCount;
  /* The cases left out, grouped by what blocks them. */
  const leftOut = Object.entries(
    (compat?.scope?.blocked || []).reduce((m, b) => {
      const k = b.by.join(", ").toLowerCase();
      (m[k] = m[k] || []).push(String(b.title).replace(/\.$/, ""));
      return m;
    }, {}),
  )
    /* The count and what blocks them; Ready to run names the scenarios. */
    .map(([by, list]) => `${list.length} that need ${by}`)
    .join(" · ");

  /* Each opening starts from the header's attempts and the current version. */
  useEffect(() => {
    if (!open) return;
    setAttempts(initialAttempts || 3);
    setVersions(
      initialVersion && versionOptions.some((o) => o.label === initialVersion)
        ? [initialVersion]
        : versionOptions.slice(0, 1).map((o) => o.label),
    );
    setMoreVersions(false);
    setAgentKey(initialAgentKey);
    setSuiteId("all");
    setPicked(null);
    setSuiteVer(null);
    setAddEvalIds([]);
    setCapTouched(false);
    setAlertAt(WORKSPACE_POLICY.alertAt);
    setShowBreakdown(false);
    setLimits(startLimits());
    setShowLimits(false);
    setShowSuggested(false);
    setEnvVer(initialEnvVersion || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /* Opened from a link before the store loaded: the versions arrive after
     the dialog did — pick the current one then. */
  useEffect(() => {
    if (open && !versions.length && versionOptions.length)
      setVersions(
        initialVersion && versionOptions.some((o) => o.label === initialVersion)
          ? [initialVersion]
          : versionOptions.slice(0, 1).map((o) => o.label),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, versionOptions.length]);

  /* A run group prices every environment it starts, not just this one. */
  const estimate = useMemo(
    () =>
      mergeEstimates(
        estimateRunPlan({
          env,
          envState,
          scenarios: runCount,
          attempts: Number(attempts) || 0,
          versions,
          evals,
          runLocation,
          missingRate,
        }),
        alsoScopes.map((a) =>
          estimateRunPlan({
            env: a.env,
            envState: a.envState,
            scenarios: a.scenarios,
            attempts: Number(attempts) || 0,
            versions: [versions[0]],
            evals: a.evals,
            runLocation: a.envState?.agent?.values?.runLocation || null,
            missingRate,
          }),
        ),
      ),
    [
      env,
      envState,
      runCount,
      attempts,
      versions,
      evals,
      runLocation,
      missingRate,
      alsoScopes,
    ],
  );
  /* The limit follows the estimate until it's edited by hand. */
  const suggested = defaultRunCap(estimate);
  const effectiveCap = capTouched ? cap : suggested;
  /* This month's usage, from the same ledger Settings → Usage reads. */
  const { state } = useSimStore();
  const me = currentUser().name;
  /* The plan's free tier is used first: what's left of it this month. */
  const freeLine = useMemo(() => {
    if (!open) return "";
    const meter = voice ? "voice_sim_minutes" : "text_sim_tokens";
    const used = simMeters(state, { since: monthStart() });
    const left = Math.max(0, FREE_TIER[meter] - (used[meter] || 0));
    const evalsLeft = Math.max(
      0,
      FREE_TIER.ai_credits - (used.ai_credits || 0),
    );
    if (!left && !evalsLeft) return "free tier used up this month";
    const r = RATE_PLAN.rates;
    const credits =
      (voice
        ? left * r.voice_sim_minutes.credits
        : (left / r.text_sim_tokens.per) * r.text_sim_tokens.credits) +
      evalsLeft;
    return `free tier covers the first ≈ ${Math.round(credits).toLocaleString()} credits`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, voice]);
  const ledger = useMemo(() => {
    if (!open) return { total: 0, project: 0, user: 0, heldRuns: 0, held: 0 };
    const m = monthUsage(state);
    const p = projectOf(env);
    /* Used, plus what runs still playing hold. */
    return {
      total: m.total + m.held.total,
      project: (m.byProject[p] || 0) + (m.held.byProject[p] || 0),
      user: (m.byUser[me] || 0) + (m.held.byUser[me] || 0),
      heldRuns: m.held.runs,
      held: m.held.total,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const check = checkRunPlan(
    estimate,
    effectiveCap,
    WORKSPACE_POLICY,
    ledger.total,
    projectOf(env),
    ledger.project,
    { user: me, userUsed: ledger.user },
  );
  /* Runs of a pairing wait while its check runs and are refused after it
     failed or timed out; a check unavailable on our side holds nothing. With
     every selected case held back there's no run to start. */
  const pairHold = !compat
    ? null
    : checkingCompat
      ? {
          title: "Waiting for the check",
        }
      : pairFailed
        ? {
            title: "Can't start until a check passes",
          }
        : allHeld
          ? {
              title: "Every selected scenario waits on a fix",
            }
          : null;
  const canStart = check.canStart && !pairHold;
  /* No scenario ticked, nothing to run. */
  /* Nothing can run (the check failed, or every selected scenario waits on a
     fix): say why once, never "0 scenarios" or "pick a scenario". */
  const noRun = pairFailed || (!multi && allHeld);
  /* The verdict (14 Nov information flow, Chintan's review): one line says
     whether the run can start and how many scenarios run; the button
     follows it and nothing else on the drawer repeats it. Stale and
     excluded ride on the same line; why each part is left out is on hover. */
  const fromSuite = suite ? null : bd;
  const envLbl = pickedEnv || currentEnvLabel;
  /* Read as "how many run, and why the rest don't" (#58): the number that
     runs first, then what holds the others back, in plain words. */
  const outParts = [
    blockedN > 0 && `${blockedN} need a change on the agent`,
    fromSuite?.blocked > 0 &&
      `${fromSuite.blocked} can't run on environment ${envLbl}`,
    fromSuite?.excluded > 0 && `${fromSuite.excluded} left out`,
  ].filter(Boolean);
  const staleN = fromSuite?.stale || 0;
  const outTip = [
    blockedN > 0 &&
      `Blocked for this agent: held back and recorded as not run (${leftOut}).`,
    fromSuite?.blocked > 0 &&
      fromSuite.blockedWhy &&
      `Blocked on ${envLbl}: ${fromSuite.blockedWhy}`,
    fromSuite?.excluded > 0 &&
      fromSuite.excludedWhy &&
      `Excluded: ${fromSuite.excludedWhy}`,
    staleN > 0 &&
      `Stale: they run but count toward no decision until proved on ${envLbl}.`,
  ]
    .filter(Boolean)
    .join("\n");
  const totalN =
    runCount +
    blockedN +
    (fromSuite?.blocked || 0) +
    (fromSuite?.excluded || 0);
  const heldN = totalN - runCount;
  /* The count leads; the notes follow (handover: the dialog led with notes). */
  const countLead = outParts.length
    ? `${runCount} of ${totalN} will run`
    : `All ${runCount} will run`;
  /* Held back and changed-since-proved are listed by name under the line
     (RunHeldNotes), so the line itself only says how many will run. */
  const notesShown =
    blockedN > 0 ||
    (fromSuite?.blockedList || []).length > 0 ||
    (fromSuite?.staleIds || []).length > 0;
  const countRest = notesShown
    ? []
    : [
        outParts.length ? `${heldN} won't: ${outParts.join(", ")}` : null,
        staleN > 0 ? `${staleN} stale` : null,
      ].filter(Boolean);
  const pairVal = compat?.validation;
  const pairState = checkingCompat ? "validating" : pairVal?.state;
  const areasNow = compat
    ? readinessOf(compat, envState?.scenarios || [], (evals || []).length)
    : [];
  const anyBlocker = areasNow.some((a) => a.status === "blocked");
  const anyUnchecked = areasNow.some((a) => a.status === "unchecked");
  const agentVerLbl =
    compat?.identity?.agentVersion ||
    otherAgent?.version ||
    versions[0] ||
    "v1";
  const sentence = (t) => String(t || "").replace(/^./, (c) => c.toLowerCase());
  /* ── the readiness check, inline: whenever the agent, its version or the
     environment differs from the run being repeated (or the pairing last
     checked), the pair is checked here before Run is active. ── */
  const ownKey = agentChoices.find((a) => a.current)?.key || null;
  const pairId = pairIdOf(pickedKey, agentVerLbl, envLbl);
  const baseId = baseline
    ? pairIdOf(
        baseline.agentKey || ownKey,
        baseline.agentVersion,
        baseline.envVersion,
      )
    : null;
  const [checked, setChecked] = useState({});
  const [pendingCheck, setPendingCheck] = useState(null);
  const [checkTimerDone, setCheckTimerDone] = useState(null);
  useEffect(() => {
    if (!open) return;
    /* A check already on record for the pairing the plan opens on counts. */
    setChecked(compat?.validation?.state === "ready" ? { [pairId]: true } : {});
    setPendingCheck(null);
    setCheckTimerDone(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const needsCheck = open
    ? needsCheckOf({
        hasCheck: !!compat && agentChoices.length > 0,
        pairId,
        baseId,
        checked,
      })
    : false;
  const startCheck = () => {
    setPendingCheck(pairId);
    setCheckTimerDone(null);
    if (onCheckCompat && canCheckCompat?.(pickedKey)) onCheckCompat(pickedKey);
    const id = pairId;
    setTimeout(() => setCheckTimerDone(id), 1200);
  };
  useEffect(() => {
    if (needsCheck && !pendingCheck && !checkingCompat) startCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsCheck, pendingCheck, pairId]);
  useEffect(() => {
    if (pendingCheck && checkTimerDone === pendingCheck && !checkingCompat) {
      setChecked((c) => ({ ...c, [pendingCheck]: true }));
      setPendingCheck(null);
    }
  }, [pendingCheck, checkTimerDone, checkingCompat]);
  /* checking → ready | problems, for this pairing. */
  const checkState =
    !compat || !agentChoices.length
      ? null
      : needsCheck || pendingCheck === pairId || checkingCompat
        ? "checking"
        : anyBlocker || pairFailed
          ? "problems"
          : "ready";
  const checkProblems = checkProblemsOf(areasNow);
  const checkHold = checkState === "checking";
  const sameAsSource = !!baseline && pairId === baseId;
  const nothingPicked = listActive && selIds.length === 0;
  const canRun = canStart && !checkHold && !nothingPicked;
  /* Same agent and environment as the run being re-run: it already ran
     there, so it reads ready and the check's detail stays folded (24 Nov
     review: one readiness answer, not two that disagree). */
  const readyAsSource = sameAsSource && !anyBlocker && !pairFailed;
  const verdict = !compat
    ? null
    : readyAsSource
      ? {
          dot: "passed",
          title: `All ${runCount} ready`,
          rest: `same agent and environment as ${baseline.label || "the run"}`,
        }
      : pairState === "validating"
        ? {
            dot: "running",
            live: true,
            title: `Checking agent ${agentVerLbl} on environment ${envLbl}`,
            rest: `up to ${PAIR_CHECK_LIMIT_MIN} min`,
          }
        : pairFailed
          ? {
              dot: "failed",
              title: "Can't start",
              rest:
                pairState === "timed_out"
                  ? `the check ran past ${PAIR_CHECK_LIMIT_MIN} min · ${ago(pairVal.at)}`
                  : `${sentence(
                      String(
                        areasNow.find((a) => a.status === "blocked")?.detail ||
                          pairVal?.reason ||
                          "the check failed",
                      )
                        .split(/\s+—\s+|(?<=\.)\s/)[0]
                        .replace(/\.$/, ""),
                    )} · checked ${ago(pairVal.at)}`,
            }
          : !multi && allHeld
            ? {
                dot: "failed",
                title: "Can't start",
                /* The scenarios picked above, not every scenario the
                 environment has — the same number the plan shows. */
                rest: `all ${blockedN || shownCount} scenarios are blocked`,
              }
            : pairState === "unavailable"
              ? {
                  dot: "cancelled",
                  title: countLead,
                  rest: [
                    ...countRest,
                    "the check couldn't run on our side, so nothing is held",
                  ].join(" · "),
                }
              : pairState === "stale"
                ? {
                    dot: "queued",
                    hollow: true,
                    title: countLead,
                    /* Why the check is out of date is on Ready to run. */
                    rest: countRest.join(" · "),
                  }
                : anyUnchecked && !anyBlocker && !pairVal
                  ? {
                      dot: "queued",
                      hollow: true,
                      title: countLead,
                      rest: [
                        ...countRest,
                        "not checked for this agent yet",
                      ].join(" · "),
                    }
                  : outParts.length
                    ? {
                        dot: "flaky",
                        title: `${runCount} of ${totalN} will run`,
                        rest: notesShown
                          ? ""
                          : [
                              `${heldN} won't: ${outParts.join(", ")}`,
                              staleN > 0 && `${staleN} stale`,
                            ]
                              .filter(Boolean)
                              .join(" · "),
                      }
                    : {
                        dot: "passed",
                        title: `All ${runCount} will run`,
                        rest: staleN && !notesShown ? `${staleN} stale` : "",
                      };
  /* A first run of this agent version on this environment version prepares
     the pair first. */
  const ranBefore = (envState?.runs || []).some(
    (r) =>
      (r.envVersion || currentEnvLabel) === envLbl &&
      (!r.agentKey || !pickedKey || r.agentKey === pickedKey) &&
      (!r.agentVersion || r.agentVersion === agentVerLbl),
  );
  /* The estimate can be over the admin's per-run limit however the limit is
     set — then the most attempts that fit is the useful suggestion. */
  const overPolicy =
    estimate.trials > 0 &&
    estimate.platform.high > WORKSPACE_POLICY.maxRunCredits;
  const perAttempt = estimate.platform.high / (Number(attempts) || 1);
  const fitAttempts = perAttempt
    ? Math.floor(WORKSPACE_POLICY.maxRunCredits / perAttempt)
    : 0;
  /* Calls already running when the limit is reached still finish. */
  const perTrialHigh = estimate.trials
    ? estimate.platform.high / estimate.trials
    : 0;
  const overrun = Math.ceil(perTrialHigh * Math.max(1, parallel || 1));

  /* The plan's word for one scenario run once, voice or text (glossary: Trial). */
  const unit = "trial";
  const external = estimate.external[0] || null;
  const fieldSx = {
    "& .MuiInputBase-input": { py: 0.5, fontVariantNumeric: "tabular-nums" },
  };

  /* Title: what this run does. */
  const rerunIds = (initialScenarioIds || []).filter(Boolean);
  const kindOf = () => {
    if (titleKind) return titleKind;
    if (!rerunIds.length || !scenarioStatus) return "scenarios";
    const st = rerunIds.map((id) => scenarioStatus[id]);
    if (st.every((s) => s === "failed")) return "failed";
    if (st.every((s) => s === "flaky")) return "flaky";
    return "scenarios";
  };
  const isRerun = !!baseline;
  const titleN = isRerun && rerunIds.length ? rerunIds.length : runCount;
  const titleText = isRerun
    ? `Re-run ${titleN} ${kindOf()}`
    : `Run ${titleN} ${titleN === 1 ? "scenario" : "scenarios"}`;
  const fromRun =
    isRerun && /^run\s+\d+/i.test(baseline.label || "")
      ? baseline.label.replace(/^./, (c) => c.toUpperCase())
      : null;
  const subtitle = [env?.name || "Environment", fromRun && `from ${fromRun}`]
    .filter(Boolean)
    .join(" · ");
  /* Tools that run for real on a platform agent, said on hover. */
  const liveTools = (() => {
    const v = envState?.agent?.values || {};
    if (!v.provider || !v.agentId) return [];
    const a = assistantsFor(v.provider).find((x) => x.id === v.agentId) || {
      id: v.agentId,
      name: envState?.agent?.name,
    };
    return (platformPreviewOf(v.provider, a, { voice })?.tools || []).filter(
      (t) => t.handling === "live",
    );
  })();
  const readinessTip = [
    liveTools.length
      ? `Runs for real: ${liveTools.map((t) => t.name).join(", ")}. Your own tools are redirected to the test world.`
      : null,
    compat && !noRun && !ranBefore
      ? `First run prepares agent ${agentVerLbl} on environment ${envLbl}; it takes several minutes.`
      : null,
    outTip || null,
  ]
    .filter(Boolean)
    .join("\n");
  const needsFix =
    !!compat &&
    checkState !== "checking" &&
    (checkState === "problems" ||
      blockedN > 0 ||
      anyBlocker ||
      notesShown ||
      ["unavailable", "stale"].includes(pairState) ||
      (anyUnchecked && !pairVal));
  const readyDot =
    checkState === "checking"
      ? { dot: "running", live: true }
      : checkState === "problems"
        ? { dot: "failed" }
        : { dot: verdict?.dot || "passed", hollow: verdict?.hollow };
  const nProblems = checkProblems.length || 1;
  const readyText =
    checkState === "checking"
      ? "Checking…"
      : checkState === "problems"
        ? `${nProblems} problem${nProblems === 1 ? "" : "s"}`
        : outParts.length
          ? `Ready · ${runCount} of ${totalN} will run`
          : "Ready";
  const cantRun = listActive ? scopeRows.filter((r) => !r.ok).length : 0;
  const cantRunN =
    wantedOut.length || (initialScenarioIds?.length ? 0 : cantRun);
  const cantRunWhy =
    wantedOutWhy ||
    [
      ...new Set(
        scopeRows
          .filter((r) => !r.ok)
          .map((r) => r.why)
          .filter(Boolean),
      ),
    ].join("; ");
  const evalNames = (evals || []).length
    ? (evals || []).map((e) => e.name).join(", ")
    : "None added";

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 560 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          sx={{ px: 3, pt: 2.5, pb: 1, flexShrink: 0 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              {titleText}
            </Typography>
            <Typography
              noWrap
              title={(() => {
                const entry = catalogueOfEnv(env);
                if (!entry) return undefined;
                return [
                  `${entry.id} · ${entry.task}`,
                  `release ${envReleaseOf(env, entry)}`,
                ].join(" · ");
              })()}
              sx={{ typography: "s2", color: "text.tertiary", fontWeight: 400 }}
            >
              {subtitle}
            </Typography>
          </Box>
          <IconButton aria-label="Close" onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        {/* Settings on top; the scenarios to run fill the rest, so the
          drawer is never half empty (25 Nov). */}
        <Box
          sx={{
            px: 3,
            pb: 1,
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflowY: "auto",
          }}
        >
          <Box
            sx={{
              mt: 1,
              "& > .kv:first-of-type": { borderTop: 0 },
            }}
          >
            {/* Scenario version */}
            <KV label="Scenario version">
              {baseline ? (
                <Typography sx={VALUE_SX}>
                  {verName(scenarioVer)}
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", ml: 0.75 }}
                  >
                    {`· same as ${fromRun || baseline.label || "the run"}`}
                  </Box>
                </Typography>
              ) : suiteLabels.length > 1 ? (
                <TextField
                  select
                  size="small"
                  value={scenarioVer}
                  onChange={(e) => setSuiteVer(e.target.value)}
                  SelectProps={{ renderValue: (v) => verName(v) }}
                  inputProps={{ "aria-label": "Scenario version" }}
                  sx={{ minWidth: 88, ...fieldSx }}
                >
                  {[...suiteLabels].reverse().map((l) => (
                    <MenuItem key={l} value={l} sx={{ gap: 1 }}>
                      <Box component="span" sx={{ flex: 1 }}>
                        {verName(l)}
                      </Box>
                      {l === currentSuiteLbl && (
                        <Box
                          component="span"
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          current
                        </Box>
                      )}
                    </MenuItem>
                  ))}
                </TextField>
              ) : (
                <Typography sx={VALUE_SX}>{verName(scenarioVer)}</Typography>
              )}
            </KV>

            {/* Agent */}
            <KV label="Agent">
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                minWidth={0}
              >
                <Box sx={{ ...VALUE_SX, minWidth: 0 }}>
                  {agentChoices.length > 1 ? (
                    <TextField
                      select
                      size="small"
                      value={
                        agentKey ||
                        agentChoices.find((a) => a.current)?.key ||
                        ""
                      }
                      onChange={(e) => setAgentKey(e.target.value)}
                      SelectProps={{
                        renderValue: (k) =>
                          agentChoices.find((a) => a.key === k)?.name || "",
                      }}
                      inputProps={{ "aria-label": "Agent" }}
                      sx={{ minWidth: 220, ...fieldSx }}
                    >
                      {agentChoices.map((a) => (
                        <MenuItem key={a.key} value={a.key} sx={{ gap: 1 }}>
                          <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
                            {a.name}
                          </Box>
                          <Box
                            component="span"
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {a.current ? "this environment's" : a.version}
                          </Box>
                        </MenuItem>
                      ))}
                    </TextField>
                  ) : (
                    agentChoices[0]?.name || envState?.agent?.name || "—"
                  )}
                </Box>
                {otherAgent ? (
                  <TextField
                    select
                    size="small"
                    value={otherAgent.version}
                    onChange={(e) => setOtherVersionPick(e.target.value)}
                    inputProps={{ "aria-label": "Agent version" }}
                    sx={{ minWidth: 88, ...fieldSx }}
                  >
                    {(otherAgent.versions?.length
                      ? otherAgent.versions
                      : [otherAgent.version]
                    ).map((v, i) => (
                      <MenuItem key={v} value={v} sx={{ gap: 1 }}>
                        <Box component="span" sx={{ flex: 1 }}>
                          {v}
                        </Box>
                        {i === 0 && (
                          <Box
                            component="span"
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            production
                          </Box>
                        )}
                      </MenuItem>
                    ))}
                  </TextField>
                ) : (
                  <TextField
                    select
                    size="small"
                    value={versions}
                    /* One version per run, as the plan's Run setup has it;
                       comparing versions is Runs → Compare (#59, #60). */
                    onChange={(e) => {
                      const v = e.target.value;
                      /* "More versions…" opens the older ones; it's not a pick. */
                      if (v === MORE) {
                        setMoreVersions(true);
                        return;
                      }
                      setVersions([v]);
                    }}
                    inputProps={{ "aria-label": "Agent version" }}
                    /* Just the version in the field ("v4"); "current" and the
                     rest stay in the menu. */
                    SelectProps={{
                      renderValue: (v) => (Array.isArray(v) ? v.join(", ") : v),
                    }}
                    sx={{ minWidth: 88, ...fieldSx }}
                  >
                    {versionOptions
                      .filter(
                        (o) =>
                          moreVersions || !o.more || versions.includes(o.label),
                      )
                      .map((o) => (
                        <MenuItem key={o.label} value={o.label} sx={{ gap: 1 }}>
                          <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
                            {o.name}
                          </Box>
                          {o.tag && (
                            <Box
                              component="span"
                              sx={{
                                typography: "s3",
                                color:
                                  o.tag === "draft"
                                    ? "status.warning.fg"
                                    : "text.tertiary",
                              }}
                            >
                              {o.tag}
                            </Box>
                          )}
                        </MenuItem>
                      ))}
                    {!moreVersions && versionOptions.some((o) => o.more) && (
                      <MenuItem value={MORE}>
                        <Box component="span" sx={{ color: "text.secondary" }}>
                          {`More versions (${versionOptions.filter((o) => o.more).length})…`}
                        </Box>
                      </MenuItem>
                    )}
                  </TextField>
                )}
              </Stack>
            </KV>

            {/* Environment */}
            <KV label="Environment">
              <Stack direction="row" spacing={1} alignItems="center">
                {/* A re-run keeps the environment it re-runs, like the
                    scenario version, so the result compares; only the agent
                    version changes (25 Nov). */}
                {baseline ? (
                  <Typography sx={VALUE_SX}>
                    {pickedEnv || currentEnvLabel}
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", ml: 0.75 }}
                    >
                      {`· same as ${fromRun || baseline.label || "the run"}`}
                    </Box>
                  </Typography>
                ) : envVersionOptions.length > 1 ? (
                  <TextField
                    select
                    size="small"
                    value={pickedEnv || currentEnvLabel}
                    onChange={(e) => {
                      setEnvVer(e.target.value);
                      setPicked(null);
                    }}
                    SelectProps={{ renderValue: (v) => v }}
                    inputProps={{ "aria-label": "Environment version" }}
                    sx={{ minWidth: 88, ...fieldSx }}
                  >
                    {envVersionOptions.map((v) => (
                      <MenuItem key={v.label} value={v.label} sx={{ gap: 1 }}>
                        <Box component="span" sx={{ flex: 1 }}>
                          {v.label}
                        </Box>
                        {v.current && (
                          <Box
                            component="span"
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                            }}
                          >
                            current
                          </Box>
                        )}
                      </MenuItem>
                    ))}
                  </TextField>
                ) : (
                  <Box component="span">{currentEnvLabel}</Box>
                )}
                {validatingOf(envState) && (
                  <Tooltip
                    arrow
                    title="Validate is running — this run uses the saved version"
                  >
                    <Typography sx={{ ...VALUE_SX, color: "text.tertiary" }}>
                      · saved version
                    </Typography>
                  </Tooltip>
                )}
              </Stack>
            </KV>

            {/* Trials */}
            <KV label="Trials per scenario">
              {noRun ? (
                <Typography sx={{ ...VALUE_SX, color: "text.tertiary" }}>
                  —
                </Typography>
              ) : (
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  flexWrap="wrap"
                >
                  <TextField
                    size="small"
                    type="number"
                    value={attempts}
                    onChange={(e) => setAttempts(e.target.value)}
                    inputProps={{
                      min: 1,
                      max: 20,
                      "aria-label": "Trials per scenario",
                      style: { textAlign: "right" },
                    }}
                    sx={{ width: 64, ...fieldSx }}
                  />
                  <Tooltip arrow title={scopeNote || ""}>
                    <Typography
                      sx={{
                        ...VALUE_SX,
                        color: "text.tertiary",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {/* The math in view: "× 12 scenarios = 36 conversations". */}
                      {estimate.trials === runCount * Number(attempts)
                        ? `× ${runCount} scenario${runCount === 1 ? "" : "s"} = ${estimate.trials} conversation${estimate.trials === 1 ? "" : "s"}`
                        : `· ${estimate.trials} conversation${estimate.trials === 1 ? "" : "s"}`}
                    </Typography>
                  </Tooltip>
                  {trialsHelpOf(attempts) && (
                    <Typography sx={{ ...VALUE_SX, color: "text.tertiary" }}>
                      · Use 3+ trials to spot flaky
                    </Typography>
                  )}
                </Stack>
              )}
            </KV>

            {/* Readiness */}
            {(compat || verdict) && (
              <KV
                label="Readiness"
                detail={
                  <Stack
                    spacing={1}
                    sx={{ pb: 1, "&:empty": { display: "none" } }}
                  >
                    {needsFix && (
                      <RunHeldNotes
                        report={compat}
                        scenarios={envState?.scenarios || []}
                        staleIds={fromSuite?.staleIds || []}
                        envHeld={fromSuite?.blockedList || []}
                      />
                    )}
                    {needsFix && compat && multi && (
                      <ToggleButtonGroup
                        exclusive
                        size="small"
                        value={shownVersion}
                        onChange={(_, v) => v && setViewVersion(v)}
                        aria-label="Ready to run for version"
                      >
                        {versionReports.map(({ v, report }) => {
                          const held = report?.scope?.blocked?.length || 0;
                          return (
                            <ToggleButton key={v} value={v}>
                              {held
                                ? `${v} · ${report.scope.runnable} of ${report.scope.requested}`
                                : `${v} · all ready`}
                            </ToggleButton>
                          );
                        })}
                      </ToggleButtonGroup>
                    )}
                    {needsFix && compat && !readyAsSource && (
                      <ReadyToRun
                        report={compat}
                        scenarios={envState?.scenarios || []}
                        evalCount={(evals || []).length}
                        onFix={(row) => onRepairCompat?.(row, pickedKey)}
                        fixKit={fixKitOf ? fixKitOf(pickedKey) : null}
                        onFixInBuilder={
                          onFixInBuilder
                            ? (jobs) => onFixInBuilder(jobs, pickedKey)
                            : null
                        }
                        onCheck={
                          onCheckCompat && canCheckCompat?.(pickedKey)
                            ? () => onCheckCompat(pickedKey)
                            : undefined
                        }
                        checking={checkingCompat}
                      />
                    )}
                    {!otherAgent && (
                      <ToolFitRow
                        env={env}
                        envState={envState}
                        patch={patch || undefined}
                        agent={shownVersion || currentEnvAgentLabel}
                        locked={!patch}
                      />
                    )}
                    {/* Provider limits are information, not a fix — shown only
                      when the check found a problem. */}
                    {checkState === "problems" && compat && (
                      <KnownLimits
                        report={compat}
                        scenarios={envState?.scenarios || []}
                        evalCount={(evals || []).length}
                        provider={
                          fixKitOf?.(pickedKey)?.agent?.values?.provider ||
                          envState?.agent?.values?.provider
                        }
                      />
                    )}
                  </Stack>
                }
              >
                <Stack direction="row" spacing={1} alignItems="center">
                  <StatusDot
                    status={readyDot.dot}
                    size={8}
                    hollow={!!readyDot.hollow}
                    live={!!readyDot.live}
                  />
                  <Typography sx={VALUE_SX}>{readyText}</Typography>
                  {readinessTip && (
                    <Tooltip
                      arrow
                      title={
                        <Box sx={{ whiteSpace: "pre-line" }}>
                          {readinessTip}
                        </Box>
                      }
                    >
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <Iconify
                          icon="solar:info-circle-linear"
                          width={14}
                          sx={{ color: "text.disabled" }}
                        />
                      </Box>
                    </Tooltip>
                  )}
                </Stack>
              </KV>
            )}

            {/* Evaluations */}
            <KV
              label="Evaluations"
              detail={
                showSuggested && pickable.length > 0 ? (
                  <Box sx={{ pb: 1 }}>
                    {pickable.length > 0 && (
                      <Stack
                        direction="row"
                        alignItems="center"
                        gap={0.75}
                        flexWrap="wrap"
                      >
                        <Box component="span" sx={{ color: "text.tertiary" }}>
                          Suggested
                        </Box>
                        {pickable.map((e) => {
                          const on = addEvalIds.includes(e.id);
                          const full = !on && addEvalIds.length >= evalRoom;
                          return (
                            <Tooltip
                              key={e.id}
                              arrow
                              title={
                                full
                                  ? "No room for more evaluations on this environment."
                                  : e.blurb || e.description || ""
                              }
                            >
                              <span>
                                <Chip
                                  size="small"
                                  label={e.name}
                                  disabled={full}
                                  onClick={() => toggleEval(e.id)}
                                  icon={
                                    <Iconify
                                      icon={
                                        on
                                          ? "solar:check-read-linear"
                                          : "solar:add-circle-linear"
                                      }
                                      width={12}
                                    />
                                  }
                                  variant={on ? "filled" : "outlined"}
                                  sx={{
                                    height: 20,
                                    borderRadius: "4px",
                                    typography: "s3",
                                    fontWeight: 500,
                                    ...(on
                                      ? {
                                          bgcolor: "action.selected",
                                          color: "text.primary",
                                        }
                                      : { color: "text.secondary" }),
                                    "& .MuiChip-icon": {
                                      ml: "6px",
                                      mr: "-2px",
                                      color: "inherit",
                                    },
                                    "& .MuiChip-label": { px: "6px" },
                                  }}
                                />
                              </span>
                            </Tooltip>
                          );
                        })}
                      </Stack>
                    )}
                    {/* A re-run grades like its source: a pick grades the
                        source's saved calls too, so the two still compare. */}
                    {baseline && (
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          mt: 0.75,
                        }}
                      >
                        {`Also grades ${fromRun || "the run"}'s saved calls · no new calls · so both runs still compare`}
                      </Typography>
                    )}
                  </Box>
                ) : null
              }
            >
              <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
                <Typography sx={VALUE_SX}>
                  {evalNames}
                  {/* A re-run grades with the same evaluations, so its
                      result compares; one added here grades both runs. */}
                  {baseline && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", ml: 0.75 }}
                    >
                      {`· same as ${fromRun || baseline.label || "the run"}`}
                    </Box>
                  )}
                  {pickable.length > 0 && (
                    <Box
                      component="button"
                      type="button"
                      onClick={() => setShowSuggested((v) => !v)}
                      aria-expanded={showSuggested}
                      sx={{
                        ...LINK_SX,
                        color: "text.tertiary",
                        ml: 0.75,
                        "&:hover": { color: "text.primary" },
                        /* A re-run's row already ends "same as Run N":
                           the link takes its own line, not a wrapped dot. */
                        ...(baseline && { display: "block", ml: 0 }),
                      }}
                    >
                      {`${baseline ? "" : "· "}${
                        addEvalIds.length
                          ? `+${addEvalIds.length} added`
                          : `+${pickable.length} suggested`
                      }`}
                    </Box>
                  )}
                </Typography>
                <Tooltip
                  arrow
                  title={
                    baseline
                      ? `A re-run grades like ${fromRun || "the run"}. A suggested evaluation you pick grades both, so they still compare.`
                      : pickable.length
                        ? "Suggested evaluations score a run only if you pick them. The scenarios' own checks always run."
                        : "The scenarios' own checks always run."
                  }
                >
                  <Box
                    component="span"
                    sx={{ display: "inline-flex", ml: 0.75 }}
                  >
                    <Iconify
                      icon="solar:info-circle-linear"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  </Box>
                </Tooltip>
              </Stack>
            </KV>

            {/* Participants */}
            {participants.length > 0 && (
              <KV label="Participants">
                <Stack direction="row" alignItems="center">
                  <Typography sx={VALUE_SX}>
                    {`${participants[0]?.name || "Coordinator"} + ${participants.length - 1} worker${participants.length === 2 ? "" : "s"}`}
                  </Typography>
                  <Tooltip
                    arrow
                    title={`${participants.map((m) => m.name).join(", ")}. ${
                      participantsEvidence?.source === "instrumented"
                        ? "Hand-offs are observed in this run."
                        : "Hand-offs aren't observed; only the overall result counts."
                    }`}
                  >
                    <Box
                      component="span"
                      sx={{ display: "inline-flex", ml: 0.75 }}
                    >
                      <Iconify
                        icon="solar:info-circle-linear"
                        width={14}
                        sx={{ color: "text.disabled" }}
                      />
                    </Box>
                  </Tooltip>
                </Stack>
              </KV>
            )}

            {/* Limits */}
            <KV
              label="Limits"
              /* Same limits on a re-run: a different turn or time limit can
                 cause or hide a failure. */
              action={
                baseline ? null : (
                  <TextLink onClick={() => setShowLimits((v) => !v)}>
                    {showLimits ? "Done" : "Edit"}
                  </TextLink>
                )
              }
              detail={
                <Collapse in={showLimits}>
                  <Box sx={{ pb: 1 }}>
                    {seedable && (
                      <Row
                        label="Seed"
                        detail={
                          seedFrom
                            ? `Same callers and data as ${seedFrom}`
                            : "Saved with the run"
                        }
                        value={seedFrom ? `Same as ${seedFrom}` : "New"}
                        muted
                      />
                    )}
                    <Row
                      label={voice ? "Longest call" : "Per-trial timeout"}
                      detail="Then it fails as timed out"
                    >
                      <TextField
                        select
                        size="small"
                        value={limits.trialTimeout}
                        onChange={(e) =>
                          setLimits((l) => ({
                            ...l,
                            trialTimeout: Number(e.target.value),
                          }))
                        }
                        inputProps={{ "aria-label": "Per-trial timeout" }}
                        sx={{ width: 120, ...fieldSx }}
                      >
                        {TRIAL_TIMEOUTS.map((m) => (
                          <MenuItem key={m} value={m}>
                            {minutesLabel(m)}
                          </MenuItem>
                        ))}
                      </TextField>
                    </Row>
                    <Row
                      label="Whole-run deadline"
                      detail={`No new ${unit}s after it`}
                    >
                      <TextField
                        select
                        size="small"
                        value={limits.runDeadline}
                        onChange={(e) =>
                          setLimits((l) => ({
                            ...l,
                            runDeadline: Number(e.target.value),
                          }))
                        }
                        inputProps={{ "aria-label": "Whole-run deadline" }}
                        sx={{ width: 120, ...fieldSx }}
                      >
                        {RUN_DEADLINES.map((m) => (
                          <MenuItem key={m} value={m}>
                            {minutesLabel(m)}
                          </MenuItem>
                        ))}
                      </TextField>
                    </Row>
                    <Row
                      label="Turn limit"
                      detail="Set in the environment's Settings"
                      value={`${limits.turnLimit} turns`}
                      muted
                    />
                    {limits.stepLimit ? (
                      <Row
                        label="Tool-step limit"
                        detail="Tool calls per trial"
                      >
                        <TextField
                          select
                          size="small"
                          value={limits.stepLimit}
                          onChange={(e) =>
                            setLimits((l) => ({
                              ...l,
                              stepLimit: Number(e.target.value),
                            }))
                          }
                          inputProps={{ "aria-label": "Tool-step limit" }}
                          sx={{ width: 120, ...fieldSx }}
                        >
                          {STEP_LIMITS.map((m) => (
                            <MenuItem key={m} value={m}>
                              {`${m} steps`}
                            </MenuItem>
                          ))}
                        </TextField>
                      </Row>
                    ) : (
                      <Row
                        label="Tool-step limit"
                        detail="Its tools run on its own platform"
                        value="Not enforced"
                        muted
                      />
                    )}
                  </Box>
                </Collapse>
              }
            >
              <Typography sx={VALUE_SX}>
                {`${minutesLabel(limits.trialTimeout)} a ${voice ? "call" : "trial"} · ${limits.turnLimit} turns`}
                {baseline && (
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", ml: 0.75 }}
                  >
                    {`· same as ${fromRun || baseline.label || "the run"}`}
                  </Box>
                )}
              </Typography>
            </KV>
          </Box>

          {/* Scenarios to run — always open, filling the drawer. */}
          <Box
            sx={{
              mt: 2,
              flex: 1,
              minHeight: 220,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Typography sx={{ ...VALUE_SX, fontWeight: 600 }}>
                Scenarios
              </Typography>
              {cantRunN > 0 && (
                <Tooltip arrow title={cantRunWhy}>
                  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                    {`· ${cantRunN} can't run here`}
                  </Typography>
                </Tooltip>
              )}
              <Box flex={1} />
              {suites.length > 0 && (
                <TextField
                  select
                  size="small"
                  value={suiteId}
                  onChange={(e) => {
                    const v = e.target.value;
                    setSuiteId(v);
                    const x = suites.find((q) => q.id === v);
                    setPicked(x ? x.ids : null);
                  }}
                  inputProps={{ "aria-label": "Suite" }}
                  sx={{ minWidth: 160, ...fieldSx }}
                >
                  <MenuItem value="all">{`All · ${scopeCount}`}</MenuItem>
                  {suites.map((x) => (
                    <MenuItem key={x.id} value={x.id}>
                      {`${x.name} · v${x.version || 1} · ${x.ids.length}`}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            </Stack>
            {listActive ? (
              <ScenarioChecklist
                fill
                /* The ones picked when it opened come first (stable while
                   ticking), so a re-run's 10 aren't lost among 62. */
                rows={(() => {
                  const first = new Set(defaultPick);
                  return [
                    ...scopeRows.filter((r) => first.has(r.id)),
                    ...scopeRows.filter((r) => !first.has(r.id)),
                  ];
                })()}
                selected={selIds}
                onToggle={toggleScenario}
                onAll={() => setPicked([...okIds])}
                onNone={() => setPicked([])}
                envLabel={envLbl}
              />
            ) : (
              <Typography sx={VALUE_SX}>
                {`${shownCount} scenario${shownCount === 1 ? "" : "s"}`}
              </Typography>
            )}
          </Box>

          {/* Simulation runs show no pre-run estimate or cap (clarified plan,
             30 Oct); improvements keep their limits. */}
          {SHOW_RUN_COST && (
            <>
              {/* ── what it costs: one number; the breakdown on request ── */}
              <Box>
                <Stack
                  direction="row"
                  alignItems="baseline"
                  justifyContent="space-between"
                  gap={2}
                >
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    Estimated cost
                  </Typography>
                  <Typography
                    sx={{
                      typography: "m3",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {estimate.platform.unknown
                      ? "Can't estimate"
                      : `≈ ${rangeLabel(estimate.platform.low, estimate.platform.high)}`}
                  </Typography>
                </Stack>
                {external && (
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      textAlign: "right",
                    }}
                  >
                    {`+ ${external.label.toLowerCase().startsWith("your") ? external.label.replace(/^Your/, "your") : external.label} on your own account: ${usdRange(external.low, external.high)}`}
                  </Typography>
                )}
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setShowBreakdown((v) => !v)}
                  aria-expanded={showBreakdown}
                  endIcon={
                    <Iconify
                      icon={
                        showBreakdown
                          ? "solar:alt-arrow-up-linear"
                          : "solar:alt-arrow-down-linear"
                      }
                      width={14}
                    />
                  }
                  sx={{ px: 0.5, ml: -0.5, mt: 0.25 }}
                >
                  {showBreakdown ? "Hide breakdown" : "Show breakdown"}
                </Button>
                <Collapse in={showBreakdown}>
                  <Box sx={{ mt: 0.5 }}>
                    {estimate.lines.map((l) => (
                      <Row
                        key={l.id}
                        label={l.label}
                        detail={l.detail}
                        value={lineValue(l)}
                        muted={l.status !== "priced"}
                      />
                    ))}
                    {estimate.external.map((x) => (
                      <Row
                        key={x.id}
                        label={`${x.label} (not in the total)`}
                        detail={x.source}
                        value={usdRange(x.low, x.high)}
                        muted
                      />
                    ))}
                    {/* A multi-agent app: each worker's own model calls, in the
                    estimate as unknown — never zero. */}
                    {membersOf(envState?.agent).length > 1 &&
                      membersOf(envState?.agent).map((m) => (
                        <Row
                          key={`member-${m.id}`}
                          label={`${m.name} (not in the total)`}
                          detail={`${m.model || "its model"} on your account · shares this run's limit`}
                          value="Unknown"
                          muted
                        />
                      ))}
                    <Row
                      label="Warn me at"
                      detail="Continue, lower the limit or stop"
                    >
                      <TextField
                        select
                        size="small"
                        value={alertAt}
                        onChange={(e) => setAlertAt(Number(e.target.value))}
                        inputProps={{ "aria-label": "Warning threshold" }}
                        sx={{ width: 160, ...fieldSx }}
                      >
                        {ALERTS.map((a) => (
                          <MenuItem
                            key={a}
                            value={a}
                          >{`${Math.round(a * 100)}% of the limit`}</MenuItem>
                        ))}
                      </TextField>
                    </Row>
                  </Box>
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                  >
                    {`${estimate.rateLine} · ${freeLine}`}
                  </Typography>
                </Collapse>
              </Box>

              {/* ── where it stops ── */}
              <Box>
                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  gap={2}
                >
                  <Stack direction="row" alignItems="center" gap={0.5}>
                    <Typography
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      Stop at
                    </Typography>
                    <Tooltip
                      arrow
                      title={`At the limit no new ${unit}s start; running ones finish (up to ~${overrun.toLocaleString()} credits more). Warning at ${Math.round(alertAt * 100)}%.${versions.length > 1 ? ` Shared by the ${versions.length} versions — each run holds ${Math.floor((effectiveCap || 0) / versions.length).toLocaleString()}.` : ""}${alsoCount ? ` Shared by the ${alsoCount + 1} environments — each run holds ${Math.floor((effectiveCap || 0) / (alsoCount + 1)).toLocaleString()}.` : ""}`}
                    >
                      <Iconify
                        icon="solar:info-circle-linear"
                        width={14}
                        sx={{ color: "text.tertiary" }}
                      />
                    </Tooltip>
                  </Stack>
                  <TextField
                    size="small"
                    type="number"
                    value={effectiveCap ?? ""}
                    onChange={(e) => {
                      setCapTouched(true);
                      setCap(
                        e.target.value === "" ? null : Number(e.target.value),
                      );
                    }}
                    error={effectiveCap > WORKSPACE_POLICY.maxRunCredits}
                    InputProps={{
                      endAdornment: (
                        <InputAdornment position="end">credits</InputAdornment>
                      ),
                    }}
                    inputProps={{
                      min: 1,
                      "aria-label": "Run spending limit in credits",
                      style: { textAlign: "right" },
                    }}
                    sx={{ width: 168, ...fieldSx }}
                  />
                </Stack>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    mt: 0.5,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {[
                    `Up to ${WORKSPACE_POLICY.maxRunCredits.toLocaleString()} per run`,
                    check.allowance.limit == null
                      ? null
                      : `${check.allowance.left.toLocaleString()} left this month${ledger.heldRuns ? ` (${ledger.held.toLocaleString()} held by ${ledger.heldRuns} running)` : ""}`,
                    check.project.limit == null
                      ? null
                      : `${check.project.name} ${check.projectLeft.toLocaleString()} left`,
                    check.user.limit == null
                      ? null
                      : `You ${check.user.left.toLocaleString()} left`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
                {check.notes.map((n) => (
                  <Typography
                    key={n}
                    sx={{
                      typography: "s3",
                      color: "status.warning.fg",
                      mt: 0.5,
                      textAlign: "right",
                    }}
                  >
                    {n}
                  </Typography>
                ))}
                {/* Over the admin's per-run limit even at its most: say what fits. */}
                {overPolicy && (
                  <Stack
                    direction="row"
                    justifyContent="flex-end"
                    alignItems="center"
                    gap={1}
                    sx={{ mt: 0.5 }}
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {fitAttempts >= 1
                        ? `${fitAttempts} attempt${fitAttempts === 1 ? "" : "s"} fit${fitAttempts === 1 ? "s" : ""} under ${fmtCredits(WORKSPACE_POLICY.maxRunCredits)}, or ask an admin to raise the per-run limit.`
                        : "Pick fewer scenarios, or ask an admin to raise the per-run limit."}
                    </Typography>
                    {fitAttempts >= 1 && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => setAttempts(fitAttempts)}
                        sx={{
                          color: "text.primary",
                          borderColor: "divider",
                          flexShrink: 0,
                        }}
                      >
                        {`Use ${fitAttempts}`}
                      </Button>
                    )}
                  </Stack>
                )}
              </Box>
            </>
          )}

          {!pairHold && check.holds.length > 0 && (
            <Box
              role="alert"
              sx={{
                border: "1px solid",
                borderColor: "status.danger.border",
                bgcolor: "status.danger.bg",
                borderRadius: "8px",
                px: 1.5,
                py: 1,
              }}
            >
              {check.holds.map((h) => (
                <Box key={h.id} sx={{ "& + &": { mt: 0.75 } }}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      color: "status.danger.fg",
                    }}
                  >
                    {h.title}
                  </Typography>
                  {h.line && (
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {h.line}
                    </Typography>
                  )}
                </Box>
              ))}
            </Box>
          )}
        </Box>

        {(submit === "rejected" || submit === "timeout") && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              mx: 3,
              mb: 1,
              px: 1.5,
              py: 1,
              border: "1px solid",
              borderColor:
                submit === "rejected"
                  ? "status.danger.border"
                  : "status.warning.border",
              borderRadius: "8px",
            }}
          >
            <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
              {submit === "rejected"
                ? "No run created: the world service is unavailable. Nothing was charged."
                : "Couldn't confirm the run started. Find it before starting again."}
            </Typography>
            {submit === "timeout" && env?.id && (
              <Button
                size="small"
                variant="outlined"
                sx={{ flexShrink: 0 }}
                onClick={() => {
                  onClose();
                  navigate(
                    paths.dashboard.simulate.environmentStep(env.id, "runs"),
                  );
                }}
              >
                Find submitted run
              </Button>
            )}
          </Stack>
        )}
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{ px: 3, py: 2, borderTop: 1, borderColor: "divider" }}
        >
          <Button
            size="small"
            variant="outlined"
            onClick={onClose}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Cancel
          </Button>
          <Tooltip
            arrow
            title={
              checkHold
                ? "Waiting for the readiness check"
                : nothingPicked
                  ? "Pick at least one scenario"
                  : canRun
                    ? SHOW_RUN_COST
                      ? `${estimate.trials} ${unit}s · stops at ${fmtCredits(effectiveCap)}`
                      : `${estimate.trials} ${unit}s`
                    : (pairHold && verdict
                        ? `${verdict.title}${verdict.rest ? ` · ${verdict.rest}` : ""}`
                        : check.holds[0]?.title) || ""
            }
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={
                  !canRun || submit === "sending" || submit === "timeout"
                }
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
                sx={{ fontSize: 14, fontWeight: 600, px: 2 }}
                onClick={() => {
                  if (submitDemo === "rejected" || submitDemo === "timeout") {
                    setSubmit("sending");
                    setTimeout(() => setSubmit(submitDemo), 900);
                    return;
                  }
                  onStart({
                    attempts: Number(attempts),
                    envVersion: pickedEnv,
                    versions: otherAgent ? [otherAgent.version] : versions,
                    withAgent: otherAgent
                      ? {
                          key: otherAgent.key,
                          name: otherAgent.name,
                          version: otherAgent.version,
                        }
                      : null,
                    suiteIds: listActive
                      ? pickChanged
                        ? selIds
                        : null
                      : suite
                        ? suite.ids
                        : null,
                    scenarioVersion:
                      scenarioVer !== currentSuiteLbl ? scenarioVer : null,
                    agentKey: pickedKey,
                    addEvals: pickable.filter((e) => addEvalIds.includes(e.id)),
                    candidate: null,
                    /* No cap is applied while pre-run cost is Later (costFlags). */
                    cap: SHOW_RUN_COST ? effectiveCap : null,
                    alertAt,
                    estimate,
                    limits,
                    /* Comparing versions: each version's run holds back its
                     own blocked cases. */
                    versionSubsets: multi
                      ? Object.fromEntries(
                          versionReports
                            .filter((x) => x.report?.scope?.blocked?.length)
                            .map(({ v, report }) => [
                              v,
                              {
                                ids: report.scope.eligibleIds,
                                requested: report.scope.requested,
                                blocked: report.scope.blocked.map((b) => b.id),
                              },
                            ]),
                        )
                      : null,
                    compatSubset:
                      !multi && blockedN > 0
                        ? {
                            ids: compat.scope.eligibleIds,
                            requested: compat.scope.requested,
                            blocked: compat.scope.blocked.map((b) => b.id),
                          }
                        : null,
                  });
                }}
              >
                {submit === "sending"
                  ? "Starting…"
                  : submit === "rejected"
                    ? "Try again"
                    : /* Name what runs (handover): held cases are never sent. */
                      `Run ${runCount} ${runCount === 1 ? "scenario" : "scenarios"}`}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

RunPlanDialog.propTypes = {
  initialScenarioIds: PropTypes.array,
  scenarioStatus: PropTypes.object,
  baseline: PropTypes.object,
  initialEnvVersion: PropTypes.string,
  titleKind: PropTypes.string,
  patch: PropTypes.func,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onStart: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  scenarioCount: PropTypes.number,
  /* Run setup: compatible agents { key, name, version, current, readiness },
     saved suites { id, name, ids }, and the saved environment version label. */
  agentChoices: PropTypes.array,
  initialAgentKey: PropTypes.string,
  suites: PropTypes.array,
  envLabel: PropTypes.string,
  scopeNote: PropTypes.string,
  initialAttempts: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  versionOptions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string,
      name: PropTypes.string,
      tag: PropTypes.string,
      more: PropTypes.bool,
    }),
  ),
  evals: PropTypes.array,
  /* Suggested evaluations not added yet — picked here, added on start. */
  suggestedEvals: PropTypes.array,
  canAddEvals: PropTypes.bool,
  runLocation: PropTypes.string,
  parallel: PropTypes.number,
  missingRate: PropTypes.bool,
  alsoCount: PropTypes.number,
  /* The agent version a Re-run asked for. */
  initialVersion: PropTypes.string,
  /* What this run is, when it isn't a plain new run (continues, snapshot). */
  scopeLead: PropTypes.string,
  /* The other environments a run group starts: [{ env, envState, scenarios, evals }]. */
  alsoScopes: PropTypes.array,
  /* { eligible, stale, blocked, blockedWhy, excluded, excludedWhy } — scopeBreakdownOf. */
  breakdown: PropTypes.object,
  envVersionOptions: PropTypes.array,
  breakdownOf: PropTypes.func,
  /* "run 3" when the run reuses an earlier run's seed. */
  seedFrom: PropTypes.string,
  /* The compatibility report for this scope — blocked cases ask for a reviewed subset. */
  compatOf: PropTypes.func,
  canCheckCompat: PropTypes.func,
  fixKitOf: PropTypes.func,
  onFixInBuilder: PropTypes.func,
  onCheckCompat: PropTypes.func,
  checkingCompat: PropTypes.bool,
  onRepairCompat: PropTypes.func,
};
