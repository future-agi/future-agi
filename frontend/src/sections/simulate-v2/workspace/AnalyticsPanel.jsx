import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { SectionCard, EmptyState } from "../components/primitives";
import { runStatsOf } from "../_mock/runCompare";
import { runEnvVersion } from "../_mock/toolFit";
import { causeOf, VERDICT_CAUSES } from "../_mock/decoupled";
import { sourceLinkOf } from "../_mock/scenarioProvenance";
import { envLabelOf } from "../_mock/envVersionKinds";
import { runSummaries } from "../_mock/comparison";
import { groupsOf } from "../_mock/scenarioOrg";
import { goalLabel } from "../_mock/scenarios";

/**
 * Trends across this environment's runs (roadmap: Runs → "Environment
 * Analytics"): pass rate by agent version, and the latest run broken down by
 * where its scenarios came from and how each one ended. When the runs span
 * environment versions the test itself changed between them, and that is
 * said beside the numbers rather than drawn as one continuous trend.
 */
const pct = (n, d) => (d ? `${Math.round((n / d) * 100)}%` : "—");
const tasksOf = (run) =>
  run?.tasks?.length ? run.tasks : run?.trialTasks || [];

export default function AnalyticsPanel({ env, envState }) {
  const runs = useMemo(
    () =>
      /* The same run records the Runs tab reads, with their outcomes. */
      runSummaries(env, envState)
        .filter((r) => r.finishedAt && !r.synthetic && r.kind !== "trial")
        .sort((a, b) => Date.parse(a.finishedAt) - Date.parse(b.finishedAt)),
    [env, envState],
  );
  const latest = runs[runs.length - 1] || null;

  const byVersion = useMemo(() => {
    const m = new Map();
    runs.forEach((r) => {
      const v = r.agentVersion || "v1";
      const s = runStatsOf(r);
      const row = m.get(v) || { version: v, runs: 0, latest: null };
      row.runs += 1;
      row.latest = { run: r, stats: s };
      m.set(v, row);
    });
    return [...m.values()];
  }, [runs]);

  const envVersions = useMemo(
    () => [
      ...new Set(
        runs.map((r) => runEnvVersion(env, envState, r)).filter(Boolean),
      ),
    ],
    [runs, env, envState],
  );

  const scenarios = useMemo(
    () => new Map((envState?.scenarios || []).map((s) => [s.id, s])),
    [envState?.scenarios],
  );
  const bySource = useMemo(() => {
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const sc = scenarios.get(t.scenarioId || t.id);
      const label = sc
        ? sourceLinkOf(sc, { env, envState }).label
        : "Not in the suite now";
      const row = m.get(label) || { label, total: 0, passed: 0 };
      row.total += 1;
      if (t.status === "passed") row.passed += 1;
      m.set(label, row);
    });
    return [...m.values()].sort((a, b) => b.total - a.total);
  }, [latest, scenarios, env, envState]);

  const byOutcome = useMemo(() => {
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const cause = t.status === "passed" ? "pass" : causeOf(t);
      const label = VERDICT_CAUSES[cause]?.label || cause;
      m.set(label, (m.get(label) || 0) + 1);
    });
    return [...m.entries()]
      .map(([label, n]) => ({ label, n }))
      .sort((a, b) => b.n - a.n);
  }, [latest]);

  /* Each run as a point on the trend: its channel, simulator model and how
     many scenarios it graded — a changed population is said beside it. */
  const trend = useMemo(
    () =>
      runs.map((r, i) => {
        const s = runStatsOf(r);
        const prev = i ? runStatsOf(runs[i - 1]).measured : null;
        return {
          run: `Run ${r.ordinal || i + 1}`,
          agent: r.agentVersion || "v1",
          channel:
            { voice: "Voice", multi: "Voice + text" }[
              r.surface || env?.surface
            ] || "Text",
          model: r.simulator?.label || "—",
          graded:
            prev != null && prev !== s.measured
              ? `${s.measured} (was ${prev})`
              : String(s.measured),
          pass: pct(s.scenarioPassed, s.measured),
        };
      }),
    [runs, env?.surface],
  );
  const populationChanged = trend.some((t) => t.graded.includes("was"));

  /* The latest run by scenario group — or by goal when there are no groups. */
  const byGroup = useMemo(() => {
    const groups = groupsOf(envState);
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const id = t.scenarioId || t.id;
      const labels = groups.length
        ? groups.filter((g) => g.ids.includes(id)).map((g) => g.name)
        : [goalLabel(t.useCase) || "Other"];
      (labels.length ? labels : ["No group"]).forEach((label) => {
        const row = m.get(label) || { label, total: 0, passed: 0 };
        row.total += 1;
        if (t.status === "passed") row.passed += 1;
        m.set(label, row);
      });
    });
    return {
      by: groups.length ? "Group" : "Goal",
      rows: [...m.values()].sort((a, b) => b.total - a.total),
    };
  }, [latest, envState]);

  if (!runs.length)
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:chart-2-linear"
          title="No runs to chart yet"
          body="Analytics fills in after the first finished run of this environment."
        />
      </Box>
    );

  const total = tasksOf(latest).length;
  return (
    <Stack spacing={2} sx={{ p: 2, maxWidth: 960 }}>
      {envVersions.length > 1 && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`These runs span ${envVersions.length} environment versions (${envVersions.map((v) => envLabelOf(env, envState, v)).join(", ")}). The test changed between them, so a rate in one isn't the same measurement as a rate in another — compare runs on the scenarios they share.`}
        </Typography>
      )}

      <SectionCard
        title="Pass rate by agent version"
        subtitle="Each version's latest finished run — scenarios that passed a majority of their repeats."
        dense
      >
        <Table
          head={["Agent version", "Runs", "Latest run", "Scenario pass"]}
          rows={byVersion.map((r) => [
            r.version,
            r.runs,
            `Run ${r.latest.run.ordinal || ""}`.trim(),
            `${r.latest.stats.scenarioPassed} of ${r.latest.stats.measured} · ${pct(r.latest.stats.scenarioPassed, r.latest.stats.measured)}`,
          ])}
        />
      </SectionCard>

      <SectionCard
        title="Pass rate over time"
        subtitle={
          populationChanged
            ? "Every finished run, oldest first. The number of graded scenarios changed between some runs — those rates aren't over the same population."
            : "Every finished run, oldest first, with the channel and simulator model it ran on."
        }
        dense
      >
        <Table
          head={[
            "Run",
            "Agent",
            "Channel",
            "Simulator model",
            "Graded",
            "Scenario pass",
          ]}
          rows={trend.map((t) => [
            t.run,
            t.agent,
            t.channel,
            t.model,
            t.graded,
            t.pass,
          ])}
        />
      </SectionCard>

      <SectionCard
        title={`Latest run by ${byGroup.by.toLowerCase()}`}
        subtitle={`Run ${latest.ordinal || ""} · ${byGroup.by === "Group" ? "a scenario in several groups counts in each" : "the goal each scenario tests"}.`}
        dense
      >
        <Table
          head={[byGroup.by, "Scenarios", "Passed"]}
          rows={byGroup.rows.map((r) => [
            r.label,
            r.total,
            `${r.passed} · ${pct(r.passed, r.total)}`,
          ])}
        />
      </SectionCard>

      <SectionCard
        title="Latest run by scenario source"
        subtitle={`Run ${latest.ordinal || ""} · where each scenario came from, and how many of them passed.`}
        dense
      >
        <Table
          head={["Source", "Scenarios", "Passed"]}
          rows={bySource.map((r) => [
            r.label,
            r.total,
            `${r.passed} · ${pct(r.passed, r.total)}`,
          ])}
        />
      </SectionCard>

      <SectionCard
        title="Latest run by outcome"
        subtitle="Failures the agent caused are kept apart from tests that couldn't measure it."
        dense
      >
        <Table
          head={["Outcome", "Scenarios", "Share"]}
          rows={byOutcome.map((r) => [r.label, r.n, pct(r.n, total)])}
        />
      </SectionCard>
    </Stack>
  );
}

