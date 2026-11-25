import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { behaviourDiff } from "../_mock/compareView";
import { DomainChip, PersonaBadge } from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";
import { turnsOf } from "../_mock/runStream";
import { OutcomeLine } from "./CompareOutcome";
import { csatOf, formatLatency, latencyOf } from "./traceTableHelpers";
import CompareLetter from "./CompareLetter";
import { runLabelOf } from "../_mock/scoreDelta";
import { phraseOf } from "../_mock/compareCells";
import ValueWithDelta from "./ValueWithDelta";

/*
  Compare, scenario by scenario: one block per scenario, one line per run.
  Every number after the baseline's carries its change against it — the
  arrow says which way the number moved, the colour whether that's better.
*/

export default function CompareTableView({
  groups,
  view,
  rowPad,
  runs = [],
  detail = false,
  onOpen,
  evals,
  bandAction,
}) {
  /* System-metric strip on the right of the row — same set the traces
     table shows: CSAT, Turns, Latency, Duration, Tokens, Cost. Widens
     with the number of active columns so nothing crushes. */
  /* Each column is as wide as its number and change need, no wider, so
     "What each run did with it" keeps the room it reads in. */
  const metricBits = [
    view.columns.csat && { key: "csat", label: "CSAT", w: 52 },
    view.columns.turns && { key: "turns", label: "Turns", w: 52 },
    view.columns.latency && { key: "latency", label: "Latency", w: 100 },
    view.columns.duration && { key: "duration", label: "Duration", w: 88 },
    view.columns.tokens && { key: "tokens", label: "Tokens", w: 104 },
    view.columns.cost && { key: "cost", label: "Model cost", w: 96 },
  ].filter(Boolean);
  const metricTemplate = metricBits.map((b) => `${b.w}px`).join(" ");
  const metricColWidth = metricBits.length
    ? metricBits.reduce((a, b) => a + b.w, 0) + (metricBits.length - 1) * 10
    : 0;
  const showScorers = view.columns.scorers && (evals?.length || 0) > 0;
  const EVAL_W = 100;
  const scorersColWidth = showScorers
    ? evals.length * EVAL_W + (evals.length - 1) * 10
    : 0;
  /* System metrics before evals — matches the single-run traces table,
     where evals live at the far right of the row. */
  /* No checkbox column — this screen is for reading a comparison, not for
     picking scenarios. Bulk actions live on the runs list. */
  /* The scenario name is the one flexible column, like a list's name
     column; Result and the numbers keep their own width and sit together. */
  const gridTemplate =
    `${detail ? "28px" : "minmax(240px, 1fr)"} 260px ${metricColWidth ? `${metricColWidth}px ` : ""}${scorersColWidth ? `${scorersColWidth}px` : ""}`.trim();

  return (
    <>
      <Box
        sx={{
          display: detail ? "none" : { xs: "none", lg: "grid" },
          gridTemplateColumns: gridTemplate,
          columnGap: 2,
          px: 2.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          /* Column names stay in view while the rows scroll. */
          position: "sticky",
          /* The page's scroll area has 16px top padding; sticking at 0 left
             a strip where rows showed through above the header. */
          top: -16,
          zIndex: 3,
          bgcolor: "background.paper",
        }}
      >
        <ColHead>Scenario</ColHead>
        <ColHead>
          {view.diff ? "What changed against the baseline" : "Result"}
        </ColHead>
        {metricBits.length > 0 && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: metricTemplate,
              columnGap: 1.25,
            }}
          >
            {metricBits.map((b) => (
              <ColHead key={b.key} sx={{ textAlign: "right" }}>
                {b.label}
              </ColHead>
            ))}
          </Box>
        )}
        {showScorers && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: `repeat(${evals.length}, ${EVAL_W}px)`,
              columnGap: 1.25,
            }}
          >
            {evals.map((e) => (
              <ColHead key={e.id} sx={{ textAlign: "right" }}>
                {e.name}
              </ColHead>
            ))}
          </Box>
        )}
      </Box>

      {groups.map((group) => (
        <Box key={group.id}>
          {group.label && !detail && (
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
              {/* What the band means is on hover; the rows say the rest. */}
              <Tooltip arrow title={bandHover(group, runs)}>
                <Typography
                  noWrap
                  sx={{
                    fontSize: 13,
                    fontWeight: 600,
                    minWidth: 0,
                    flexShrink: group.note ? 0 : 1,
                    color:
                      group.tone === "bad"
                        ? "status.danger.fg"
                        : "text.secondary",
                  }}
                >
                  {group.label}
                </Typography>
              </Tooltip>
              {group.mark === "critical" && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  critical
                </Typography>
              )}
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                {group.rows.length}
              </Typography>

              {!group.rates && bandAction?.(group) && (
                <>
                  <Box flex={1} />
                  {bandAction(group)}
                </>
              )}
              {group.rates && (
                <>
                  <Box flex={1} />
                  {group.rates.map((r) => (
                    <Tooltip
                      key={r.runId}
                      arrow
                      title={
                        r.n
                          ? `Passed in run ${r.letter} on ${r.n} shared scenario${r.n === 1 ? "" : "s"} that check it`
                          : "No shared scenario measured in every run"
                      }
                    >
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.5}
                        sx={{ flexShrink: 0 }}
                      >
                        <CompareLetter letter={r.letter} color={r.color} />
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            minWidth: 30,
                          }}
                        >
                          {r.rate == null ? "—" : `${r.rate}%`}
                        </Typography>
                      </Stack>
                    </Tooltip>
                  ))}
                </>
              )}
            </Stack>
          )}

          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {group.rows.map((row) => (
              <Box
                key={row.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", lg: gridTemplate },
                  columnGap: 2,
                  px: 2.5,
                  py: rowPad,
                  cursor: "pointer",
                  "&:hover": {
                    bgcolor: "action.hover",
                    "& .row-open": { opacity: 1 },
                  },
                  ...focusRing,
                }}
                {...clickable(() => onOpen(row))}
              >
                <Box minWidth={0} sx={detail ? { visibility: "hidden" } : null}>
                  {/* Title on its own line, full width; the pair and what
                      moved sit quietly under it (no chip beside the title). */}
                  {/* Wraps to two lines; anything longer is on hover. */}
                  <Typography
                    title={row.title}
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {row.title}
                  </Typography>
                  <OutcomeLine
                    pair={row.pair}
                    cells={row.cells}
                    extra={
                      view.rowHeight !== "large" &&
                      typeof row.persona?.name === "string"
                        ? row.persona.name
                        : null
                    }
                  />
                  {view.rowHeight === "large" && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mt: 0.25,
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {row.task}
                    </Typography>
                  )}
                  {view.rowHeight === "large" && (
                    <Box sx={{ mt: 0.75 }}>
                      <PersonaBadge persona={row.persona} compact />
                    </Box>
                  )}
                </Box>

                <Stack spacing={0.75} sx={{ minWidth: 0 }}>
                  {row.cells.map((c, i) => (
                    <Stack
                      key={c.runId}
                      direction="row"
                      spacing={1.25}
                      alignItems="center"
                      sx={{ height: 20 }}
                    >
                      <CompareLetter letter={c.letter} color={c.color} />
                      {/* Who the failure belongs to, on the row that reports
                          it — an unmeasured row must never read as the agent's. */}
                      {c.domain && !c.domain.measured && (
                        <DomainChip domain={c.domain} dense />
                      )}
                      {/* One phrase per run: the verdict, or for a failure its
                          cause in red (it says "failed" by itself), then how
                          many trials passed. The full reason is on hover. */}
                      <Typography
                        noWrap
                        title={c.deciding.text}
                        sx={{ flex: 1, minWidth: 0, typography: "s3" }}
                      >
                        <Box
                          component="span"
                          sx={{ fontWeight: 600, color: phraseOf(c).color }}
                        >
                          {phraseOf(c).text}
                        </Box>
                        {c.repeats > 1 && c.passes != null && c.measured && (
                          <Box
                            component="span"
                            sx={{ color: "text.disabled", ml: 0.75 }}
                          >
                            {`${c.passes}/${c.repeats}`}
                          </Box>
                        )}
                        {view.diff && i > 0 ? (
                          <Box
                            component="span"
                            sx={{ color: "text.secondary", ml: 1 }}
                          >
                            <DiffText
                              diff={behaviourDiff(row.cells[0].task, c.task)}
                            />
                          </Box>
                        ) : (
                          phraseOf(c).note && (
                            <Box
                              component="span"
                              sx={{ color: "text.secondary", ml: 1 }}
                            >
                              {phraseOf(c).note}
                            </Box>
                          )
                        )}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>

                {/* System metrics column — sits before evals, matching the
                    header order and the single-run traces table. */}
                <Stack
                  spacing={0.75}
                  sx={{
                    display: { xs: "none", lg: "flex" },
                    position: "relative",
                  }}
                >
                  {/* The row opens a side-by-side of the transcripts; without a
                      mark, nothing on it says that. */}
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={16}
                    className="row-open"
                    sx={{
                      position: "absolute",
                      right: -14,
                      top: 2,
                      opacity: 0,
                      transition: "opacity .12s",
                      color: "text.tertiary",
                      pointerEvents: "none",
                    }}
                  />
                  {(() => {
                    /* value(c) is the number; text(v) how it reads. */
                    const columns = [
                      view.columns.csat && {
                        key: "csat",
                        value: (c) => csatOf(c.task),
                        text: (v) => String(v),
                        delta: (d) => `${Math.round(Math.abs(d) * 10) / 10}`,
                      },
                      view.columns.turns && {
                        key: "turns",
                        value: (c) =>
                          c.task ? turnsOf(c.task.steps || []) : null,
                        text: (v) => String(v),
                        delta: (d) => String(Math.abs(d)),
                        lowerIsBetter: true,
                      },
                      view.columns.latency && {
                        key: "latency",
                        value: (c) => (c.task ? latencyOf(c.task) : null),
                        text: (v) => formatLatency(v),
                        delta: (d) => formatLatency(Math.abs(d)),
                        lowerIsBetter: true,
                      },
                      view.columns.duration && {
                        key: "duration",
                        value: (c) => (c.task ? c.durationMs : null),
                        text: (v) => `${(v / 1000).toFixed(1)}s`,
                        delta: (d) => `${(Math.abs(d) / 1000).toFixed(1)}s`,
                        lowerIsBetter: true,
                      },
                      view.columns.tokens && {
                        key: "tokens",
                        value: (c) => (c.task ? c.tokens : null),
                        text: (v) => v.toLocaleString(),
                        delta: (d) => Math.abs(d).toLocaleString(),
                        lowerIsBetter: true,
                      },
                      view.columns.cost && {
                        key: "cost",
                        value: (c) =>
                          !c.task || c.costUnverified ? null : c.cost || 0,
                        text: (v) => `≈ $${v.toFixed(2)}`,
                        delta: (d) => `$${Math.abs(d).toFixed(2)}`,
                        lowerIsBetter: true,
                      },
                    ].filter(Boolean);
                    const base = row.cells[0];
                    return row.cells.map((c, i) => (
                      <Box
                        key={c.runId}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: metricTemplate,
                          columnGap: 1.25,
                          /* Same height as the run's Result line, so numbers sit beside it. */
                          alignItems: "center",
                          height: 20,
                        }}
                      >
                        {columns.map((col) => {
                          const v = col.value(c);
                          const was = i > 0 ? col.value(base) : null;
                          return (
                            <ValueWithDelta
                              key={col.key}
                              text={v == null ? "—" : col.text(v)}
                              diff={v != null && was != null ? v - was : null}
                              diffText={col.delta}
                              lowerIsBetter={!!col.lowerIsBetter}
                            />
                          );
                        })}
                      </Box>
                    ));
                  })()}
                </Stack>

                {/* Eval scores column — sits after the system metrics so
                    the run's numbers cluster together and evals close the
                    row on the right, mirroring the traces table. */}
                {showScorers && (
                  <Stack
                    spacing={0.75}
                    sx={{ display: { xs: "none", lg: "flex" } }}
                  >
                    {row.cells.map((c, i) => (
                      <Box
                        key={c.runId}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: `repeat(${evals.length}, ${EVAL_W}px)`,
                          columnGap: 1.25,
                          /* Same height as the run's Result line, so numbers sit beside it. */
                          alignItems: "center",
                          height: 20,
                        }}
                      >
                        {evals.map((e) => {
                          const r = (c.task?.evalResults || []).find(
                            (x) => x.id === e.id,
                          );
                          if (!r) {
                            return (
                              <Typography
                                key={e.id}
                                sx={{
                                  textAlign: "right",
                                  typography: "s2",
                                  color: "text.disabled",
                                  fontVariantNumeric: "tabular-nums",
                                }}
                              >
                                —
                              </Typography>
                            );
                          }
                          const pct = Math.round(r.score * 100);
                          const b =
                            i > 0
                              ? (row.cells[0].task?.evalResults || []).find(
                                  (x) => x.id === e.id,
                                )
                              : null;
                          return (
                            <ValueWithDelta
                              key={e.id}
                              text={`${pct}%`}
                              strong
                              tone={r.passed ? null : "status.danger.fg"}
                              diff={b ? pct - Math.round(b.score * 100) : null}
                              diffText={(d) => String(Math.abs(d))}
                            />
                          );
                        })}
                      </Box>
                    ))}
                  </Stack>
                )}
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </>
  );
}
CompareTableView.propTypes = {
  groups: PropTypes.array,
  view: PropTypes.object,
  rowPad: PropTypes.number,
  evals: PropTypes.array,
  onOpen: PropTypes.func,
  bandAction: PropTypes.func,
  runs: PropTypes.array,
  detail: PropTypes.bool,
};

