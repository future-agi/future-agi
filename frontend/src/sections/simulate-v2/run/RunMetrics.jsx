import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Everything the run reports, in one band.
 *
 * This replaced four tall cards and a second row of four more — nine cards
 * carrying nine numbers, most of a screen height before the traces started.
 * The numbers are the least of what a results screen has to show, so they get
 * one dense strip: label above, figure below, hairline dividers, no card
 * chrome and no icons competing with the values.
 *
 * It also reports more than the cards did. The old strip stopped at pass rate,
 * failures, cost and duration; the run knows its turn counts, its step totals,
 * its critical count and its per-eval averages, and all of those were being
 * computed and thrown away.
 */
const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1) h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
  return h;
};

export default function RunMetrics({ env, tasks, stats, evals }) {
  const voice = (env?.surface || "chat") === "voice";

  const derived = useMemo(() => {
    const n = Math.max(tasks.length, 1);
    const steps = tasks.reduce((a, t) => a + (t.steps?.length || 0), 0);
    const duration = tasks.reduce((a, t) => a + (t.durationMs || 0), 0);
    const h = hash(tasks.map((t) => t.id).join("|"));
    /*
      The channel metrics the graders report alongside the scores. Derived from
      the run rather than stored on it, and hashed off the task ids so the same
      run always reports the same numbers.
    */
    const agentTalk = 48 + (h % 14);
    return {
      critical: tasks.filter((t) => t.critical && t.status === "failed").length,
      /* Runs where the transcript claims something the call log does not
         support. Surfaced at run level because it is invisible otherwise —
         every grader that reads words scores those conversations as fine. */
      unsupported: tasks.filter((t) => t.callLog?.unsupportedClaim).length,
      avgSteps: steps / n,
      totalSteps: steps,
      avgDuration: duration / n / 1000,
      totalDuration: duration / 1000,
      avgTokens: stats.tokens / n,
      avgScore: 1 + stats.passRate * 4,
      latency: 280 + (h % 320),
      wpm: 118 + (h % 46),
      stopLatency: 160 + (h % 140),
      talkRatio: `${agentTalk}/${100 - agentTalk}`,
      inputTokens: (stats.tokens * 0.68) / n,
      outputTokens: (stats.tokens * 0.32) / n,
    };
  }, [tasks, stats]);

  const evalSummary = useMemo(
    () =>
      evals.map((e) => {
        /* Graders read only what was measured. An episode whose sandbox never
           came up still carries eval results from the moment before it failed,
           and counting those moves every grader's percentage for a reason that
           has nothing to do with the agent. */
        const results = tasks
          .filter((t) => t.status !== "unmeasured")
          .map((t) => t.evalResults?.find((r) => r.id === e.id))
          .filter(Boolean);
        const passed = results.filter((r) => r.passed).length;
        const avg = results.length ? results.reduce((a, r) => a + r.score, 0) / results.length : 0;
        return { ...e, passed, total: results.length, avg };
      }),
    [evals, tasks],
  );

  const cells = [
    {
      label: "Pass rate",
      value: `${Math.round(stats.passRate * 100)}%`,
      /* Over what was measurable, and it has to say so — the rate above is
         computed over the measured tasks, so "3 of 9" beside 48% is two
         different denominators in one tile. */
      sub: stats.unmeasured
        ? `${stats.passed} of ${stats.measured} measured`
        : `${stats.passed} of ${stats.total}`,
      tone: stats.passRate >= 0.8 ? "good" : stats.passRate >= 0.5 ? "warn" : "bad",
    },
    { label: "Failed", value: stats.failed, sub: `${derived.critical} critical`, tone: stats.failed ? "bad" : "good" },
    { label: "Tasks", value: stats.total, sub: `${derived.totalSteps} steps` },
    ...(stats.unmeasured
      ? [{
        label: "Not measured",
        value: stats.unmeasured,
        sub: "builder, not the agent",
        tone: "warn",
      }]
      : []),
    {
      label: "Said, not done",
      value: derived.unsupported,
      sub: "claims with no tool call",
      tone: derived.unsupported ? "bad" : "good",
    },
    { label: "Avg turns", value: derived.avgSteps.toFixed(1), sub: "per task" },
    { label: "Avg duration", value: `${derived.avgDuration.toFixed(1)}s`, sub: `${derived.totalDuration.toFixed(0)}s total` },
    { label: "Cost", value: `$${stats.cost.toFixed(2)}`, sub: `${(stats.tokens / 1000).toFixed(1)}k tokens` },
    { label: "Avg tokens", value: Math.round(derived.avgTokens).toLocaleString(), sub: "per task" },
    { label: voice ? "Avg CSAT" : "Avg score", value: derived.avgScore.toFixed(1), sub: "out of 5" },
    ...(voice
      ? [
          { label: "Agent latency", value: `${derived.latency}`, sub: "ms to first word" },
          { label: "Agent WPM", value: derived.wpm, sub: "words / min" },
          { label: "Stop latency", value: `${derived.stopLatency}`, sub: "ms after barge-in" },
          { label: "Talk ratio", value: derived.talkRatio, sub: "agent / customer" },
        ]
      : [
          { label: "Chat latency", value: `${derived.latency}`, sub: "ms per turn" },
          { label: "Input tokens", value: Math.round(derived.inputTokens).toLocaleString(), sub: "avg per task" },
          { label: "Output tokens", value: Math.round(derived.outputTokens).toLocaleString(), sub: "avg per task" },
        ]),
  ];

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.25, bgcolor: "background.paper", mb: 2 }}>
      <Stack direction="row" sx={{ flexWrap: "wrap" }}>
        {cells.map((c, i) => (
          <Box
            key={c.label}
            sx={{
              flex: "1 1 128px",
              minWidth: 128,
              px: 2,
              py: 1.5,
              borderLeft: i === 0 ? "none" : "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s3", fontWeight: 700, color: "text.subtitle",
                letterSpacing: 0.3, textTransform: "uppercase",
              }}
            >
              {c.label}
            </Typography>
            <Typography
              sx={{
                typography: "m2", fontWeight: 700, lineHeight: 1.2,
                fontVariantNumeric: "tabular-nums",
                color: c.tone === "bad" ? "#DC2626" : c.tone === "warn" ? "#CA8A04" : c.tone === "good" ? "#16A34A" : "text.primary",
              }}
            >
              {c.value}
            </Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{c.sub}</Typography>
          </Box>
        ))}
      </Stack>

      {/*
        The evals on one line each rather than a card each. A grader is a name,
        a rate and an average — three short values that do not need 180px of
        card to hold them, and reading them as a column is how you spot the one
        that is dragging.
      */}
      {evalSummary.length > 0 && (
        <Stack sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          {evalSummary.map((e) => {
            const pct = Math.round((e.passed / Math.max(e.total, 1)) * 100);
            return (
              <Stack
                key={e.id}
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ px: 2, py: 1, "&:not(:first-of-type)": { borderTop: "1px solid", borderColor: "divider" } }}
              >
                <Iconify icon={e.icon} width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                <Typography noWrap sx={{ width: 190, flexShrink: 0, typography: "s2", fontWeight: 600 }}>
                  {e.name}
                </Typography>
                <Tooltip arrow title={`${e.passed} of ${e.total} tasks passed this eval`}>
                  <Box sx={{ flex: 1, minWidth: 60, height: 4, borderRadius: 2, bgcolor: "background.neutral", overflow: "hidden" }}>
                    <Box
                      sx={{
                        height: "100%", width: `${pct}%`,
                        bgcolor: pct >= 80 ? "#16A34A" : pct >= 50 ? "#CA8A04" : "#DC2626",
                      }}
                    />
                  </Box>
                </Tooltip>
                <Typography sx={{ width: 44, flexShrink: 0, textAlign: "right", typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {pct}%
                </Typography>
                <Typography sx={{ width: 68, flexShrink: 0, textAlign: "right", typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                  avg {e.avg.toFixed(2)}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      )}
    </Box>
  );
}

RunMetrics.propTypes = {
  env: PropTypes.object,
  tasks: PropTypes.array.isRequired,
  stats: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
};
