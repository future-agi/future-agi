import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useSimStore } from "../store";
import { candidateSourceOf, takesSnapshot } from "../_mock/candidateSource";
import CandidateSourcePicker from "../run/CandidateSourcePicker";
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
import { membersOf } from "../_mock/improvementPolicy";
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
import { COMPAT_STATUS } from "../_mock/compatibility";
import { StatusDot } from "../components/primitives";

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
  seedFrom = null,
  compat = null,
  onRepairCompat,
}) {
  const voice = ["voice", "multi"].includes(env?.surface);
  const [attempts, setAttempts] = useState(initialAttempts || 3);
  const [versions, setVersions] = useState(
    versionOptions.slice(0, 1).map((o) => o.label),
  );
  const [moreVersions, setMoreVersions] = useState(false);
  /* A code agent can be tested from a commit or an uploaded archive instead
     of a version — one candidate, so only with one version picked. */
  const [candidate, setCandidate] = useState(null);
  const snapshotPick = takesSnapshot(envState?.agent) && versions.length === 1;
  const [cap, setCap] = useState(null);
  const [capTouched, setCapTouched] = useState(false);
  const [alertAt, setAlertAt] = useState(WORKSPACE_POLICY.alertAt);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const [limits, setLimits] = useState(() => runLimitsOf(env, envState));
  const [showLimits, setShowLimits] = useState(false);
  /* Blocked cases: running only the eligible ones is an explicit choice. */
  const [subsetOk, setSubsetOk] = useState(false);
  const blockedN = compat?.scope?.blocked?.length || 0;

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
    setCandidate(null);
    setCapTouched(false);
    setAlertAt(WORKSPACE_POLICY.alertAt);
    setShowBreakdown(false);
    setLimits(runLimitsOf(env, envState));
    setShowLimits(false);
    setSubsetOk(false);
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
          scenarios: scenarioCount,
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
      scenarioCount,
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

  const unit = voice ? "call" : "trial";
  const external = estimate.external[0] || null;
  const fieldSx = {
    "& .MuiInputBase-input": { py: 0.5, fontVariantNumeric: "tabular-nums" },
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{ sx: { maxWidth: 560 } }}
    >
      <DialogTitle sx={{ pb: 0.5 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>
          Run plan
        </Typography>
        <Typography noWrap sx={{ typography: "s2", color: "text.tertiary" }}>
          {[
            env?.name || "Environment",
            voice ? "Voice" : "Text",
            `environment ${currentEnvVersion(env, envState)?.label || "v1"}`,
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
        {/* From a ready world: the exact release and pack this run uses. */}
        {(() => {
          const entry = catalogueOfEnv(env);
          if (!entry) return null;
          const pack =
            {
              starter: "Starter pack",
              full: "Full world",
            }[env?.pack] || (env?.pack ? "A named pack" : null);
          return (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {[
                `${entry.id} · ${entry.task}`,
                `release ${envReleaseOf(env, entry)}`,
                pack,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          );
        })()}
      </DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {scopeLead && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {scopeLead}
            </Typography>
          )}
          {/* ── what runs: one sentence, its numbers editable in place ── */}
          <Stack
            direction="row"
            alignItems="center"
            flexWrap="wrap"
            gap={1}
            sx={{ typography: "s1", color: "text.secondary" }}
          >
            <Tooltip arrow title={scopeNote || ""}>
              <Box component="span" sx={{ color: "text.primary" }}>
                {`${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`}
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
                "aria-label": "Attempts per scenario",
                style: { textAlign: "right" },
              }}
              sx={{ width: 64, ...fieldSx }}
            />
            <span>{`attempt${Number(attempts) === 1 ? "" : "s"} on agent`}</span>
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
                  (o) => moreVersions || !o.more || versions.includes(o.label),
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

          {/* ── what's in and out, counted before any ID is resolved ── */}
          {breakdown && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: -1,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {[
                `${breakdown.eligible} eligible`,
                breakdown.stale
                  ? `${breakdown.stale} stale — they run but count toward no decision`
                  : null,
                breakdown.blocked
                  ? `${breakdown.blocked} blocked${breakdown.blockedWhy ? ` (${breakdown.blockedWhy.replace(/\.$/, "")})` : ""}`
                  : null,
                breakdown.excluded
                  ? `${breakdown.excluded} excluded (${breakdown.excludedWhy})`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          )}

          {/* ── compatibility: what this agent can't run here, before spending ── */}
          {compat && blockedN > 0 && (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "status.warning.border",
                borderRadius: "8px",
                px: 1.5,
                py: 1,
              }}
            >
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <StatusDot
                  status={COMPAT_STATUS.blocked.dot}
                  size={6}
                  live={false}
                />
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  {`${blockedN} of ${compat.scope.requested} requested cases can't run with this agent`}
                </Typography>
                {onRepairCompat && (
                  <Button
                    size="small"
                    onClick={onRepairCompat}
                    sx={{ color: "text.primary", flexShrink: 0 }}
                  >
                    Repair
                  </Button>
                )}
              </Stack>
              {compat.rows
                .filter((r) => r.blocks)
                .map((r) => (
                  <Typography
                    key={r.id}
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                  >
                    {`${r.label} · ${r.detail}`}
                  </Typography>
                ))}
              <Stack
                direction="row"
                alignItems="flex-start"
                spacing={0.75}
                onClick={() => setSubsetOk((v) => !v)}
                sx={{ mt: 0.75, cursor: "pointer" }}
              >
                <Checkbox
                  size="small"
                  checked={subsetOk}
                  tabIndex={-1}
                  sx={{ p: 0, mt: "1px" }}
                  inputProps={{ "aria-label": "Run the eligible cases only" }}
                />
                <Typography sx={{ typography: "s2" }}>
                  {`Run the ${compat.scope.eligible} eligible cases. The ${blockedN} blocked stay in the count as not run — the result reads ${compat.scope.eligible} evaluated / ${compat.scope.requested} requested.`}
                </Typography>
              </Stack>
            </Box>
          )}

          {snapshotPick && (
            <CandidateSourcePicker
              agent={envState?.agent}
              agentVersion={versions[0]}
              current={candidateSourceOf(candidate, envState?.agent)}
              onPick={setCandidate}
              offer="Or test a commit or uploaded code instead"
              sx={{ px: 0, py: 0, borderTop: "none", mt: -1 }}
            />
          )}

          {/* ── seed and limits: one line; each one on request ── */}
          <Box>
            <Stack
              direction="row"
              alignItems="baseline"
              justifyContent="space-between"
              gap={2}
            >
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Seed and limits
              </Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  minWidth: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {limitsLine(limits, {
                  voice,
                  seed: seedFrom ? `seed of ${seedFrom}` : "new seed",
                })}
              </Typography>
            </Stack>
            <Button
              size="small"
              onClick={() => setShowLimits((v) => !v)}
              aria-expanded={showLimits}
              endIcon={
                <Iconify
                  icon={
                    showLimits
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={14}
                />
              }
              sx={{ px: 0.5, ml: -0.5, mt: 0.25, color: "text.secondary" }}
            >
              {showLimits ? "Hide limits" : "Change limits"}
            </Button>
            <Collapse in={showLimits}>
              <Box sx={{ mt: 0.5 }}>
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

          {/* ── what it costs: one number; the breakdown on request ── */}
          <Box>
            <Stack
              direction="row"
              alignItems="baseline"
              justifyContent="space-between"
              gap={2}
            >
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
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
              sx={{ px: 0.5, ml: -0.5, mt: 0.25, color: "text.secondary" }}
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
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  Stop at
                </Typography>
                <Tooltip
                  arrow
                  title={`At the limit no new ${unit}s start; ${unit}s already running finish — up to ~${overrun.toLocaleString()} credits more. You're warned at ${Math.round(alertAt * 100)}%.${versions.length > 1 ? ` Shared by the ${versions.length} versions — each run holds ${Math.floor((effectiveCap || 0) / versions.length).toLocaleString()}.` : ""}${alsoCount ? ` Shared by the ${alsoCount + 1} environments — each run holds ${Math.floor((effectiveCap || 0) / (alsoCount + 1)).toLocaleString()}.` : ""}`}
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
                  setCap(e.target.value === "" ? null : Number(e.target.value));
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
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
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

          {check.holds.length > 0 && (
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
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {h.line}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Tooltip
          arrow
          title={
            check.canStart
              ? `${estimate.trials} ${unit}s · stops at ${fmtCredits(effectiveCap)}`
              : check.holds[0]?.title || ""
          }
        >
          <span>
            <Button
              size="small"
              variant="contained"
              disabled={!check.canStart || (blockedN > 0 && !subsetOk)}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              onClick={() =>
                onStart({
                  attempts: Number(attempts),
                  versions,
                  candidate: snapshotPick ? candidate : null,
                  cap: effectiveCap,
                  alertAt,
                  estimate,
                  limits,
                  compatSubset:
                    blockedN > 0
                      ? {
                          ids: compat.scope.eligibleIds,
                          requested: compat.scope.requested,
                          blocked: compat.scope.blocked.map((b) => b.id),
                        }
                      : null,
                })
              }
            >
              Start run
            </Button>
          </span>
        </Tooltip>
      </DialogActions>
    </Dialog>
  );
}

RunPlanDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onStart: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  scenarioCount: PropTypes.number,
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
  /* "run 3" when the run reuses an earlier run's seed. */
  seedFrom: PropTypes.string,
  /* The compatibility report for this scope — blocked cases ask for a reviewed subset. */
  compat: PropTypes.object,
  onRepairCompat: PropTypes.func,
};