/* A regression band's hover says which run regressed: "Run 2 · 1". */
const BAND_STATUS = {
  "band-regressions": "failed",
  "band-became-flaky": "flaky",
};
function bandHover(group, runs) {
  const want = BAND_STATUS[group.id];
  if (!want) return group.note || "";
  const counts = runs
    .slice(1)
    .map((run) => ({
      run,
      n: group.rows.filter(
        (row) => row.cells.find((c) => c.runId === run.id)?.status === want,
      ).length,
    }))
    .filter((x) => x.n > 0);
  return (
    <Box>
      {group.note}
      {counts.map(({ run, n }) => (
        <Box key={run.id}>{`${runLabelOf(run)} · ${n}`}</Box>
      ))}
    </Box>
  );
}

/** The behaviour diff, in one line. */
function DiffText({ diff }) {
  if (!diff) return null;
  if (diff.identical) {
    return (
      <Box component="span" sx={{ color: "text.tertiary" }}>
        Same behaviour as the baseline.
      </Box>
    );
  }
  const parts = [];
  if (diff.missedNow.length)
    parts.push(`stopped calling ${diff.missedNow.join(", ")}`);
  else if (diff.dropped.length)
    parts.push(`dropped ${diff.dropped.join(", ")}`);
  if (diff.added.length) parts.push(`added ${diff.added.join(", ")}`);
  if (diff.turnDelta)
    parts.push(
      `${Math.abs(diff.turnDelta)} ${diff.turnDelta > 0 ? "more" : "fewer"} turns`,
    );
  if (!parts.length && diff.firstDivergence >= 0) {
    parts.push(
      `same tools, wording differs from turn ${diff.firstDivergence + 1}`,
    );
  }
  return (
    <>
      {diff.verdictChanged && (
        <Box
          component="span"
          sx={{ color: "status.danger.fg", fontWeight: 600 }}
        >
          Verdict changed —{" "}
        </Box>
      )}
      {parts.join(", ")}.
    </>
  );
}
DiffText.propTypes = { diff: PropTypes.object };

function ColHead({ children, sx }) {
  return (
    <Typography
      noWrap
      sx={{
        /* Same as the run table's header cells. */
        typography: "s2",
        fontWeight: 500,
        color: "text.secondary",
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node, sx: PropTypes.object };
