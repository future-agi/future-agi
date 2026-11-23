import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { diagnosisSummaryOf, goalWords, rejectedFor } from "../_mock/optimizer";
import { plural } from "../_mock/nudgeEffects";
import {
  repeatsOf,
  limitRows,
  runsCountOf,
  waitingAt,
  improvementTotalSpend,
  startMsOf,
  stopImprovement,
  IMPROVEMENT_PHASES,
  OPT_STATUS,
  DEFAULT_LIMITS,
} from "../_mock/optimizationRuns";
import { creditsForUsd, creditsLabel } from "../_mock/metering";
import { envVersionNameOf } from "../_mock/codeRefs";
import { mayChangeListOf } from "./constraintEdits";

/*
  A running epoch, as the stages it goes through — Queued, Reproducing,
  Diagnosing, Proposing, Checking (Stopping while a stop settles) — one row
  each, in the key-value shape of the verdict that replaces it when it
  finishes. The one running view on every path. A finished stage keeps its
  one-line result; only the stage running now says more, and how long it
  has been going and what it has spent (a stage can take up to an hour).
  The candidates it tries arrive in the table below; this says only where
  it is and what each stage is working from.
*/
const NUM = { fontVariantNumeric: "tabular-nums" };

/* "14 min ago", "1 h 5 min ago". */
const agoLabel = (ms) => {
  const m = Math.max(0, Math.floor(ms / 60000));
  if (m < 1) return "under a minute ago";
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""} ago`;
};

/* A text with its first mention of each link token made a text link. */
function Linked({ text, links }) {
  const parts = [text];
  links.forEach(({ token, onClick }) => {
    const at = parts.findIndex(
      (p) => typeof p === "string" && p.includes(token),
    );
    if (at < 0) return;
    const [before, ...rest] = parts[at].split(token);
    parts.splice(
      at,
      1,
      before,
      <Link
        key={token}
        component="button"
        type="button"
        underline="hover"
        onClick={onClick}
        sx={{
          typography: "inherit",
          fontWeight: "inherit",
          color: "text.primary",
          verticalAlign: "baseline",
          textDecoration: "underline",
          textUnderlineOffset: "3px",
        }}
      >
        {token}
      </Link>,
      rest.join(token),
    );
  });
  return parts.filter((p) => p !== "");
}

function StateIcon({ state }) {
  if (state === "now") {
    return (
      <Box
        sx={{
          width: 11,
          height: 11,
          borderRadius: "50%",
          flexShrink: 0,
          border: "1.5px solid",
          borderColor: "primary.main",
          borderTopColor: "transparent",
          animation: "stage-spin 0.7s linear infinite",
          "@keyframes stage-spin": { to: { transform: "rotate(360deg)" } },
        }}
      />
    );
  }
  return (
    <Iconify
      icon={
        state === "done" ? "solar:check-circle-linear" : "solar:circle-linear"
      }
      width={16}
      sx={{
        color: state === "done" ? "text.primary" : "text.disabled",
        flexShrink: 0,
      }}
    />
  );
}
StateIcon.propTypes = { state: PropTypes.string };

export default function ImprovementStages({
  record,
  envState,
  progress,
  model = null,
  onOpenIssue,
  onOpenRun,
}) {
  /* An ending already known before searching plays Reproduce on the search it would have run. */
  const r = record.result || record.pendingResult || {};
  const stage = progress?.stage || "reproduce";
  const done = !!progress?.done;
  const feed = record.trigger?.kind === "error-feed";
  const issueId = record.trigger?.issueId;
  const fromRun =
    (envState?.runs || []).find((x) => x.id === record.fromRunId) || null;
  /* A git agent's version reads as its commit ("main @ 3f9a2c1"), never vN. */
  const version = record.baseAgentVersion
    ? envVersionNameOf(envState, record.baseAgentVersion, fromRun?.envId).main
    : "today's version";
  const rep = record.reproduce || {};
  const reps = repeatsOf(record);
  const cal = record.calibration || r.calibration || null;
  /* The environment's own scenarios, measured on today's version alongside. */
  /* The environment's own — a fix's first run also carries the issue's. */
  const riding = new Set((fromRun?.fixScenarios || []).map((x) => x.id));
  const envCount = (fromRun?.scenarioIds || []).filter(
    (id) => !riding.has(id),
  ).length;
  const diag = diagnosisSummaryOf(record, r);
  const cause =
    (diag?.cause || record.snapshot?.rootCause || "").replace(/\.$/, "") ||
    null;
  const suggestion = record.fixDepth === "suggestion";
  /* Started on a goal (New improvement, a schedule, Run now) — not from an
     issue or a run's Debug failures, which start from a cause. */
  const goal = ["goal", "schedule", "manual"].includes(record.trigger?.kind)
    ? record.trigger.goal || { id: "pass-rate" }
    : null;
  const shown = (r.trials || []).slice(0, progress?.runsDone || 0);
  const scored = shown.filter((t) => !rejectedFor(t));
  const best = scored.length ? Math.max(...scored.map((t) => t.score)) : null;
  /* "2 of up to 8 runs" while searching; an early stop is said once played. */
  const count = runsCountOf(progress, r) || {
    done: 0,
    max: 0,
    upTo: true,
    stopped: null,
  };
  /* The local timer's three stages, read as the plan's: the search stage is
     Diagnosing until its diagnose step has played, then Proposing. */
  const order = ["reproduce", "diagnose", "search", "verify"];
  const here =
    stage === "search" && progress?.step === "diagnose" ? "diagnose" : stage;
  const stateOf = (key) => {
    if (done) return "done";
    const a = order.indexOf(key);
    const b = order.indexOf(here);
    return a < b ? "done" : a === b ? "now" : "todo";
  };
  /* Waiting on the environment's run: Queued, in Reproducing's place. A stop
     in progress reads Stopping on the stage it interrupts. */
  const running = record.status === OPT_STATUS.RUNNING;
  const queued = running && !done && waitingAt(record);
  const stopping = running && !done && !!record.stopRequestedAt;
  const nameOf = (key, label) =>
    stopping && stateOf(key) === "now"
      ? IMPROVEMENT_PHASES.stopping
      : key === "reproduce" && queued
        ? IMPROVEMENT_PHASES.queued
        : label;

  const rows = [
    {
      key: "reproduce",
      label: nameOf("reproduce", IMPROVEMENT_PHASES.reproducing),
      text:
        feed && rep.total
          ? stateOf("reproduce") === "now"
            ? `Running ${plural(rep.total, "scenario")} from ${issueId}${envCount ? ` and the environment's ${envCount}` : ""} on ${version}, ${reps} times each`
            : `Reproduced on ${rep.reproduced || 0} of ${plural(rep.total, "scenario")} from ${issueId}, on ${version}`
          : fromRun?.measuredFor
            ? stateOf("reproduce") === "now"
              ? `Running the environment's ${plural(envCount, "scenario")} on ${version}, ${reps} times each`
              : `Run ${fromRun.ordinal} measured it today: ${fromRun.passed ?? 0} of ${fromRun.total ?? envCount} pass every repeat`
            : fromRun
              ? `Starts from Run ${fromRun.ordinal} on ${version}`
              : stateOf("reproduce") === "now"
                ? `Running the scenarios on ${version}`
                : `Measured ${version} on ${plural((r.trainMeasured || 0) + (r.heldMeasured || 0), "scenario")}`,
      sub: [
        feed && rep.total
          ? `Scenarios from ${issueId} are for this epoch only.`
          : null,
        /* What measuring today found in the environment — once it has. */
        feed && fromRun && stateOf("reproduce") === "done"
          ? `Run ${fromRun.ordinal} measured it today: ${fromRun.passed ?? 0} of ${fromRun.total ?? envCount} pass every repeat.`
          : null,
        /* What else that run found — never this fix's to work on. */
        feed && stateOf("reproduce") === "done" && (r.otherFailing || []).length
          ? `Also found ${plural(r.otherFailing.length, "other failure")}, left alone.`
          : null,
        stateOf("reproduce") === "done" && rep.inconclusive
          ? `${plural(rep.inconclusive, "scenario")} failed only once, so ${rep.inconclusive === 1 ? "it isn't a target" : "they aren't targets"}.`
          : null,
        stateOf("reproduce") === "done" && cal && r.noise != null
          ? `The current version varied by ${r.noise} pts between repeats.`
          : null,
      ]
        .filter(Boolean)
        .join(" "),
      /* The environment's run, a link where the line names it. */
      link:
        fromRun &&
        onOpenRun &&
        (!fromRun.measuredFor || stateOf("reproduce") === "done")
          ? { token: `Run ${fromRun.ordinal}`, onClick: onOpenRun }
          : null,
    },
    {
      key: "diagnose",
      label: nameOf("diagnose", IMPROVEMENT_PHASES.diagnosing),
      text: suggestion
        ? "Reads the failures to write up the cause and the change to make"
        : cause
          ? feed
            ? `Starts from the cause in ${issueId}: ${cause}.`
            : `Starts from its diagnosis: ${cause}.`
          : goal
            ? stateOf("reproduce") === "done"
              ? `Aimed at ${goalWords(goal)}: ${plural(r.goalTargets || 0, "scenario")} short of it today.`
              : `Aimed at ${goalWords(goal)}.`
            : "Reads the failures to find what to change",
      sub:
        cause && goal
          ? stateOf("reproduce") === "done"
            ? `Aimed at ${goalWords(goal)}: ${plural(r.goalTargets || 0, "scenario")} short of it today.`
            : `Aimed at ${goalWords(goal)}.`
          : null,
    },
    {
      key: "search",
      label: nameOf("search", IMPROVEMENT_PHASES.searching),
      text: suggestion
        ? "No change is tried — this agent can only be called. You get a suggested change to apply yourself."
        : stateOf("search") === "todo"
          ? "Tries changes one at a time; stops early once candidates stop beating the best"
          : count.stopped
            ? `${plural(count.done, "candidate")} of up to ${count.max} · ${count.stopped}${best != null ? ` · best ${best}%` : ""} on ${plural(r.trainMeasured || 0, "target scenario")}`
            : `${count.done} of ${count.upTo ? "up to " : ""}${plural(count.max, "candidate")}${best != null ? ` · best so far ${best}%` : ""} on ${plural(r.trainMeasured || 0, "target scenario")}`,
      /* Voice runs in real time, so the search keeps a slice — said, not hidden. */
      sub: r.voiceCap
        ? `Voice calls run in real time, so it searches on ${r.voiceCap.scenarios} of ${r.voiceCap.of} scenarios. Must-pass still covers all.`
        : null,
    },
    {
      key: "verify",
      label: nameOf("verify", IMPROVEMENT_PHASES.verifying),
      text: suggestion
        ? "Writes up the cause and the change to make"
        : [
            `${stateOf("verify") === "done" ? "Checked" : "Checks"} the best candidate on`,
            r.heldMeasured
              ? `${plural(r.heldMeasured, "held-back scenario")} it never saw`
              : null,
            r.heldMeasured && r.mustPassMeasured != null ? "and" : null,
            r.mustPassMeasured != null
              ? plural(r.mustPassMeasured, "must-pass scenario")
              : null,
          ]
            .filter(Boolean)
            .join(" "),
      /* Where must-pass comes from (RSI PRD: marked + regression memory +
         passing every repeat), and why nothing was held back when it wasn't. */
      sub:
        [
          r.mustPass
            ? `Must-pass: ${[`${r.mustPass.passing} passing every repeat today`, r.mustPass.marked ? `${r.mustPass.marked} marked` : null, r.mustPass.remembered ? `${r.mustPass.remembered} from regression memory` : null].filter(Boolean).join(", ")}.`
            : null,
          /* An issue's fix: the winner once more on what else failed today,
             to say what it fixed along the way. */
          (r.otherFailing || []).length
            ? `Then once on today's ${plural(r.otherFailing.length, "other failure")}, to see if any were fixed along the way.`
            : null,
          !r.heldMeasured && !suggestion
            ? "Too few scenarios reproduced to hold any back, so the gain can't be checked on unseen ones."
            : null,
        ]
          .filter(Boolean)
          .join(" ") || null,
    },
  ];

  /* What every run is held to, visible while they're tried (RSI PRD: constraints
     are visible in setup, during the run and on the proposal). */
  const mustPassTotal =
    (r.mustPass?.total ?? record.frozen?.mustPass ?? 0) +
    (r.otherEnvs || []).reduce((n, e) => n + (e.mustPass || 0), 0);
  /* What may change reads as the agent's rules name it; the budget is said
     on the running stage's progress line. */
  const heldTo = limitRows(record.limits || DEFAULT_LIMITS, model, {
    mustPass: mustPassTotal,
  })
    .filter(
      (c) =>
        !(record.budget && c.id === "budget") &&
        !["cost", "latency"].includes(c.id),
    )
    .map((c) =>
      c.id === "changes"
        ? {
            ...c,
            label: "May change",
            value: mayChangeListOf(
              record.limits || DEFAULT_LIMITS,
              envState?.agent || null,
            ),
          }
        : c,
    );

  /* How long the running stage has been going and what it has spent:
     "Started 14 min ago · 3,120 of 25,000 credits spent". */
  const progressLine = (() => {
    if (!running || done || queued) return null;
    const started = `Started ${agoLabel(Date.now() - startMsOf(record))}`;
    if (!progress) return started;
    const spent = creditsForUsd(
      improvementTotalSpend(
        stopImprovement(record, progress),
        record.surface || envState?.surface,
      ),
    ).toLocaleString();
    return record.budget
      ? `${started} · ${spent} of ${creditsLabel(record.budget)} spent`
      : `${started} · ${spent} credits spent`;
  })();

  /* Each link once: the issue where a line first names it, the run likewise. */
  const issueLink =
    feed && issueId && onOpenIssue
      ? { token: issueId, onClick: onOpenIssue }
      : null;
  const issueRow = issueLink
    ? rows.find((row) => String(row.text).includes(issueId))?.key
    : null;

  return (
    <Box
      sx={{
        mb: 2,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      {rows.map((row, i) => {
        const state = stateOf(row.key);
        const quiet = state === "todo";
        const links = [
          ...(row.key === issueRow ? [issueLink] : []),
          ...(row.link && String(row.text).includes(row.link.token)
            ? [row.link]
            : []),
        ];
        /* A run link the line doesn't name goes at its end. */
        const trailing =
          row.link && !String(row.text).includes(row.link.token)
            ? row.link
            : null;
        return (
          <Stack
            key={row.key}
            direction="row"
            alignItems="flex-start"
            spacing={1.5}
            sx={{
              px: 2,
              py: 1,
              ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
            }}
          >
            <Box
              sx={{
                width: 16,
                height: 20,
                display: "grid",
                placeItems: "center",
                flexShrink: 0,
              }}
            >
              <StateIcon state={state} />
            </Box>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: quiet ? 500 : 600,
                color: quiet ? "text.subtitle" : "text.primary",
                width: 128,
                flexShrink: 0,
                lineHeight: "20px",
              }}
            >
              {row.label}
            </Typography>
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: quiet ? 400 : 600,
                  color: quiet ? "text.subtitle" : "text.primary",
                  lineHeight: "20px",
                  ...NUM,
                }}
              >
                <Linked text={row.text} links={links} />
                {trailing && (
                  <>
                    {" · "}
                    <Linked text={trailing.token} links={[trailing]} />
                  </>
                )}
              </Typography>
              {/* The detail is the running stage's alone. */}
              {row.sub && state === "now" && (
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
                >
                  {row.sub}
                </Typography>
              )}
              {state === "now" && progressLine && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    mt: 0.25,
                    ...NUM,
                  }}
                >
                  {progressLine}
                </Typography>
              )}
            </Box>
          </Stack>
        );
      })}
      {heldTo.length > 0 && (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Box
            sx={{
              width: 16,
              height: 20,
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
            }}
          >
            <Iconify
              icon="solar:shield-check-linear"
              width={16}
              sx={{ color: "text.secondary" }}
            />
          </Box>
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              width: 128,
              flexShrink: 0,
              lineHeight: "20px",
            }}
          >
            {/* One name for the agent's rules everywhere (#69). */}
            Constraints
          </Typography>
          <Stack
            direction="row"
            flexWrap="wrap"
            columnGap={2}
            rowGap={0.25}
            sx={{ flex: 1, minWidth: 0 }}
          >
            {heldTo.map((c) => (
              <Typography
                key={c.id}
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  lineHeight: "20px",
                }}
              >
                {`${c.label} · `}
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 600 }}
                >
                  {c.value}
                </Box>
              </Typography>
            ))}
          </Stack>
        </Stack>
      )}
    </Box>
  );
}

ImprovementStages.propTypes = {
  record: PropTypes.object.isRequired,
  envState: PropTypes.object,
  /* From improvementProgressOf: stage, runsDone, runsTotal, done. */
  progress: PropTypes.object,
  /* The model the agent runs on — what a model lock holds it to. */
  model: PropTypes.string,
  onOpenIssue: PropTypes.func,
  onOpenRun: PropTypes.func,
};
