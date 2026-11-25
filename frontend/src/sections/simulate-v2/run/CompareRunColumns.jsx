import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { runLabelOf } from "../_mock/scoreDelta";
import {
  evalPctOf,
  metricDefsOf,
  movementOf,
  phraseOf,
  trialCellOf,
  trialCountAcross,
  trialCountOf,
} from "../_mock/compareCells";
import CompareLetter from "./CompareLetter";
import ValueWithDelta from "./ValueWithDelta";

/*
  Compare, one row per scenario: each run is a column with its result, and
  the numbers show one run against the baseline (picked in the header).
  A row's chevron opens every run's numbers; clicking the row opens the
  calls side by side.
*/
const RUN_W = 132;
/* A cell that wasn't scored has no scores to show or to compare against. */
const scored = (c) => !!c && ["passed", "flaky", "failed"].includes(c.status);
const EVAL_W = 88;
const GAP = 12;

/* Decimals each number keeps once averaged: CSAT and turns one, the rest
   whole numbers (cost keeps cents). */
const DECIMALS = { csat: 1, turns: 1, cost: 2 };
const roundTo = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

/* The cell's trials as cells: its own trials when it ran several, else
   just the cell. */
const trialsOf = (cell) => {
  const n = cell?.task?.samples?.length || 0;
  if (!scored(cell) || n < 2) return [cell];
  return Array.from({ length: n }, (_, k) => trialCellOf(cell, k));
};
/* A metric on the main row: the average across the run's trials. */
const avgMetric = (m, cell) => {
  const vs = trialsOf(cell)
    .map((t) => m.value(t))
    .filter((v) => v != null);
  if (!vs.length) return null;
  return vs.length === 1 && trialsOf(cell).length === 1
    ? vs[0]
    : roundTo(mean(vs), DECIMALS[m.key] ?? 0);
};
const avgEval = (cell, id) => {
  const rs = trialsOf(cell)
    .map((t) => evalPctOf(t, id))
    .filter(Boolean);
  if (!rs.length) return null;
  if (trialsOf(cell).length === 1) return rs[0];
  const pct = Math.round(mean(rs.map((r) => r.pct)));
  return { pct, passed: rs.every((r) => r.passed) };
};

