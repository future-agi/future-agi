import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { behaviourDiff } from "../_mock/compareView";
import { runIdentityOf } from "../_mock/runIdentity";
import { creditsLabel } from "../_mock/metering";
import { twinTimelineFor, twinById } from "../_mock/twins";
import { Verdict } from "../components/primitives";
import { textTone } from "../components/textTone";
import { csatOf } from "./traceTableHelpers";
import OutcomeChip from "./CompareOutcome";
import { clickable, focusRing } from "../components/clickable";

/* Same derivations the TraceTable and CompareRuns TableView use, so a
   number read here matches the number read on the single-run screen. */
const hashStr = (s) => {
  let h = 0;
  for (let i = 0; i < String(s || "").length; i += 1)
    h = (h * 31 + String(s).charCodeAt(i)) >>> 0;
  return h;
};
const turnsOf = (t) => (t ? t.steps?.length ?? 0 : null);
const latencyOf = (t) =>
  t ? t.latencyMs ?? 280 + (hashStr(t.id) % 320) : null;

/**
 * A column per agent version.
 *
 * The table view answers "what moved" — it is a list of scenarios with the
 * runs folded inside. This answers the other question: given this scenario,
 * how did each version handle it, and what did each one cost. Reading down a
 * column is reading one agent; reading across is reading one scenario.
 *
 * Every column carries the same things in the same order — outcome, cost,
 * graders — because the whole value of the layout is that your eye can compare
 * position to position without re-reading the labels.
 */