AnalyticsPanel.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
};

/* A spec-sheet table: the first column reads, the rest are aligned numbers. */
function Table({ head, rows }) {
  const cols = `minmax(180px, 2fr) ${head
    .slice(1)
    .map(() => "minmax(96px, 1fr)")
    .join(" ")}`;
  const cell = (i) => ({
    typography: "s2",
    textAlign: i === 0 ? "left" : "right",
    fontVariantNumeric: "tabular-nums",
    minWidth: 0,
  });
  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: cols,
          gap: 2,
          px: 2,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {head.map((h, i) => (
          <Typography
            key={h}
            sx={{ ...cell(i), typography: "s3", color: "text.tertiary" }}
          >
            {h}
          </Typography>
        ))}
      </Box>
      {rows.map((r) => (
        <Box
          key={r[0]}
          sx={{
            display: "grid",
            gridTemplateColumns: cols,
            gap: 2,
            px: 2,
            py: 0.875,
            "& + &": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {r.map((v, i) => (
            <Typography key={`${r[0]}-${i}`} noWrap sx={cell(i)}>
              {v}
            </Typography>
          ))}
        </Box>
      ))}
    </Box>
  );
}

Table.propTypes = {
  head: PropTypes.array.isRequired,
  rows: PropTypes.array.isRequired,
};
