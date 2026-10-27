import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  CALL_MINUTES_RANGE,
  RATE_PLAN,
  estimateRunPlan,
  fmtCredits,
  judgeCreditsOf,
  judgesOf,
  platformUsdOf,
  rangeLabel,
  usageOfTask,
  usdRange,
} from "../_mock/pricing";
import { scenarioTitle } from "../_mock/scenarios";
import { resolveEval } from "../_mock/evals";
import { freeTierByRun, runCreditsOf } from "../_mock/usageLedger";
import { BILLING_POLICY } from "../_mock/rates";
import { useOptionalSimStore } from "../store";
import { StatusChip } from "../components/primitives";

/*
  The run's Cost tab: what it actually used against what the run plan
  estimated, said once at the top; then quantity × rate per action, with each
  action's share; then each scenario's calls, most expensive first, every
  line tracing to the scenario it came from. What wasn't charged (never
  started, nothing measured) is listed with its reason; what's billed
  outside Future AGI stays apart and pending until the provider reports it.
*/

const PAGE = 25;
const fmt = (n) => Math.round(n).toLocaleString();
const qtyLabel = (qty, unit) =>
  unit === "min"
    ? `${qty.toFixed(1)} min`
    : `${(qty / 1000).toFixed(1)}K tokens`;

/* A scenario's own name — its variant, as the Scenarios tab shows it. */
const humanize = (s) =>
  String(s || "")
    .replace(/[-_]+/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
const variantOf = (id) => {
  const parts = String(id || "")
    .split("-")
    .filter(Boolean);
  let lastNum = -1;
  parts.forEach((p, i) => {
    if (/^\d+$/.test(p)) lastNum = i;
  });
  const tail = lastNum >= 0 ? parts.slice(lastNum + 1) : [];
  if (tail.length) return humanize(tail.join(" "));
  if (lastNum > 0) return humanize(parts[lastNum - 1]);
  return humanize(parts.slice(-1)[0] || "Run");
};

function SectionTitle({ title, note }) {
  return (
    <Stack direction="row" alignItems="baseline" gap={1} sx={{ mb: 0.75 }}>
      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
        {title}
      </Typography>
      {note && (
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {note}
        </Typography>
      )}
    </Stack>
  );
}
SectionTitle.propTypes = { title: PropTypes.string, note: PropTypes.string };

/* The system table (the theme styles it to spec): numbers and values at
   12px, right-aligned with tabular figures; a row title at 13/500. */
const num = {
  typography: "s3",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const val = { typography: "s3", whiteSpace: "nowrap" };
const title = { typography: "s2", fontWeight: 500, color: "text.primary" };
/* Rows are 36px, 44 for two lines; header labels never wrap. */
const twoLine = { py: 0 };
const tableSx = {
  "& thead th": { whiteSpace: "nowrap" },
  "& tbody tr": { height: 36 },
  "& tbody td": { py: 0 },
};

/* A thin share bar and its percentage. */
function Share({ value }) {
  const pct = Math.max(0, Math.min(1, value || 0));
  return (
    <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
      <Box
        sx={{
          flex: 1,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.selected",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            width: `${pct * 100}%`,
            height: "100%",
            bgcolor: "text.secondary",
            borderRadius: 3,
          }}
        />
      </Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.secondary",
          width: 32,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {`${Math.round(pct * 100)}%`}
      </Typography>
    </Stack>
  );
}
Share.propTypes = { value: PropTypes.number };

/* Where the actual landed against the estimated range. */
function RangeBar({ low, high, actual }) {
  const max = Math.max(high, actual) * 1.08 || 1;
  const at = (n) => `${(n / max) * 100}%`;
  return (
    <Box sx={{ position: "relative", height: 24, maxWidth: 420, mt: 1 }}>
      <Box
        sx={{
          position: "absolute",
          top: 7,
          left: 0,
          right: 0,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.selected",
        }}
      />
      <Tooltip arrow title={`Estimated ${fmt(low)}–${fmt(high)} credits`}>
        <Box
          sx={{
            position: "absolute",
            top: 7,
            left: at(low),
            width: `calc(${at(high)} - ${at(low)})`,
            height: 6,
            borderRadius: 3,
            bgcolor: "text.disabled",
          }}
        />
      </Tooltip>
      <Tooltip arrow title={`Actual ${fmt(actual)} credits`}>
        <Box
          sx={{
            position: "absolute",
            top: 3,
            left: `calc(${at(actual)} - 1px)`,
            width: 2,
            height: 14,
            borderRadius: 1,
            bgcolor: "text.primary",
          }}
        />
      </Tooltip>
    </Box>
  );
}
RangeBar.propTypes = {
  low: PropTypes.number,
  high: PropTypes.number,
  actual: PropTypes.number,
};

export default function CostTab({ run, env, envState, tasks, onOpenTask }) {
  /* Known adjustments: this run's share of the plan's monthly free tier,
     used first by the runs that finished first. */
  const simStore = useOptionalSimStore();
  const free = useMemo(
    () => freeTierByRun(simStore?.state)[run?.id] || null,
    [simStore?.state, run?.id],
  );
  const [showAll, setShowAll] = useState(false);
  const voice = ["voice", "multi"].includes(env?.surface);
  const budget = run?.budget || null;
  const repeats = run?.repeats || 1;

  /* The graders the run was scored by, resolved — billing reads their type. */
  const evals = useMemo(
    () =>
      (run?.evals || envState?.evals || []).map(resolveEval).filter(Boolean),
    [run, envState],
  );
  const rows = useMemo(() => {
    const judges = judgesOf(evals);
    const judgeCredits = judgeCreditsOf(evals);
    return tasks
      .map((t) => ({
        task: t,
        ...usageOfTask(t, { voice, judgeCredits, judges, repeats }),
      }))
      .sort((a, b) => b.credits - a.credits);
  }, [tasks, evals, voice, repeats]);

  const charged = rows.filter((r) => r.charged);
  const waived = rows.filter((r) => !r.charged);
  const callsCharged = charged.reduce((a, r) => a + r.samples, 0);
  const qty = charged.reduce((a, r) => a + r.qty, 0);
  const sim = charged.reduce((a, r) => a + r.simCredits, 0);
  const judgeCalls = charged.reduce((a, r) => a + r.judgeCalls, 0);
  const evalCredits = charged.reduce((a, r) => a + r.evalCredits, 0);
  const actual = sim + evalCredits;
  /* What the run is billed: its usage less its share of the free tier. */
  /* Debug failures, charged once when it was first opened. */
  const record = (envState?.runs || []).find((r) => r.id === run?.id);
  const analysis = record?.analysis || null;
  const used = actual + (analysis?.credits || 0);
  const billed = Math.max(0, used - (free?.credits || 0));
  const notStarted = budget?.notStarted?.length || 0;
  /* Retried calls that were charged: their failed first attempt is refunded. */
  const retriedRows = rows.filter((r) => r.task.retried && r.charged);

  /* Why the rest never started: our limit, the month's credits, a stop
     asked for, or the voice platform's own account limit. */
  const stopWhy =
    {
      stopped: "stopped early",
      credits: "low on credits",
      provider: "voice platform's own limit",
    }[budget?.stoppedBy] || "limit reached";
  const rate = voice
    ? RATE_PLAN.rates.voice_sim_minutes
    : RATE_PLAN.rates.text_sim_tokens;
  const external = voice ? platformUsdOf(envState, qty) : null;
  const callWord = voice ? "call" : "conversation";
  const plural = (n, w) => `${fmt(n)} ${w}${n === 1 ? "" : "s"}`;

  /* The estimate the run plan saved; for a run without one, its scope priced
     at today's rates, and said so. */
  const savedEst = budget?.estimate || null;
  const est =
    savedEst ||
    (() => {
      const e = estimateRunPlan({
        env,
        envState,
        scenarios: run?.scenarioIds?.length || tasks.length,
        attempts: repeats,
        versions: [run?.agentVersion].filter(Boolean),
        evals,
      });
      return e.platform.unknown
        ? null
        : { low: e.platform.low, high: e.platform.high, trials: e.trials };
    })();

  const against = !est
    ? "No estimate to compare with"
    : actual < est.low
      ? `Under the ≈ ${rangeLabel(est.low, est.high)} estimate`
      : actual > est.high
        ? `Over the ≈ ${rangeLabel(est.low, est.high)} estimate`
        : `Within the ≈ ${rangeLabel(est.low, est.high)} estimate`;

  /* Why the actual isn't the estimate — said in terms of what happened. */
  const reasons = [
    notStarted
      ? `${plural(notStarted, callWord)} never started (${stopWhy})`
      : null,
    voice && callsCharged
      ? `${(qty / callsCharged).toFixed(1)} min a call (${CALL_MINUTES_RANGE[0]}–${CALL_MINUTES_RANGE[1]} estimated)`
      : null,
    waived.length
      ? `${plural(waived.length, "scenario")} not measured, not charged`
      : null,
    retriedRows.length
      ? `${plural(retriedRows.length, "failed attempt")} on our side refunded`
      : null,
  ].filter(Boolean);

  const quiet = [
    budget?.cap
      ? `Run limit ${fmtCredits(budget.cap)}${budget.groupCap ? ` (its share of the group's ${fmtCredits(budget.groupCap)})` : ""}${budget.stoppedBy === "limit" ? " · reached" : ""}`
      : "No run limit",
    est && !savedEst ? "estimated at today's rates" : null,
  ].filter(Boolean);

  /* Everything that isn't in the credits total, each said for what it is:
     the customer's own accounts (pending until the provider reports), where
     the agent ran, and work that was never charged. */
  const location = budget?.runLocation ?? envState?.agent?.values?.runLocation;
  const outside = [
    external
      ? {
          label: `${external.label} minutes`,
          status: "Billed to your account · pending until it reports",
          amount: usdRange(external.low, external.high),
        }
      : (() => {
          /* What the agent's own traces reported — the same figure as the
             trace header and Analytics; unreported calls stay unknown. */
          const reported = tasks.filter((t) => t.cost != null);
          const usd = reported.reduce((a, t) => a + t.cost, 0);
          const missing = tasks.length - reported.length;
          return {
            label: "Your agent's model",
            status: reported.length
              ? `Billed to your own key · from its traces${missing ? ` · ${missing} not reported` : ""}`
              : "Billed to your own key · not reported",
            amount: reported.length
              ? `≈ $${usd.toFixed(2)}${missing ? " + unknown" : ""}`
              : "unknown",
          };
        })(),
    location === "hosted"
      ? {
          label: "Hosted sandbox",
          status: "Measured, not billed in beta",
          amount: `~${fmt(Math.ceil(callsCharged * 1.5))} min`,
        }
      : {
          label: "Your agent",
          status: location
            ? "Ran on your machine · no compute charged"
            : "Ran where you host it · no compute charged",
          amount: "0",
        },
    waived.length
      ? {
          label: "Not measured",
          status: `${plural(waived.length, "scenario")} · waived, not charged`,
          amount: "0",
        }
      : null,
    notStarted
      ? {
          label: "Didn't start",
          status: `${plural(notStarted, callWord)} · ${stopWhy} · not charged`,
          amount: "0",
        }
      : null,
  ].filter(Boolean);

  /* The run's billing events, in order — the limit held at the start, what
     was used, refunded, waived and never started, the free tier, any
     analysis, the limit released, and runs that continued it. */
  const when = (iso) =>
    iso
      ? new Date(iso).toLocaleString(undefined, {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";
  const refund = retriedRows.reduce(
    (a, r) => a + r.credits / Math.max(1, r.samples),
    0,
  );
  const continued = (envState?.runs || []).filter(
    (r) => r.continues === run?.id,
  );
  const continuedCredits = runCreditsOf(env, envState);
  const continuesRun = record?.continues
    ? (envState?.runs || []).find((r) => r.id === record.continues)
    : null;
  const history = [
    continuesRun && {
      when: when(record?.startedAt),
      event: `Continues run ${continuesRun.ordinal ?? ""}`.trim(),
      detail: "only the scenarios its limit stopped from starting",
      credits: "—",
      quiet: true,
    },
    budget?.cap && {
      when: when(record?.startedAt),
      event: "Limit held",
      detail: "reserved, not charged",
      credits: fmt(budget.cap),
      quiet: true,
    },
    {
      when: when(record?.finishedAt),
      event: `Calls used`,
      detail: plural(callsCharged, callWord),
      credits: `+${fmt(actual + refund)}`,
    },
    refund > 0 && {
      when: when(record?.finishedAt),
      event: "Refunded",
      detail: `${plural(retriedRows.length, "failed attempt")} on our side, run again`,
      credits: `−${fmt(refund)}`,
    },
    waived.length > 0 && {
      when: when(record?.finishedAt),
      event: "Waived",
      detail: `${plural(waived.length, "scenario")} not measured`,
      credits: "0",
      quiet: true,
    },
    notStarted > 0 && {
      when: when(record?.finishedAt),
      event: "Not started",
      detail: `${plural(notStarted, callWord)}`,
      credits: "0",
      quiet: true,
    },
    free && {
      when: when(record?.finishedAt),
      event: "Free tier",
      detail: "your plan's monthly allowance",
      credits: `−${fmt(free.credits)}`,
    },
    budget?.cap && {
      when: when(record?.finishedAt),
      event: "Limit released",
      detail: "what the run didn't use",
      credits: fmt(Math.max(0, budget.cap - actual)),
      quiet: true,
    },
    analysis && {
      when: when(analysis.at),
      event: "Failure analysis",
      detail: `${plural(analysis.scenarios, "failing scenario")} · charged once`,
      credits: `+${fmt(analysis.credits)}`,
    },
    {
      when: "",
      event: "Charged",
      credits: fmt(billed),
      strong: true,
    },
    ...continued.map((c) => ({
      when: when(c.startedAt),
      event: `Continued in run ${c.ordinal ?? ""}`.trim(),
      detail: "the scenarios that didn't start · billed on its own Cost tab",
      credits:
        continuedCredits[c.id] != null
          ? fmt(continuedCredits[c.id])
          : "running",
      quiet: true,
    })),
  ].filter(Boolean);

  const shown = showAll ? rows : rows.slice(0, PAGE);
  const maxCredits = rows[0]?.credits || 1;

  return (
    <Stack spacing={3} sx={{ py: 2 }}>
      {/* ── actual against the estimate ── */}
      <Box>
        <Stack direction="row" alignItems="baseline" gap={1.5} flexWrap="wrap">
          <Typography
            sx={{
              typography: "l3",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmtCredits(billed)}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${against} · ${plural(callsCharged, `${callWord}`)} charged${free ? ` · ${fmt(used)} used, ${fmt(free.credits)} from your free tier` : ""}`}
          </Typography>
        </Stack>
        {est && <RangeBar low={est.low} high={est.high} actual={actual} />}
        {/* Why it differs, and what's outside the total — one line. */}
        <Typography
          noWrap
          title={[...reasons, ...quiet].join(" · ")}
          sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
        >
          {[...reasons, ...quiet]
            .join(" · ")
            .replace(/^\w/, (c) => c.toUpperCase())}
        </Typography>
      </Box>

      {/* ── by action: quantity × rate, and each action's share ── */}
      <Box>
        <SectionTitle
          title="By action"
          note={
            budget?.rates
              ? `${budget.rates} rates · recorded when it started`
              : `${RATE_PLAN.name} · ${RATE_PLAN.version} rates`
          }
        />
        <Table sx={tableSx}>
          <TableHead>
            <TableRow>
              <TableCell>Action</TableCell>
              <TableCell sx={{ width: "40%" }}>Share</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Quantity</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Rate</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Credits</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {[
              {
                label: "Simulated caller and actors",
                q: qtyLabel(qty, voice ? "min" : "tokens"),
                r: voice
                  ? `${rate.credits} / min`
                  : `${rate.credits} / 1M tokens`,
                c: sim,
              },
              {
                label: "Evaluations",
                q: `${fmt(judgeCalls)} judge calls`,
                r: judgeCalls ? "2 / call" : "—",
                c: evalCredits,
              },
              /* Debug failures, once it was opened — its own action. */
              ...(analysis
                ? [
                    {
                      label: "Failure analysis",
                      q: `${fmt(analysis.scenarios)} failing scenarios`,
                      r: `${RATE_PLAN.rates.analysis_calls.credits} / scenario`,
                      c: analysis.credits,
                    },
                  ]
                : []),
            ].map((a) => (
              <TableRow key={a.label}>
                <TableCell sx={title}>{a.label}</TableCell>
                <TableCell>
                  <Share value={used ? a.c / used : 0} />
                </TableCell>
                <TableCell sx={num}>{a.q}</TableCell>
                <TableCell sx={num}>{a.r}</TableCell>
                <TableCell sx={num}>{fmt(a.c)}</TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell sx={{ ...val, typography: "s2" }}>
                Seeded world and tools
              </TableCell>
              <TableCell />
              <TableCell sx={{ ...num, color: "text.tertiary" }}>—</TableCell>
              <TableCell sx={{ ...num, color: "text.tertiary" }}>
                included
              </TableCell>
              <TableCell sx={{ ...num, color: "text.tertiary" }}>0</TableCell>
            </TableRow>
            {/* Known adjustments — the plan's free tier is the one there is. */}
            <TableRow>
              <TableCell sx={{ ...val, typography: "s2" }}>Free tier</TableCell>
              <TableCell sx={{ ...val, color: "text.tertiary" }}>
                {free
                  ? "Your plan's monthly allowance, used first"
                  : "Used up by earlier runs this month"}
              </TableCell>
              <TableCell
                sx={{ ...num, color: free ? undefined : "text.tertiary" }}
              >
                {free
                  ? [
                      free.units
                        ? `${qtyLabel(free.units, voice ? "min" : "tokens")}`
                        : null,
                      free.evalCredits
                        ? `${fmt(free.evalCredits)} eval credits`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" + ")
                  : "—"}
              </TableCell>
              <TableCell sx={{ ...num, color: "text.tertiary" }}>
                adjustment
              </TableCell>
              <TableCell
                sx={{ ...num, color: free ? undefined : "text.tertiary" }}
              >
                {free ? `−${fmt(free.credits)}` : "0"}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell sx={title}>Charged</TableCell>
              <TableCell />
              <TableCell />
              <TableCell />
              <TableCell
                sx={{ ...num, fontWeight: 600, color: "text.primary" }}
              >
                {fmt(billed)}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Box>

      {/* ── not in the total: paid elsewhere, pending, waived, never run ── */}
      <Box>
        <SectionTitle
          title="Not in the total"
          note="Charged elsewhere, pending, or not charged"
        />
        <Table sx={tableSx}>
          <TableHead>
            <TableRow>
              <TableCell>Item</TableCell>
              <TableCell>Status</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Amount</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {outside.map((o) => (
              <TableRow key={o.label}>
                <TableCell sx={title}>{o.label}</TableCell>
                <TableCell sx={val}>{o.status}</TableCell>
                <TableCell sx={num}>{o.amount}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      {/* ── cost history: the run's billing events, in order ── */}
      <Box>
        <SectionTitle
          title="Cost history"
          note="Every usage event on this run, charged once"
        />
        <Table sx={tableSx}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 140 }}>When</TableCell>
              <TableCell>Event</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Credits</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {history.map((h) => (
              <TableRow key={h.event}>
                <TableCell sx={val}>{h.when}</TableCell>
                <TableCell sx={h.strong ? title : { ...val, typography: "s2" }}>
                  {h.event}
                  {h.detail && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", typography: "s3" }}
                    >
                      {` · ${h.detail}`}
                    </Box>
                  )}
                </TableCell>
                <TableCell
                  sx={{
                    ...num,
                    ...(h.strong && { fontWeight: 600, color: "text.primary" }),
                    ...(h.quiet && { color: "text.tertiary" }),
                  }}
                >
                  {h.credits}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {`${BILLING_POLICY.refund} ${BILLING_POLICY.waive} ${BILLING_POLICY.notStarted} ${BILLING_POLICY.once}`}
        </Typography>
      </Box>

      {/* ── by scenario: each scenario's calls, most expensive first ── */}
      <Box>
        <SectionTitle
          title="By scenario"
          note="Most expensive first · click one to open it"
        />
        <Table sx={{ ...tableSx, "& tbody tr": { height: 44 } }}>
          <TableHead>
            <TableRow>
              <TableCell>Scenario</TableCell>
              <TableCell>Status</TableCell>
              <TableCell sx={{ width: 180 }}>Share of total</TableCell>
              <TableCell sx={{ textAlign: "right" }}>
                {voice ? "Minutes" : "Tokens"}
              </TableCell>
              <TableCell sx={{ textAlign: "right" }}>Simulation</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Evals</TableCell>
              <TableCell sx={{ textAlign: "right" }}>Credits</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((r) => {
              const t = r.task;
              const sub = [
                scenarioTitle(t.title),
                t.persona?.name,
                plural(r.samples, callWord),
              ]
                .filter(Boolean)
                .join(" · ");
              const dim = r.charged ? {} : { color: "text.tertiary" };
              return (
                <Tooltip
                  key={t.id}
                  arrow
                  placement="top-start"
                  title={r.why || ""}
                  disableHoverListener={!r.why}
                >
                  <TableRow
                    hover={!!onOpenTask}
                    tabIndex={onOpenTask ? 0 : undefined}
                    onClick={onOpenTask ? () => onOpenTask(t) : undefined}
                    onKeyDown={
                      onOpenTask
                        ? (e) => e.key === "Enter" && onOpenTask(t)
                        : undefined
                    }
                    sx={{ cursor: onOpenTask ? "pointer" : "default" }}
                  >
                    <TableCell sx={{ ...twoLine, maxWidth: 0, width: "100%" }}>
                      <Typography noWrap sx={{ ...title, ...dim }}>
                        {variantOf(t.id)}
                      </Typography>
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {sub}
                      </Typography>
                    </TableCell>
                    <TableCell sx={twoLine}>
                      {r.charged ? (
                        <StatusChip status={t.status} />
                      ) : (
                        <Typography sx={{ ...val, color: "text.tertiary" }}>
                          Not charged
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ ...twoLine, width: 180, minWidth: 180 }}>
                      {r.charged ? (
                        <Share value={r.credits / (actual || maxCredits)} />
                      ) : null}
                    </TableCell>
                    <TableCell sx={{ ...twoLine, ...num, ...dim }}>
                      {qtyLabel(r.qty, r.unit)}
                    </TableCell>
                    <TableCell sx={{ ...twoLine, ...num, ...dim }}>
                      {fmt(r.simCredits)}
                    </TableCell>
                    <TableCell sx={{ ...twoLine, ...num, ...dim }}>
                      {fmt(r.evalCredits)}
                    </TableCell>
                    <TableCell
                      sx={{
                        ...twoLine,
                        ...num,
                        ...(r.charged
                          ? { color: "text.primary", fontWeight: 600 }
                          : dim),
                      }}
                    >
                      {fmt(r.credits)}
                    </TableCell>
                  </TableRow>
                </Tooltip>
              );
            })}
          </TableBody>
        </Table>
        {rows.length > PAGE && (
          <Button
            size="small"
            onClick={() => setShowAll((v) => !v)}
            sx={{ mt: 0.75, color: "text.secondary" }}
          >
            {showAll ? "Show fewer" : `Show all ${rows.length}`}
          </Button>
        )}
      </Box>
    </Stack>
  );
}

CostTab.propTypes = {
  run: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  tasks: PropTypes.array.isRequired,
  onOpenTask: PropTypes.func,
};
