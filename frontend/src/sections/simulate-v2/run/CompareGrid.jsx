import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { behaviourDiff } from "../_mock/compareView";
import { twinTimelineFor, twinById } from "../_mock/twins";
import { Verdict } from "../components/primitives";

const TWIN_TINT = "#7857FC";

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
export default function CompareGrid({ comparison, groups, evals, envState, view, onOpen }) {
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
            borderBottom: "1px solid", borderColor: "divider",
            position: "sticky", top: 0, zIndex: 2, bgcolor: "background.paper",
          }}
        >
          <Box sx={{ px: 2.5, py: 1.5 }}>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
              Scenario
            </Typography>
          </Box>
          {runs.map((r) => (
            <Stack
              key={r.id}
              direction="row" alignItems="center" spacing={1}
              sx={{ px: 2, py: 1.5, borderLeft: "1px solid", borderColor: "divider" }}
            >
              <Letter letter={r.letter} color={r.color} />
              <Typography noWrap sx={{ typography: "s2", fontWeight: 700, minWidth: 0 }}>
                agent {r.agentVersion}
              </Typography>
              {r.id === baseline.id && (
                <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>baseline</Typography>
              )}
              <Box flex={1} />
              <Typography sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>{r.passRate}%</Typography>
            </Stack>
          ))}
        </Box>

        {groups.map((group) => (
          <Box key={group.id}>
            {group.label && <GroupBand label={group.label} count={group.rows.length} />}

            {group.rows.map((row) => (
              <Box key={row.id}>
                {/* the scenario, spanning the whole width — it is the question
                    every column below is answering */}
                <Stack
                  direction="row" alignItems="center" spacing={1}
                  onClick={() => onOpen(row)}
                  sx={{
                    px: 2.5, py: 1.25, cursor: "pointer",
                    bgcolor: "background.neutral",
                    borderBottom: "1px solid", borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Iconify icon="solar:layers-minimalistic-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{row.title}</Typography>
                  {row.critical && (
                    <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626", flexShrink: 0 }} />
                  )}
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                    {row.task}
                  </Typography>
                </Stack>

                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
                    borderBottom: "1px solid", borderColor: "divider",
                  }}
                >
                  <Box sx={{ px: 2.5, py: 2 }}>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {row.persona?.name}
                      {row.persona?.age ? ` · ${row.persona.age}` : ""}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.disabled" }}>
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
  comparison: PropTypes.object, groups: PropTypes.array,
  evals: PropTypes.array, envState: PropTypes.object,
  view: PropTypes.object, onOpen: PropTypes.func,
};

function Cell({ cell, baselineCell, evals, envState, twinBacked, view, onOpen }) {
  const isBaseline = cell.runId === baselineCell.runId;
  const diff = view.diff && !isBaseline ? behaviourDiff(baselineCell.task, cell.task) : null;

  /*
    Twin write stat — only when the env is twin-backed. Auto-shown, not
    gated on a column toggle: the sole existence of a twin backing on
    the env is what makes this stat meaningful, so there's no reason
    to hide it behind a settings knob users would have to find.
    Baseline gets its raw count; every other column shows a delta vs.
    the baseline (writes are a proxy for "how differently did this
    agent version treat the sandbox").
  */
  const twinWrites = twinBacked && cell.task
    ? totalTwinWrites(envState, cell.task)
    : null;
  const twinWritesBaseline = twinBacked && baselineCell.task
    ? totalTwinWrites(envState, baselineCell.task)
    : null;
  const twinDelta = twinWrites != null && twinWritesBaseline != null && !isBaseline
    ? twinWrites - twinWritesBaseline
    : null;
  const twinBreakdown = twinBacked && cell.task
    ? writesByService(envState, cell.task)
    : null;

  return (
    <Box
      onClick={onOpen}
      sx={{
        px: 2, py: 2, borderLeft: "1px solid", borderColor: "divider",
        cursor: "pointer", "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Box sx={{ mb: 1 }}>
        <Verdict status={cell.status} passes={cell.passes} repeats={cell.repeats} />
      </Box>

      {/* what happened, or what changed */}
      <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}>
        {diff ? <DiffLine diff={diff} /> : cell.deciding.text}
      </Typography>

      <Stack direction="row" spacing={2} sx={{ mb: (view.columns.scorers || twinBreakdown) ? 1.5 : 0 }} flexWrap="wrap" rowGap={1}>
        {view.columns.duration && (
          <Stat label="Duration" value={cell.task ? `${(cell.durationMs / 1000).toFixed(1)}s` : "—"} delta={cell.durationDelta} lowerIsBetter />
        )}
        {view.columns.tokens && (
          <Stat label="Tokens" value={cell.task ? `${cell.tokens}` : "—"} delta={cell.tokensDelta} lowerIsBetter />
        )}
        {view.columns.cost && (
          <Stat label="Cost" value={`$${(cell.cost || 0).toFixed(3)}`} />
        )}
        {twinBacked && twinWrites != null && (
          <TwinWritesStat
            writes={twinWrites}
            delta={twinDelta}
            breakdown={twinBreakdown}
          />
        )}
      </Stack>

      {view.columns.scorers && (
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, px: 1.25, pt: 0.875 }}
          >
            Graders
          </Typography>
          <Stack sx={{ px: 1.25, py: 0.75 }} spacing={0.375}>
            {evals.map((e) => {
              const r = (cell.task?.evalResults || []).find((x) => x.id === e.id);
              if (!r) return null;
              return (
                <Stack key={e.id} direction="row" alignItems="center" spacing={1}>
                  <Typography noWrap sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}>
                    {e.name}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", fontWeight: 700, color: r.passed ? "text.primary" : "#C2603F", fontVariantNumeric: "tabular-nums" }}
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
  cell: PropTypes.object, baselineCell: PropTypes.object,
  evals: PropTypes.array, envState: PropTypes.object,
  twinBacked: PropTypes.bool,
  view: PropTypes.object, onOpen: PropTypes.func,
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
      arrow placement="top"
      title={
        <Stack spacing={0.5} sx={{ py: 0.5, minWidth: 160 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "common.white" }}>
            Sandbox writes by service
          </Typography>
          {Object.entries(breakdown || {}).map(([sId, count]) => {
            const t = twinById(sId);
            return (
              <Stack key={sId} direction="row" alignItems="center" spacing={0.75}>
                <Iconify icon={t?.icon || "solar:server-square-linear"} width={11} sx={{ color: t?.color || TWIN_TINT }} />
                <Typography sx={{ typography: "s3", color: "common.white", flex: 1 }}>{t?.name || sId}</Typography>
                <Typography sx={{ typography: "s3", fontWeight: 700, color: "common.white", fontVariantNumeric: "tabular-nums" }}>
                  {count}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      }
    >
      <Box>
        <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.4, textTransform: "uppercase" }}>
          Clone writes
        </Typography>
        <Stack direction="row" alignItems="baseline" spacing={0.5}>
          <Typography sx={{
            typography: "s1", fontWeight: 700,
            color: TWIN_TINT,
            fontVariantNumeric: "tabular-nums",
          }}>
            {writes}
          </Typography>
          {delta != null && delta !== 0 && (
            <Typography sx={{
              typography: "s3", fontWeight: 700,
              color: delta > 0 ? "#C2603F" : "#16A34A",
              fontVariantNumeric: "tabular-nums",
            }}>
              {arrow}{Math.abs(delta)}
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
  if (diff.identical) return <Box component="span" sx={{ color: "text.subtitle" }}>Same behaviour as the baseline.</Box>;

  const parts = [];
  if (diff.missedNow.length) parts.push(`stopped calling ${diff.missedNow.join(", ")}`);
  else if (diff.dropped.length) parts.push(`dropped ${diff.dropped.join(", ")}`);
  if (diff.added.length) parts.push(`added ${diff.added.join(", ")}`);
  if (diff.turnDelta) parts.push(`${Math.abs(diff.turnDelta)} ${diff.turnDelta > 0 ? "more" : "fewer"} turns`);
  if (!parts.length && diff.firstDivergence >= 0) parts.push(`same tools, wording differs from turn ${diff.firstDivergence + 1}`);

  return (
    <>
      {diff.verdictChanged && (
        <Box component="span" sx={{ color: "#C2603F", fontWeight: 700 }}>Verdict changed — </Box>
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
      <Typography sx={{ typography: "s3", color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.3 }}>
        {label}
      </Typography>
      <Stack direction="row" alignItems="baseline" spacing={0.5}>
        <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
        {delta != null && delta !== 0 && (
          <Typography sx={{ typography: "s3", fontWeight: 600, color: good ? "#5AA47B" : "#C2603F" }}>
            {delta > 0 ? "+" : ""}{delta}%
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
Stat.propTypes = {
  label: PropTypes.string, value: PropTypes.string,
  delta: PropTypes.number, lowerIsBetter: PropTypes.bool,
};

function GroupBand({ label, count }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{ px: 2.5, py: 1, bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{count}</Typography>
    </Stack>
  );
}
GroupBand.propTypes = { label: PropTypes.string, count: PropTypes.number };

function Letter({ letter, color }) {
  return (
    <Box
      sx={{
        width: 20, height: 20, borderRadius: 0.75, flexShrink: 0,
        display: "grid", placeItems: "center", typography: "s3", fontWeight: 700,
        color, bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
Letter.propTypes = { letter: PropTypes.string, color: PropTypes.string };