export default function CompareRunColumns({
  groups,
  view,
  rowPad,
  evals = [],
  runs = [],
  onOpen,
  bandAction,
  compareId = null,
  allRows = null,
}) {
  const base = runs[0];
  const others = runs.slice(1);
  const compared = runs.find((r) => r.id === compareId) || others[0] || null;
  const [open, setOpen] = useState(() => new Set());
  const [only, setOnly] = useState(null);

  /* Turns says nothing when every value is the same: leave the column out. */
  const turnsSame = useMemo(() => {
    const vals = new Set();
    const every = allRows || groups.flatMap((g) => g.rows);
    const turns = metricDefsOf({ columns: { turns: true } })[0];
    every.forEach((row) =>
      row.cells.forEach((c) =>
        trialsOf(c).forEach((t) => {
          const v = scored(t) ? turns.value(t) : null;
          if (v != null) vals.add(v);
        }),
      ),
    );
    return vals.size <= 1;
  }, [allRows, groups]);
  const metrics = metricDefsOf(view).filter(
    (m) => !(m.key === "turns" && turnsSame),
  );
  const showEvals = view.columns.scorers && evals.length > 0;
  const template = [
    "24px",
    "minmax(180px, 1fr)",
    ...runs.map(() => `${RUN_W}px`),
    ...metrics.map((m) => `${m.w}px`),
    ...(showEvals ? evals.map(() => `${EVAL_W}px`) : []),
  ].join(" ");

  /* Per run: how many scenarios did better or worse than the baseline. */
  /* Over every scenario, not just the filtered rows: a filter changes
     which rows show, never the totals. */
  const counts = useMemo(() => {
    const out = {};
    const every = allRows || groups.flatMap((g) => g.rows);
    [{ rows: every }].forEach((g) =>
      g.rows.forEach((row) =>
        row.cells.forEach((c) => {
          const m = movementOf(
            c,
            row.cells.find((x) => x.runId === base?.id),
          );
          if (!m) return;
          out[c.runId] = out[c.runId] || { improved: 0, regressed: 0 };
          out[c.runId][m] += 1;
        }),
      ),
    );
    return out;
  }, [allRows, groups, base?.id]);

  const cellOf = (row, run) =>
    run ? row.cells.find((c) => c.runId === run.id) : null;
  /* "Regressions first": within each group, the compared run's regressed
     scenarios lead, then its improved ones. */
  const RANK = { regressed: 0, improved: 1 };
  const rankOf = (row) =>
    RANK[
      movementOf(compared ? cellOf(row, compared) : null, cellOf(row, base))
    ] ?? 2;
  const sorted =
    view.sort === "movement"
      ? groups.map((g) => ({
          ...g,
          rows: [...g.rows].sort((a, b) => rankOf(a) - rankOf(b)),
        }))
      : groups;
  const shown = only
    ? sorted
        .map((g) => ({
          ...g,
          rows: g.rows.filter(
            (row) =>
              movementOf(
                row.cells.find((c) => c.runId === only.runId),
                row.cells.find((c) => c.runId === base?.id),
              ) === only.kind,
          ),
        }))
        .filter((g) => g.rows.length)
    : sorted;
  const toggle = (id) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rowGrid = {
    display: "grid",
    gridTemplateColumns: template,
    columnGap: `${GAP}px`,
    alignItems: "center",
    px: 2.5,
  };

  return (
    <>
      {/* Header, one line: run names with their ▲/▼ counts, then the
          number columns. Fixed while the rows scroll. */}
      <Box
        sx={{
          ...rowGrid,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          position: "sticky",
          /* The page's scroll area has 16px top padding; sticking at 0 left
             a strip where rows showed through above the header. */
          top: -16,
          zIndex: 3,
          bgcolor: "background.paper",
          display: { xs: "none", lg: "grid" },
        }}
      >
        <Box />
        <Head>Scenario</Head>
        {runs.map((r, i) => {
          const n = counts[r.id] || { improved: 0, regressed: 0 };
          return (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <CompareLetter letter={r.letter} color={r.color} />
              <Tooltip arrow title={i === 0 ? "Baseline" : ""}>
                <Head noWrap title={runLabelOf(r)}>
                  {/* An epoch's candidate is named in full on hover; the
                      column keeps the part that tells it apart. */}
                  {r.kind === "trial" && r.trialN
                    ? `Candidate ${r.trialN}`
                    : runLabelOf(r)}
                </Head>
              </Tooltip>
              {i > 0 &&
                [
                  ["improved", "▲", "status.success.fg"],
                  ["regressed", "▼", "status.danger.fg"],
                ]
                  .filter(([kind]) => n[kind] > 0)
                  .map(([kind, mark, color]) => {
                    const on = only?.runId === r.id && only?.kind === kind;
                    return (
                      <Tooltip
                        key={kind}
                        arrow
                        title={`${n[kind]} ${kind} vs ${runLabelOf(base)} · show only these`}
                      >
                        <ButtonBase
                          onClick={() =>
                            setOnly(on ? null : { runId: r.id, kind })
                          }
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            px: 0.25,
                            borderRadius: "4px",
                            color,
                            bgcolor: on ? "action.selected" : "transparent",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {`${mark}${n[kind]}`}
                        </ButtonBase>
                      </Tooltip>
                    );
                  })}
            </Stack>
          );
        })}
        {metrics.map((m) => (
          <Head key={m.key} sx={{ textAlign: "right" }}>
            {m.label}
          </Head>
        ))}
        {showEvals &&
          evals.map((e) => (
            <Head key={e.id} noWrap title={e.name} sx={{ textAlign: "right" }}>
              {e.name}
            </Head>
          ))}
      </Box>

      {only && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`Only ${runLabelOf(runs.find((r) => r.id === only.runId))} ${only.kind}`}
          </Typography>
          <ButtonBase
            onClick={() => setOnly(null)}
            sx={{ typography: "s3", color: "primary.main", fontWeight: 600 }}
          >
            Clear
          </ButtonBase>
        </Stack>
      )}

      {shown.map((group) => (
        <Box key={group.id}>
          {group.label && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2.5,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Tooltip arrow title={group.note || ""}>
                <Typography
                  noWrap
                  sx={{
                    fontSize: 13,
                    fontWeight: 600,
                    color:
                      group.tone === "bad"
                        ? "status.danger.fg"
                        : "text.secondary",
                  }}
                >
                  {group.label}
                </Typography>
              </Tooltip>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {group.rows.length}
              </Typography>
              {bandAction?.(group) && (
                <>
                  <Box flex={1} />
                  {bandAction(group)}
                </>
              )}
            </Stack>
          )}

          {group.rows.map((row) => {
            const isOpen = open.has(row.id);
            const baseCell = cellOf(row, base);
            const cmpCell = cellOf(row, compared);
            const nTrials = trialCountAcross(runs.map((r) => cellOf(row, r)));
            return (
              <Box
                key={row.id}
                sx={{ borderBottom: "1px solid", borderColor: "divider" }}
              >
                <Box
                  sx={{
                    ...rowGrid,
                    py: rowPad,
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                    ...focusRing,
                  }}
                  {...clickable(() => onOpen(row))}
                >
                  {nTrials > 1 ? (
                    <Tooltip
                      arrow
                      title={isOpen ? "Hide trials" : "Show each trial"}
                    >
                      <ButtonBase
                        onClick={(e) => {
                          e.stopPropagation();
                          toggle(row.id);
                        }}
                        sx={{ borderRadius: "4px", width: 20, height: 20 }}
                      >
                        <Iconify
                          icon="solar:alt-arrow-right-linear"
                          width={14}
                          sx={{
                            color: "text.tertiary",
                            transform: isOpen ? "rotate(90deg)" : "none",
                            transition: "transform .12s",
                          }}
                        />
                      </ButtonBase>
                    </Tooltip>
                  ) : (
                    <Box />
                  )}

                  <Box minWidth={0}>
                    <Typography
                      noWrap
                      title={row.title}
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {row.title}
                    </Typography>
                    {typeof row.persona?.name === "string" && (
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {row.persona.name}
                      </Typography>
                    )}
                  </Box>

                  {runs.map((r) => {
                    const c = cellOf(row, r);
                    const ph = phraseOf(c);
                    const count = trialCountOf(c);
                    return (
                      <Typography
                        key={r.id}
                        noWrap
                        title={c?.deciding?.text || ""}
                        sx={{ typography: "s3", minWidth: 0 }}
                      >
                        <Box
                          component="span"
                          sx={{ fontWeight: 600, color: ph.color }}
                        >
                          {ph.text}
                        </Box>
                        {count && (
                          <Box
                            component="span"
                            sx={{ color: "text.disabled", ml: 0.75 }}
                          >
                            {count}
                          </Box>
                        )}
                      </Typography>
                    );
                  })}

                  {metrics.map((m) => {
                    const v = avgMetric(m, cmpCell);
                    const was = scored(baseCell)
                      ? avgMetric(m, baseCell)
                      : null;
                    return (
                      <ValueWithDelta
                        key={m.key}
                        text={v == null ? "—" : m.text(v)}
                        diff={v != null && was != null ? v - was : null}
                        diffText={m.delta}
                        against={runLabelOf(base)}
                        wasText={was != null ? m.text(was) : null}
                        lowerIsBetter={!!m.lowerIsBetter}
                      />
                    );
                  })}
                  {showEvals &&
                    evals.map((e) => {
                      const now = scored(cmpCell)
                        ? avgEval(cmpCell, e.id)
                        : null;
                      const was = scored(baseCell)
                        ? avgEval(baseCell, e.id)
                        : null;
                      return (
                        <ValueWithDelta
                          key={e.id}
                          text={now ? `${now.pct}%` : "—"}
                          strong={!!now}
                          tone={
                            !now
                              ? "text.disabled"
                              : now.passed
                                ? null
                                : "status.danger.fg"
                          }
                          diff={now && was ? now.pct - was.pct : null}
                          against={runLabelOf(base)}
                          wasText={was ? `${was.pct}%` : null}
                        />
                      );
                    })}
                </Box>

                {isOpen && nTrials > 1 && (
                  /* The row's trials, one line each: every run's result for
                     that trial in its own column, and the picked run's
                     numbers for it against the baseline's same trial. */
                  <Box sx={{ bgcolor: "background.neutral", py: 0.5 }}>
                    {Array.from({ length: nTrials }, (_, k) => {
                      const tBase = trialCellOf(baseCell, k);
                      const tCmp = trialCellOf(cmpCell, k);
                      return (
                        <Box key={k} sx={{ ...rowGrid, py: 0.5 }}>
                          <Box />
                          <Typography
                            sx={{ typography: "s3", color: "text.secondary" }}
                          >
                            {`Trial ${k + 1}`}
                          </Typography>
                          {runs.map((r) => {
                            const tc = trialCellOf(cellOf(row, r), k);
                            const ph = phraseOf(tc);
                            return (
                              <Typography
                                key={r.id}
                                noWrap
                                title={tc?.deciding?.text || ""}
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: ph.color,
                                  minWidth: 0,
                                }}
                              >
                                {ph.text}
                              </Typography>
                            );
                          })}
                          {metrics.map((m) => {
                            const v = m.value(tCmp);
                            const was = scored(tBase) ? m.value(tBase) : null;
                            return (
                              <ValueWithDelta
                                key={m.key}
                                text={v == null ? "—" : m.text(v)}
                                diff={v != null && was != null ? v - was : null}
                                diffText={m.delta}
                                against={runLabelOf(base)}
                                wasText={was != null ? m.text(was) : null}
                                lowerIsBetter={!!m.lowerIsBetter}
                              />
                            );
                          })}
                          {showEvals &&
                            evals.map((e) => {
                              const now = scored(tCmp)
                                ? evalPctOf(tCmp, e.id)
                                : null;
                              const was = scored(tBase)
                                ? evalPctOf(tBase, e.id)
                                : null;
                              return (
                                <ValueWithDelta
                                  key={e.id}
                                  text={now ? `${now.pct}%` : "—"}
                                  strong={!!now}
                                  tone={
                                    !now
                                      ? "text.disabled"
                                      : now.passed
                                        ? null
                                        : "status.danger.fg"
                                  }
                                  diff={now && was ? now.pct - was.pct : null}
                                  against={runLabelOf(base)}
                                  wasText={was ? `${was.pct}%` : null}
                                />
                              );
                            })}
                        </Box>
                      );
                    })}
                  </Box>
                )}
              </Box>
            );
          })}
        </Box>
      ))}
    </>
  );
}
CompareRunColumns.propTypes = {
  groups: PropTypes.array,
  view: PropTypes.object,
  rowPad: PropTypes.number,
  evals: PropTypes.array,
  runs: PropTypes.array,
  onOpen: PropTypes.func,
  bandAction: PropTypes.func,
  compareId: PropTypes.string,
  allRows: PropTypes.array,
};

function Head({ children, sx, noWrap, title }) {
  return (
    <Typography
      noWrap={noWrap}
      title={title}
      /* Same as the run table's header cells. */
      sx={{ typography: "s2", fontWeight: 500, color: "text.secondary", ...sx }}
    >
      {children}
    </Typography>
  );
}
Head.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
  noWrap: PropTypes.bool,
  title: PropTypes.string,
};
