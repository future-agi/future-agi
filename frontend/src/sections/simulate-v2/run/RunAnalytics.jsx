import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { SectionCard, cardGrid } from "../components/primitives";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { modeOf, FAILURE_MODES } from "../_mock/coverage";

/**
 * Analytics.
 *
 * The traces answer "what happened in this scenario". This answers the two
 * questions a table of rows cannot: where the scores cluster for each grader,
 * and which kind of scenario the agent is actually losing on.
 *
 * The second is the one worth having. A pass rate of 43% says nothing about
 * whether the agent is broadly mediocre or specifically incapable of holding a
 * rule under pressure — and those need completely different fixes.
 */
export default function RunAnalytics({ tasks, evals }) {
  const buckets = useMemo(
    () =>
      evals.map((e) => {
        const scores = tasks
          .map((t) => t.evalResults?.find((r) => r.id === e.id)?.score)
          .filter((n) => typeof n === "number");
        const bands = [0, 0, 0, 0, 0];
        scores.forEach((sc) => { bands[Math.min(4, Math.floor(sc * 5))] += 1; });
        return { ...e, bands, count: scores.length };
      }),
    [evals, tasks],
  );

  const byMode = useMemo(() => {
    const acc = new Map();
    tasks.forEach((t) => {
      const m = modeOf(t);
      const cur = acc.get(m) || { passed: 0, total: 0 };
      cur.total += 1;
      if (t.status === "passed") cur.passed += 1;
      acc.set(m, cur);
    });
    return FAILURE_MODES.filter((m) => acc.has(m.id)).map((m) => ({ ...m, ...acc.get(m.id) }));
  }, [tasks]);

  return (
    <Stack spacing={2}>
      <SectionCard
        title="Score distribution"
        subtitle="Where each grader's scores land — a flat spread and a cliff mean different things"
      >
        <Stack sx={{ p: 2.5 }} spacing={2}>
          {buckets.map((b) => (
            <Box key={b.id}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
                <Typography sx={{ flex: 1, typography: "s2", fontWeight: 600 }}>{b.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{b.count} tasks</Typography>
              </Stack>
              <Stack direction="row" spacing={0.5}>
                {b.bands.map((n, i) => (
                  <Box key={i} sx={{ flex: 1 }}>
                    <Box
                      sx={{
                        height: 34, borderRadius: 0.75,
                        bgcolor: n ? interpolateColorBasedOnScore((i + 0.5) / 5, 1) : "background.neutral",
                        display: "grid", placeItems: "center",
                      }}
                    >
                      <Typography sx={{ typography: "s2", fontWeight: 700, color: n ? "text.primary" : "text.disabled" }}>
                        {n || "—"}
                      </Typography>
                    </Box>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", textAlign: "center", mt: 0.25 }}>
                      {i * 20}–{(i + 1) * 20}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </Box>
          ))}
        </Stack>
      </SectionCard>

      <SectionCard
        title="Pass rate by failure mode"
        subtitle="Which kind of scenario the agent is losing on, which is what decides the fix"
      >
        <Box sx={{ ...cardGrid(200), gap: 1.5, p: 2.5 }}>
          {byMode.map((m) => {
            const pct = Math.round((m.passed / Math.max(m.total, 1)) * 100);
            return (
              <Box key={m.id} sx={{ p: 1.75, border: "1px solid", borderColor: "divider", borderRadius: 1.25 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{m.label}</Typography>
                <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums", lineHeight: 1.3 }}>
                  {pct}%
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {m.passed} of {m.total} passed
                </Typography>
                <Box sx={{ mt: 1, height: 4, borderRadius: 2, bgcolor: "background.neutral", overflow: "hidden" }}>
                  <Box sx={{ height: "100%", width: `${pct}%`, bgcolor: interpolateColorBasedOnScore(pct / 100, 1) }} />
                </Box>
              </Box>
            );
          })}
        </Box>
      </SectionCard>
    </Stack>
  );
}

RunAnalytics.propTypes = { tasks: PropTypes.array, evals: PropTypes.array };
