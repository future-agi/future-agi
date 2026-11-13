import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
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
  limitsLine,
  minutesLabel,
  runLimitsOf,
} from "../_mock/runLimits";
import { currentEnvVersion } from "../_mock/versions";
import ReadyToRun from "../compat/ReadyToRun";
import { SHOW_RUN_COST } from "../_mock/costFlags";
import { evalSlotsLeft } from "../_mock/evals";
import { platformPreviewOf } from "../_mock/platformPreview";
import { assistantsFor } from "../_mock/connectAgent";
import { delegationEvidenceOf } from "../_mock/handoffs";
import { membersOf } from "../_mock/improvementPolicy";
import { useNavigate, useSearchParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import SideDrawer from "../components/SideDrawer";
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
const MAX_VERSIONS = 3;
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
  scopeLead,
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
    versionOptions.slice(0, 1).map((o) => o.label),
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
  const otherAgent =
    agentChoices.find((a) => a.key === agentKey && !a.current) || null;
  const suite = suites.find((x) => x.id === suiteId) || null;
  /* The environment version this run uses: the current one unless picked. */
  const currentEnvLabel =
    envVersionOptions.find((v) => v.current)?.label ||
    envLabel ||
    currentEnvVersion(env, envState)?.label ||
    "v1";
  const [envVer, setEnvVer] = useState(null);
  const pickedEnv = envVer && envVer !== currentEnvLabel ? envVer : null;
  const bd = pickedEnv && breakdownOf ? breakdownOf(pickedEnv) : breakdown;
  /* The version's scenarios in all — the count line splits them into
     eligible, blocked and excluded. */
  const scopeCount = bd
    ? bd.eligible + bd.blocked + bd.excluded
    : scenarioCount;
  const shownCount = suite
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
          ids: suite ? suite.ids : null,
          envVersion: pickedEnv,
        })
      : null;
  /* Several versions compared: one run each, so one Ready to run each —
     the switch above it says which one is shown. */
  const multi = !otherAgent && versions.length > 1;
  const [viewVersion, setViewVersion] = useState(null);
  const shownVersion =
    multi && versions.includes(viewVersion) ? viewVersion : versions[0];
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
  const [limits, setLimits] = useState(() => runLimitsOf(env, envState));
  const [showLimits, setShowLimits] = useState(false);
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
    setAddEvalIds([]);
    setCapTouched(false);
    setAlertAt(WORKSPACE_POLICY.alertAt);
    setShowBreakdown(false);
    setLimits(runLimitsOf(env, envState));
    setShowLimits(false);
    setEnvVer(null);
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
          line: "Runs start when it finishes.",
        }
      : pairFailed
        ? {
            title: "Can't start until a check passes",
            line: "Fix the red row, then Re-check.",
          }
        : allHeld
          ? {
              title: "Every selected scenario waits on a fix",
              line: "Fix it, or pick other scenarios.",
            }
          : null;
  const canStart = check.canStart && !pairHold;
  /* Nothing can run (the check failed, or every selected scenario waits on a
     fix): say why once, never "0 scenarios" or "pick a scenario". */
  const noRun = pairFailed || (!multi && allHeld);
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

  return (
    /* A side drawer (13 Nov): the environment stays in view beside the plan. */
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 560 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2.5, py: 2, borderBottom: 1, borderColor: "divider" }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Run plan
            </Typography>
            {/* From a ready world, its exact release and pack are on hover. */}
            <Typography
              noWrap
              title={(() => {
                const entry = catalogueOfEnv(env);
                if (!entry) return undefined;
                const pack =
                  { starter: "Starter pack", full: "Full world" }[env?.pack] ||
                  (env?.pack ? "A named pack" : null);
                return [
                  `${entry.id} · ${entry.task}`,
                  `release ${envReleaseOf(env, entry)}`,
                  pack,
                ]
                  .filter(Boolean)
                  .join(" · ");
              })()}
              sx={{ typography: "s2", color: "text.tertiary" }}
            >
              {[
                env?.name || "Environment",
                voice ? "Voice" : "Text",
                `environment ${pickedEnv || currentEnvLabel}`,
                runLocation
                  ? runLocation === "hosted"
                    ? "hosted"
                    : "on your machine"
                  : null,
                alsoCount
                  ? `plus ${alsoCount} other environment${alsoCount === 1 ? "" : "s"}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
        </Stack>

        <Box sx={{ px: 2.5, py: 2, flex: 1, minHeight: 0, overflowY: "auto" }}>
          <Stack spacing={2}>
            {scopeLead && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {scopeLead}
              </Typography>
            )}
            {/* ── the agent first: everything below is for this agent on
              this environment (compatibility belongs to the pair) ── */}
            {
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    width: 88,
                    flexShrink: 0,
                  }}
                >
                  Agent
                </Typography>
                <Box sx={{ typography: "s2", minWidth: 0 }}>
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
                {/* The version beside the agent (12 Nov review): what runs is
                  said once, at the top. */}
                {otherAgent ? (
                  <Box component="span" sx={{ color: "text.primary" }}>
                    {otherAgent.version}
                  </Box>
                ) : (
                  <TextField
                    select
                    size="small"
                    value={versions}
                    onChange={(e) => {
                      const v = e.target.value;
                      const picked = Array.isArray(v) ? v : [v];
                      /* "More versions…" opens the older ones; it's not a pick. */
                      if (picked.includes(MORE)) {
                        setMoreVersions(true);
                        return;
                      }
                      if (picked.length >= 1 && picked.length <= MAX_VERSIONS)
                        setVersions(picked);
                    }}
                    SelectProps={{
                      multiple: true,
                      renderValue: (sel) => sel.join(", "),
                    }}
                    inputProps={{ "aria-label": "Agent versions" }}
                    sx={{ minWidth: 88, ...fieldSx }}
                  >
                    {versionOptions
                      .filter(
                        (o) =>
                          moreVersions || !o.more || versions.includes(o.label),
                      )
                      .map((o) => (
                        <MenuItem
                          key={o.label}
                          value={o.label}
                          disabled={
                            !versions.includes(o.label) &&
                            versions.length >= MAX_VERSIONS
                          }
                          sx={{ gap: 1 }}
                        >
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
            }

            {/* ── Ready to run (plan T06): this agent version on this
              environment, before anything starts — six areas, each blocker
              with its scenarios and fix ── */}
            {compat && multi && (
              <Stack direction="row" spacing={1.5} alignItems="center">
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    width: 88,
                    flexShrink: 0,
                  }}
                >
                  Version
                </Typography>
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
              </Stack>
            )}
            {compat && (
              <ReadyToRun
                report={compat}
                scenarios={envState?.scenarios || []}
                evalCount={(evals || []).length}
                onFix={(row) => onRepairCompat?.(row, pickedKey)}
                fixKit={fixKitOf ? fixKitOf(pickedKey) : null}
                onCheck={
                  onCheckCompat && canCheckCompat?.(pickedKey)
                    ? () => onCheckCompat(pickedKey)
                    : undefined
                }
                checking={checkingCompat}
              >
                {multi && allHeld && !pairFailed && (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`Every selected scenario waits on a fix (${leftOut}).`}
                  </Typography>
                )}
              </ReadyToRun>
            )}

            {/* ── run setup: agent · environment · suite, read together ── */}
            {(agentChoices.length > 1 || suites.length > 0 || envLabel) && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  px: 1.5,
                  py: 1,
                }}
              >
                {[
                  ...(participants.length
                    ? [
                        {
                          k: "Participants",
                          v: `${participants[0]?.name || "Coordinator"} + ${participants.length - 1} worker${participants.length === 2 ? "" : "s"}`,
                          note: `${participants.map((m) => m.name).join(", ")}. ${
                            participantsEvidence?.source === "instrumented"
                              ? "Hand-offs are observed in this run."
                              : "Hand-offs aren't observed; only the overall result counts."
                          }`,
                        },
                      ]
                    : []),
                  /* Environment and suite on one line: the version that runs,
                   then which of its scenarios. */
                  {
                    k: "Environment",
                    v: (
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.5}
                        useFlexGap
                        flexWrap="wrap"
                      >
                        {envVersionOptions.length > 1 ? (
                          <TextField
                            select
                            size="small"
                            value={pickedEnv || currentEnvLabel}
                            onChange={(e) => setEnvVer(e.target.value)}
                            SelectProps={{ renderValue: (v) => v }}
                            inputProps={{ "aria-label": "Environment version" }}
                            sx={{ minWidth: 88, ...fieldSx }}
                          >
                            {envVersionOptions.map((v) => (
                              <MenuItem
                                key={v.label}
                                value={v.label}
                                sx={{ gap: 1 }}
                              >
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
                          <Box component="span">{`${currentEnvLabel} ·`}</Box>
                        )}
                        {suites.length ? (
                          <TextField
                            select
                            size="small"
                            value={suiteId}
                            onChange={(e) => setSuiteId(e.target.value)}
                            inputProps={{ "aria-label": "Suite" }}
                            sx={{ minWidth: 180, ...fieldSx }}
                          >
                            <MenuItem value="all">
                              {`All · ${scopeCount}`}
                            </MenuItem>
                            {suites.map((x) => (
                              <MenuItem key={x.id} value={x.id}>
                                {`${x.name} · v${x.version || 1} · ${x.ids.length}`}
                              </MenuItem>
                            ))}
                          </TextField>
                        ) : (
                          <Box component="span">
                            {`${scopeCount} scenarios`}
                          </Box>
                        )}
                      </Stack>
                    ),
                    note: suite
                      ? "A suite is frozen when the run starts: later edits to it don't change this run."
                      : null,
                  },
                  /* What scores this run: the added evaluations, then the
                   suggested ones — picked here, never added by default. */
                  {
                    k: "Evaluations",
                    v: (
                      <Stack spacing={0.5} sx={{ py: 0.25, minWidth: 0 }}>
                        <Stack direction="row" alignItems="center">
                          <Box component="span">
                            {(evals || []).length
                              ? (evals || []).map((e) => e.name).join(", ")
                              : "None added"}
                          </Box>
                          <Tooltip
                            arrow
                            title={
                              pickable.length
                                ? "Suggested evaluations score a run only if you pick them. Picked ones are added to this environment's evaluations when the run starts. The scenarios' own checks always run."
                                : "The scenarios' own checks always run. Add evaluations on the Evaluations step."
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
                        {pickable.length > 0 && (
                          <Stack
                            direction="row"
                            alignItems="center"
                            gap={0.75}
                            flexWrap="wrap"
                          >
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary" }}
                            >
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
                      </Stack>
                    ),
                    note: null,
                  },
                ].map((row) => (
                  <Stack
                    key={row.k}
                    direction="row"
                    spacing={1.5}
                    alignItems="flex-start"
                    sx={{ py: 0.5 }}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.secondary",
                        width: 88,
                        flexShrink: 0,
                        pt: "5px",
                      }}
                    >
                      {row.k}
                    </Typography>
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Box
                        sx={{
                          typography: "s2",
                          color: "text.primary",
                          minHeight: 28,
                          display: "flex",
                          alignItems: "center",
                        }}
                      >
                        {row.v}
                        {/* One line per row; what it means is on hover. */}
                        {row.note && (
                          <Tooltip arrow title={row.note}>
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
                        )}
                      </Box>
                    </Box>
                  </Stack>
                ))}
              </Box>
            )}

            {/* ── what runs: one sentence, its numbers editable in place ── */}
            {!noRun && (
              <Stack
                direction="row"
                alignItems="center"
                flexWrap="wrap"
                gap={1}
                sx={{ typography: "s1", color: "text.secondary" }}
              >
                <Tooltip arrow title={scopeNote || ""}>
                  <Box component="span" sx={{ color: "text.primary" }}>
                    {`${runCount} scenario${runCount === 1 ? "" : "s"}`}
                  </Box>
                </Tooltip>
                <span>×</span>
                <TextField
                  size="small"
                  type="number"
                  value={attempts}
                  onChange={(e) => setAttempts(e.target.value)}
                  inputProps={{
                    min: 1,
                    max: 20,
                    "aria-label": "Repeats per scenario",
                    style: { textAlign: "right" },
                  }}
                  sx={{ width: 64, ...fieldSx }}
                />
                <span>{`repeat${Number(attempts) === 1 ? "" : "s"}`}</span>
                <Box
                  component="span"
                  sx={{
                    color: "text.primary",
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`= ${estimate.trials} ${unit}${estimate.trials === 1 ? "" : "s"}`}
                </Box>
              </Stack>
            )}

            {/* ── what's in and out, in one line that adds up (12 Nov
              review): will run of the total, then each group left out in
              plain words; why each is left out on hover. Stale cases run,
              so they're a note on the run count, not a group of their own. */}
            {!pairFailed &&
              !allHeld &&
              (compat || bd) &&
              (() => {
                const fromSuite = suite ? null : bd;
                const out = [
                  blockedN > 0 && {
                    n: blockedN,
                    text: "blocked for this agent",
                    why: `Held back and recorded as not run — ${leftOut}.`,
                  },
                  fromSuite?.blocked > 0 && {
                    n: fromSuite.blocked,
                    text: `blocked on ${pickedEnv || currentEnvLabel}`,
                    why: fromSuite.blockedWhy || "",
                  },
                  fromSuite?.excluded > 0 && {
                    n: fromSuite.excluded,
                    text: "excluded",
                    why: fromSuite.excludedWhy || "",
                  },
                ].filter(Boolean);
                const total = runCount + out.reduce((t, o) => t + o.n, 0);
                const stale = fromSuite?.stale || 0;
                const line = [
                  out.length
                    ? `${runCount} of ${total} eligible`
                    : `All ${runCount} eligible`,
                  ...out.map((o) => `${o.n} ${o.text}`),
                  stale ? `${stale} stale` : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const tip = [
                  ...out.map(
                    (o) => `${o.n} ${o.text}${o.why ? ` — ${o.why}` : ""}`,
                  ),
                  stale
                    ? `${stale} stale — they run but count toward no decision.`
                    : null,
                ]
                  .filter(Boolean)
                  .join("\n");
                return (
                  <Tooltip
                    arrow
                    placement="top-start"
                    title={
                      tip ? (
                        <Box sx={{ whiteSpace: "pre-line" }}>{tip}</Box>
                      ) : (
                        ""
                      )
                    }
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mt: -1,
                        fontVariantNumeric: "tabular-nums",
                        alignSelf: "flex-start",
                      }}
                    >
                      {line}
                    </Typography>
                  </Tooltip>
                );
              })()}

            {/* ── a platform agent: what runs for real during this run, named ── */}
            {(() => {
              const v = envState?.agent?.values || {};
              if (!v.provider || !v.agentId) return null;
              const a = assistantsFor(v.provider).find(
                (x) => x.id === v.agentId,
              ) || { id: v.agentId, name: envState?.agent?.name };
              const live = (
                platformPreviewOf(v.provider, a, { voice })?.tools || []
              ).filter((t) => t.handling === "live");
              if (!live.length) return null;
              return (
                <Tooltip
                  arrow
                  title={`${live.map((t) => `${t.name}: ${t.line}`).join(" · ")}. Your own tools are redirected to the test world.`}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      mt: -1,
                      alignSelf: "flex-start",
                    }}
                  >
                    {`Runs for real: ${live.map((t) => t.name).join(", ")}`}
                  </Typography>
                </Tooltip>
              );
            })()}

            {/* ── seed and limits: one line; each one on request ── */}
            <Box>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {seedable ? "Seed and limits" : "Limits"}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {limitsLine(limits, {
                    voice,
                    /* A seed only where the agent can take one (runs.md). */
                    seed: !seedable
                      ? null
                      : seedFrom
                        ? `seed of ${seedFrom}`
                        : "new seed",
                  })}
                </Typography>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setShowLimits((v) => !v)}
                  aria-expanded={showLimits}
                  startIcon={
                    showLimits ? null : (
                      <Iconify icon="solar:pen-linear" width={14} />
                    )
                  }
                  sx={{ flexShrink: 0 }}
                >
                  {showLimits ? "Done" : "Edit"}
                </Button>
              </Stack>
              <Collapse in={showLimits}>
                <Box sx={{ mt: 0.5 }}>
                  {seedable && (
                    <Row
                      label="Seed"
                      detail={
                        seedFrom
                          ? `The same draws as ${seedFrom}, so its scenarios meet the same callers and data`
                          : "Fixed when the run starts and saved with it — a re-run can reuse it"
                      }
                      value={seedFrom ? `Same as ${seedFrom}` : "New"}
                      muted
                    />
                  )}
                  <Row
                    label={voice ? "Longest call" : "Per-trial timeout"}
                    detail={`A ${unit} still going then stops and fails as timed out`}
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
                    detail={`No new ${unit}s start after it; evidence is kept`}
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
                    detail="The environment's limit — change it in Settings"
                    value={`${limits.turnLimit} turns`}
                    muted
                  />
                  {limits.stepLimit ? (
                    <Row
                      label="Tool-step limit"
                      detail="Tool calls one trial may make before it stops"
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
                      detail={`${envState?.agent?.name || "This agent"} runs its tools on its own platform, so steps can't be stopped from here`}
                      value="Not enforced"
                      muted
                    />
                  )}
                </Box>
              </Collapse>
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
                          <InputAdornment position="end">
                            credits
                          </InputAdornment>
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

            {(noRun ? [pairHold] : check.holds).length > 0 && (
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
                {(noRun ? [{ id: "pair", ...pairHold }] : check.holds).map(
                  (h) => (
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
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {h.line}
                      </Typography>
                    </Box>
                  ),
                )}
              </Box>
            )}
          </Stack>
        </Box>

        {(submit === "rejected" || submit === "timeout") && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              mx: 2.5,
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
                ? "No run created — the world service is unavailable. Nothing was charged; your choices are kept."
                : "We couldn't confirm the run started. It may exist, so Start stays off — find it first."}
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
          sx={{ px: 2.5, py: 1.5, borderTop: 1, borderColor: "divider" }}
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
              canStart
                ? SHOW_RUN_COST
                  ? `${estimate.trials} ${unit}s · stops at ${fmtCredits(effectiveCap)}`
                  : `${estimate.trials} ${unit}s`
                : pairHold?.title || check.holds[0]?.title || ""
            }
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={
                  !canStart || submit === "sending" || submit === "timeout"
                }
                startIcon={<Iconify icon="solar:play-bold" width={14} />}
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
                    suiteIds: suite ? suite.ids : null,
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
                    : "Start run"}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

RunPlanDialog.propTypes = {
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
  onCheckCompat: PropTypes.func,
  checkingCompat: PropTypes.bool,
  onRepairCompat: PropTypes.func,
};