export default function CompareGrid({
  comparison,
  groups,
  evals,
  env,
  envState,
  view,
  onOpen,
  bandAction,
}) {
  const { runs, baseline } = comparison;
  const twinBacked = !!envState?.twinBacking;

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 260 + runs.length * 300 }}>
        {/* who the columns are */}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
            borderBottom: "1px solid",
            borderColor: "divider",
            position: "sticky",
            top: 0,
            zIndex: 2,
            bgcolor: "background.paper",
          }}
        >
          <Box sx={{ px: 2.5, py: 1.5 }}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                textTransform: "none",
                letterSpacing: 0.4,
              }}
            >
              Scenario
            </Typography>
          </Box>
          {runs.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 1.5,
                borderLeft: "1px solid",
                borderColor: "divider",
              }}
            >
              <Letter letter={r.letter} color={r.color} />
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
              >
                {r.kind === "trial"
                  ? `Run ${r.trialN} · ${r.selfImprovementName ? `${r.selfImprovementName} · ` : ""}`
                  : ""}
                {runIdentityOf(r, envState, env).label}
              </Typography>
              {r.id === baseline.id && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  baseline
                </Typography>
              )}
              <Box flex={1} />
              <Typography
                sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}
              >
                {r.passRate}%
              </Typography>
            </Stack>
          ))}
        </Box>

        {groups.map((group) => (
          <Box key={group.id}>
            {group.label && (
              <GroupBand
                group={group}
                columns={runs.length}
                action={bandAction?.(group)}
              />
            )}

            {group.rows.map((row) => (
              <Box key={row.id}>
                {/* the scenario, spanning the whole width — it is the question
                    every column below is answering */}
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  {...clickable(() => onOpen(row))}
                  sx={{
                    px: 2.5,
                    py: 1.25,
                    cursor: "pointer",
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                    ...focusRing,
                  }}
                >
                  <Iconify
                    icon="solar:layers-minimalistic-linear"
                    width={14}
                    sx={{ color: "text.tertiary", flexShrink: 0 }}
                  />
                  <OutcomeChip pair={row.pair} />
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                    {row.title}
                  </Typography>
                  {row.critical && (
                    <Iconify
                      icon="solar:danger-triangle-bold"
                      width={14}
                      sx={{ color: "status.danger.fg", flexShrink: 0 }}
                    />
                  )}
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {row.task}
                  </Typography>
                </Stack>

                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box sx={{ px: 2.5, py: 2 }}>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {row.persona?.name}
                      {row.persona?.age ? ` · ${row.persona.age}` : ""}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.disabled" }}
                    >
                      {(row.persona?.traits || []).join(", ")}
                    </Typography>
                  </Box>

                  {row.cells.map((cell) => (
                    <Cell
                      key={cell.runId}
                      cell={cell}
                      baselineCell={row.cells[0]}
                      evals={evals}
                      envState={envState}
                      twinBacked={twinBacked}
                      view={view}
                      onOpen={() => onOpen(row)}
                    />
                  ))}
                </Box>
              </Box>
            ))}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

CompareGrid.propTypes = {
  comparison: PropTypes.object,
  groups: PropTypes.array,
  evals: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  view: PropTypes.object,
  onOpen: PropTypes.func,
  bandAction: PropTypes.func,
};

function Cell({
  cell,
  baselineCell,
  evals,
  envState,
  twinBacked,
  view,
  onOpen,
}) {
  const isBaseline = cell.runId === baselineCell.runId;
  const diff =
    view.diff && !isBaseline
      ? behaviourDiff(baselineCell.task, cell.task)
      : null;

  /*
    Twin write stat — only when the env is twin-backed. Auto-shown, not
    gated on a column toggle: the sole existence of a twin backing on
    the env is what makes this stat meaningful, so there's no reason
    to hide it behind a settings knob users would have to find.
    Baseline gets its raw count; every other column shows a delta vs.
    the baseline (writes are a proxy for "how differently did this
    agent version treat the sandbox").
  */
  const twinWrites =
    twinBacked && cell.task ? totalTwinWrites(envState, cell.task) : null;
  const twinWritesBaseline =
    twinBacked && baselineCell.task
      ? totalTwinWrites(envState, baselineCell.task)
      : null;
  const twinDelta =
    twinWrites != null && twinWritesBaseline != null && !isBaseline
      ? twinWrites - twinWritesBaseline
      : null;
  const twinBreakdown =
    twinBacked && cell.task ? writesByService(envState, cell.task) : null;

  return (
    <Box
      {...clickable(onOpen)}
      sx={{
        px: 2,
        py: 2,
        borderLeft: "1px solid",
        borderColor: "divider",
        cursor: "pointer",
        "&:hover": { bgcolor: "action.hover" },
        ...focusRing,
      }}
    >
      <Box sx={{ mb: 1 }}>
        <Verdict
          status={cell.status}
          passes={cell.passes}
          repeats={cell.repeats}
        />
      </Box>

      {/* what happened, or what changed */}
      <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}>
        {diff ? <DiffLine diff={diff} /> : cell.deciding.text}
      </Typography>

      <Stack
        direction="row"
        spacing={2}
        sx={{ mb: view.columns.scorers || twinBreakdown ? 1.5 : 0 }}
        flexWrap="wrap"
        rowGap={1}
      >
        {view.columns.csat && (
          <Stat
            label="CSAT"
            value={csatOf(cell.task) != null ? String(csatOf(cell.task)) : "—"}
          />
        )}
        {view.columns.turns && (
          <Stat
            label="Turns"
            value={cell.task ? String(turnsOf(cell.task)) : "—"}
          />
        )}
        {view.columns.latency && (
          <Stat
            label="Latency"
            value={cell.task ? `${latencyOf(cell.task)}ms` : "—"}
          />
        )}
        {view.columns.duration && (
          <Stat
            label="Duration"
            value={cell.task ? `${(cell.durationMs / 1000).toFixed(1)}s` : "—"}
            delta={cell.durationDelta}
            lowerIsBetter
          />
        )}
        {view.columns.tokens && (
          <Stat
            label="Tokens"
            value={cell.task ? `${cell.tokens}` : "—"}
            delta={cell.tokensDelta}
            lowerIsBetter
          />
        )}
        {view.columns.cost && (
          <Stat
            label="Cost"
            value={
              !cell.task
                ? "—"
                : cell.costUnverified
                  ? "unverified"
                  : creditsLabel(cell.cost || 0)
            }
          />
        )}
        {twinBacked &&
          twinWrites != null &&
          !/^OPT-\d+-t\d+$/.test(cell.runId || "") && (
            <TwinWritesStat
              writes={twinWrites}
              delta={twinDelta}
              breakdown={twinBreakdown}
            />
          )}
      </Stack>

      {view.columns.scorers && (
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              px: 1.25,
              pt: 0.875,
            }}
          >
            Graders
          </Typography>
          <Stack sx={{ px: 1.25, py: 0.75 }} spacing={0.375}>
            {evals.map((e) => {
              const r = (cell.task?.evalResults || []).find(
                (x) => x.id === e.id,
              );
              if (!r) return null;
              return (
                <Stack
                  key={e.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {e.name}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: r.passed ? "text.primary" : "status.danger.fg",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(r.score * 100)}%
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
Cell.propTypes = {
  cell: PropTypes.object,
  baselineCell: PropTypes.object,
  evals: PropTypes.array,
  envState: PropTypes.object,
  twinBacked: PropTypes.bool,
  view: PropTypes.object,
  onOpen: PropTypes.func,
};

/**
 * Twin-writes stat for one column. Same visual family as Duration /
 * Tokens / Cost above it, but tinted purple to belong with the rest
 * of the twin surface, and with a rich tooltip: per-service breakdown
 * so the reader can see "this agent version wrote 5 Slack messages,
 * the baseline wrote 2" without opening the drawer.
 */
function TwinWritesStat({ writes, delta, breakdown }) {
  const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "";
  return (
    <Tooltip
      arrow
      placement="top"
      title={
        <Stack spacing={0.5} sx={{ py: 0.5, minWidth: 160 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, color: "common.white" }}
          >
            Sandbox writes by service
          </Typography>
          {Object.entries(breakdown || {}).map(([sId, count]) => {
            const t = twinById(sId);
            return (
              <Stack
                key={sId}
                direction="row"
                alignItems="center"
                spacing={0.75}
              >
                <Iconify
                  icon={t?.icon || "solar:server-square-linear"}
                  width={12}
                  sx={{ color: t?.color || "primary.main" }}
                />
                <Typography
                  sx={{ typography: "s3", color: "common.white", flex: 1 }}
                >
                  {t?.name || sId}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "common.white",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      }
    >
      <Box>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            letterSpacing: 0.4,
            textTransform: "none",
          }}
        >
          Clone writes
        </Typography>
        <Stack direction="row" alignItems="baseline" spacing={0.5}>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: 600,
              color: "primary.main",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {writes}
          </Typography>
          {delta != null && delta !== 0 && (
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: delta > 0 ? "status.danger.fg" : "status.success.fg",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {arrow}
              {Math.abs(delta)}
            </Typography>
          )}
        </Stack>
      </Box>
    </Tooltip>
  );
}
TwinWritesStat.propTypes = {
  writes: PropTypes.number,
  delta: PropTypes.number,
  breakdown: PropTypes.object,
};

/**
 * Total sandbox writes for one task's twin timeline. Cached
 * per-call at the Cell level, not memoised globally: the timeline
 * derivation is cheap, and cell-scoped caching stays in sync when
 * runs re-render.
 */
function totalTwinWrites(envState, task) {
  const t = twinTimelineFor(envState, task);
  return Object.values(t.writesByService).reduce((a, b) => a + b, 0);
}
function writesByService(envState, task) {
  const t = twinTimelineFor(envState, task);
  return t.writesByService;
}

/** The diff, as one sentence. The full account is in the drawer. */
function DiffLine({ diff }) {
  if (diff.identical)
    return (
      <Box component="span" sx={{ color: "text.tertiary" }}>
        Same behaviour as the baseline.
      </Box>
    );

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
  if (!parts.length && diff.firstDivergence >= 0)
    parts.push(
      `same tools, wording differs from turn ${diff.firstDivergence + 1}`,
    );

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
DiffLine.propTypes = { diff: PropTypes.object };

function Stat({ label, value, delta, lowerIsBetter }) {
  const good = delta == null ? null : lowerIsBetter ? delta < 0 : delta > 0;
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          textTransform: "none",
          letterSpacing: 0.3,
        }}
      >
        {label}
      </Typography>
      <Stack direction="row" alignItems="baseline" spacing={0.5}>
        <Typography
          sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
        >
          {value}
        </Typography>
        {delta != null && delta !== 0 && (
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: good ? "status.success.fg" : "status.danger.fg",
            }}
          >
            {delta > 0 ? "+" : ""}
            {delta}%
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
Stat.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  delta: PropTypes.number,
  lowerIsBetter: PropTypes.bool,
};

/* A group's band. Grouped by sub-goal, each run's pass rate on it sits in
   that run's column, so the band reads across like the rows under it. */
function GroupBand({ group, columns, action }) {
  const head = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 2.5, py: 1, minWidth: 0 }}
    >
      <Typography
        noWrap
        sx={{
          fontSize: 13,
          fontWeight: 600,
          minWidth: 0,
          flexShrink: group.note ? 0 : 1,
          color: group.tone === "bad" ? "status.danger.fg" : "text.secondary",
        }}
      >
        {group.label}
      </Typography>
      {group.mark === "critical" && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >
          critical
        </Typography>
      )}
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
      >
        {group.rows.length}
      </Typography>
      {group.note && (
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
        >
          · {group.note}
        </Typography>
      )}
      {action && !group.rates && (
        <>
          <Box flex={1} />
          {action}
        </>
      )}
    </Stack>
  );
  if (!group.rates) {
    return (
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
        {head}
      </Box>
    );
  }
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: `220px repeat(${columns}, 1fr)`,
        alignItems: "center",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {head}
      {group.rates.map((r) => (
        <Tooltip
          key={r.runId}
          arrow
          title={
            r.n
              ? `Passed on ${r.n} shared scenario${r.n === 1 ? "" : "s"} that check it`
              : "No shared scenario measured in every run"
          }
        >
          <Typography
            sx={{
              px: 2,
              py: 1,
              borderLeft: "1px solid",
              borderColor: "divider",
              fontSize: 13,
              fontWeight: 600,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {r.rate == null ? "—" : `${r.rate}%`}
          </Typography>
        </Tooltip>
      ))}
    </Box>
  );
}
GroupBand.propTypes = {
  group: PropTypes.object,
  columns: PropTypes.number,
  action: PropTypes.node,
};

function Letter({ letter, color }) {
  return (
    <Box
      sx={{
        width: 20,
        height: 20,
        borderRadius: "4px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: 600,
        color: textTone(color),
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
Letter.propTypes = { letter: PropTypes.string, color: PropTypes.string };
