import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { diagnosisSummaryOf, goalWords, rejectedFor } from "../_mock/optimizer";
import { plural } from "../_mock/nudgeEffects";
import {
  repeatsOf,
  limitRows,
  runsCountOf,
  DEFAULT_LIMITS,
} from "../_mock/optimizationRuns";
import { creditsLabel } from "../_mock/metering";
import { envVersionNameOf } from "../_mock/codeRefs";

/*
  A running improvement, as the stages a fix goes through — Reproduce,
  Search, Verify, then Ready for review — one row each, in the key-value
  shape of the verdict that replaces it when it finishes. The runs it tries
  arrive in the table below, as real runs; this says only where it is and
  what each stage is working from.
*/
const NUM = { fontVariantNumeric: "tabular-nums" };

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
  const order = ["reproduce", "search", "verify"];
  const stateOf = (key) => {
    if (done) return "done";
    const a = order.indexOf(key);
    const b = order.indexOf(stage);
    return a < b ? "done" : a === b ? "now" : "todo";
  };

  const rows = [
    {
      key: "reproduce",
      label: "Reproduce",
      text:
        feed && rep.total
          ? stateOf("reproduce") === "now"
            ? `Running ${plural(rep.total, "scenario")} from ${issueId}${envCount ? ` and the environment's ${envCount}` : ""} on ${version}, ${reps} times each`
            : `Reproduced on ${rep.reproduced || 0} of ${plural(rep.total, "scenario")} from ${issueId}, on ${version}`
          : fromRun?.measuredFor
            ? stateOf("reproduce") === "now"
              ? `Running the environment's ${plural(envCount, "scenario")} on ${version}, ${reps} times each`
              : `The environment's Run ${fromRun.ordinal} measured it today: ${fromRun.passed ?? 0} of ${fromRun.total ?? envCount} pass every repeat`
            : fromRun
              ? `Starts from Run ${fromRun.ordinal} on ${version}`
              : stateOf("reproduce") === "now"
                ? `Running the scenarios on ${version}`
                : `Measured ${version} on ${plural((r.trainMeasured || 0) + (r.heldMeasured || 0), "scenario")}`,
      sub: [
        feed && rep.total
          ? `Scenarios from ${issueId} are for this fix only.`
          : null,
        /* What measuring today found in the environment — once it has. */
        feed && fromRun && stateOf("reproduce") === "done"
          ? `The environment's Run ${fromRun.ordinal} measured it today: ${fromRun.passed ?? 0} of ${fromRun.total ?? envCount} of its scenarios pass every repeat.`
          : null,
        /* What else that run found — never this fix's to work on. */
        feed && stateOf("reproduce") === "done" && (r.otherFailing || []).length
          ? `Also found ${plural(r.otherFailing.length, "other failure")}, not part of this issue — the fix leaves ${r.otherFailing.length === 1 ? "it" : "them"} alone.`
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
      /* The environment's run, named so it isn't read as one of this page's runs. */
      link:
        fromRun &&
        onOpenRun &&
        (!fromRun.measuredFor || stateOf("reproduce") === "done")
          ? { label: `Environment Run ${fromRun.ordinal}`, onClick: onOpenRun }
          : null,
    },
    {
      key: "search",
      label: "Search",
      text: suggestion
        ? "No change is tried — this agent can only be called. You get a suggested change to apply yourself."
        : stateOf("search") === "todo"
          ? "Tries changes one at a time, each as a trial on this environment — it stops early once trials stop beating the best"
          : count.stopped
            ? `${plural(count.done, "trial")} of up to ${count.max} · ${count.stopped}${best != null ? ` · best ${best}%` : ""} on ${plural(r.trainMeasured || 0, "target scenario")}`
            : `${count.done} of ${count.upTo ? "up to " : ""}${plural(count.max, "trial")}${best != null ? ` · best so far ${best}%` : ""} on ${plural(r.trainMeasured || 0, "target scenario")}`,
      sub:
        [
          /* A goal aims at the scenarios short of it; an issue or a run's
           Debug failures, at the cause they found. */
          goal
            ? stateOf("reproduce") === "done"
              ? `Aimed at ${goalWords(goal)}: ${plural(r.goalTargets || 0, "scenario")} short of it today.`
              : `Aimed at ${goalWords(goal)}.`
            : cause
              ? feed
                ? `Starts from the cause in ${issueId}: ${cause}.`
                : `Starts from its diagnosis: ${cause}.`
              : null,
          /* Voice runs in real time, so the search keeps a slice — said, not hidden. */
          r.voiceCap
            ? `Voice calls run in real time, so it searches on ${r.voiceCap.scenarios} of the ${r.voiceCap.of} scenarios: the issue's, what fails today, then the rest. Must-pass still covers all of them.`
            : null,
        ]
          .filter(Boolean)
          .join(" ") || null,
      link:
        feed && onOpenIssue
          ? { label: `View ${issueId}`, onClick: onOpenIssue }
          : null,
    },
    {
      key: "verify",
      label: "Verify",
      text: suggestion
        ? "Writes up the cause and the change to make"
        : [
            `${stateOf("verify") === "done" ? "Checked" : "Checks"} the best trial on`,
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
    {
      key: "review",
      label: "Done",
      text: "A winner comes back as a new version. You decide whether to make it the default.",
      sub: null,
      state: "todo",
    },
  ];

  /* What every run is held to, visible while they're tried (RSI PRD: constraints
     are visible in setup, during the run and on the proposal). */
  const mustPassTotal =
    (r.mustPass?.total ?? record.frozen?.mustPass ?? 0) +
    (r.otherEnvs || []).reduce((n, e) => n + (e.mustPass || 0), 0);
  const heldTo = [
    ...limitRows(record.limits || DEFAULT_LIMITS, model, {
      mustPass: mustPassTotal,
    }).filter((c) => !(record.budget && c.id === "budget")),
    ...(record.budget
      ? [
          {
            id: "spend",
            label: "Run budget",
            value: creditsLabel(record.budget),
          },
        ]
      : []),
  ];

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
        const state = row.state || stateOf(row.key);
        const quiet = state === "todo";
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
                {row.text}
              </Typography>
              {row.sub && !quiet && (
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
                >
                  {row.sub}
                </Typography>
              )}
            </Box>
            {row.link && (
              <Button
                size="small"
                onClick={row.link.onClick}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
              >
                {row.link.label}
              </Button>
            )}
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
            Held to
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
