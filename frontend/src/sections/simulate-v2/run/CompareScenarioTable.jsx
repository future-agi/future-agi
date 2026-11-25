import PropTypes from "prop-types";
import { Fragment } from "react";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  DomainChip,
  PersonaBadge,
  VERDICT_TONE,
} from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";
import { behaviourDiff } from "../_mock/compareView";
import { subGoalResultsOf } from "../_mock/scenarioSubGoals";
import { cellFactsOf, subGoalCountOf } from "../_mock/compareRowFacts";
import { comparedWithText, rowLabelOf, runLabelOf } from "../_mock/scoreDelta";
import { csatOf, latencyOf as tableLatencyOf } from "./traceTableHelpers";
import { turnsOf as countTurns } from "../_mock/runStream";
import { OutcomeLine } from "./CompareOutcome";
import CompareLetter from "./CompareLetter";

/*
  The comparison, laid out like the run's scenario table: one block per
  scenario, and inside it one row per run — Baseline first, then every other
  run in the order they were picked. Each row carries that run's result, its
  trial dots, sub-goals and eval scores, with an arrow and the absolute change
  in points against the baseline row. Nothing the row already shows is said a
  second time in a sentence.
*/

/* Per-task metrics, derived the way the single-run table derives them, so a
   value read there equals the value read here. */
/* The run table's status colours, so a flaky amber never reads like a red
   fail (24 Nov review). The shared verdict tone keeps its labels. */
const STATUS_DOT = {
  passed: "status.success.solid",
  flaky: "status.warning.solid",
  failed: "status.danger.solid",
};
const toneOf = (status) => {
  const base = VERDICT_TONE[status] || VERDICT_TONE.missing;
  const text = { passed: "status.success.fg", failed: "status.danger.fg" };
  return {
    ...base,
    color: STATUS_DOT[status] || base.color,
    text: text[status] || base.text,
  };
};
const turnsOf = (t) => (t ? countTurns(t.steps || []) : null);
const latencyOf = (t) => (t ? tableLatencyOf(t) : null);

const METRICS = [
  {
    key: "csat",
    label: "CSAT",
    get: (c) => {
      const v = csatOf(c.task);
      return v != null ? String(v) : "—";
    },
  },
  {
    key: "turns",
    label: "Turns",
    get: (c) => {
      const v = turnsOf(c.task);
      return v != null ? String(v) : "—";
    },
  },
  {
    key: "latency",
    label: "Latency",
    get: (c) => {
      const v = latencyOf(c.task);
      if (v == null) return "—";
      /* Seconds once it passes one, so the cell never cuts off. */
      return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`;
    },
  },
  {
    key: "duration",
    label: "Duration",
    get: (c) => (c.task ? `${(c.durationMs / 1000).toFixed(1)}s` : "—"),
  },
  {
    key: "tokens",
    label: "Tokens",
    get: (c) => (c.task ? c.tokens.toLocaleString() : "—"),
  },
  {
    key: "cost",
    label: "Model cost",
    get: (c) =>
      !c.task
        ? "—"
        : c.costUnverified
          ? "unverified"
          : `≈ $${(c.cost || 0).toFixed(2)}`,
  },
];

/* The two sizes this screen reads at: the rest of the table keeps the body
   size, names and key numbers sit a step above it. */
const KEY = { fontSize: 13, lineHeight: "18px" };
const NUM = { fontVariantNumeric: "tabular-nums" };

const HEAD_H = 40;

/** ▲ +18 / ▼ −5 — green when better, red when worse, nothing when the same. */
function DeltaMark({ d, amberWhenWorse }) {
  if (!d || d.dir === "same") return null;
  if (d.meaningful === false)
    return (
      <Typography
        component="span"
        sx={{
          typography: "s3",
          color: "text.tertiary",
          whiteSpace: "nowrap",
          ...NUM,
        }}
      >
        {d.text}
      </Typography>
    );
  const color = d.better
    ? "status.success.fg"
    : amberWhenWorse
      ? "status.warning.fg"
      : "status.danger.fg";
  return (
    <Typography
      component="span"
      sx={{
        typography: "s3",
        fontWeight: 600,
        color,
        whiteSpace: "nowrap",
        ...NUM,
      }}
    >
      {`${d.arrow} ${d.text}`}
    </Typography>
  );
}
DeltaMark.propTypes = { d: PropTypes.object, amberWhenWorse: PropTypes.bool };

function Cell({ children, first, align = "left", sx }) {
  return (
    <Box
      sx={{
        minWidth: 0,
        px: 1.5,
        display: "flex",
        alignItems: "center",
        justifyContent: align === "right" ? "flex-end" : "flex-start",
        ...(!first && { borderLeft: "1px solid", borderColor: "divider" }),
        ...sx,
      }}
    >
      {children}
    </Box>
  );
}
Cell.propTypes = {
  children: PropTypes.node,
  first: PropTypes.bool,
  align: PropTypes.string,
  sx: PropTypes.object,
};

function HeadCell({ children, first, align }) {
  return (
    <Cell first={first} align={align} sx={{ height: HEAD_H }}>
      <Typography
        noWrap
        sx={{ typography: "s2", fontWeight: 500, color: "text.secondary" }}
      >
        {children}
      </Typography>
    </Cell>
  );
}
HeadCell.propTypes = {
  children: PropTypes.node,
  first: PropTypes.bool,
  align: PropTypes.string,
};

/* One dot per trial, in the order they ran — a flaky scenario reads as a mix. */
function TrialDotsRow({ samples }) {
  return (
    <Stack direction="row" spacing={0.375} aria-hidden>
      {samples.map((s, i) => (
        <Box
          key={i}
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: STATUS_DOT[s] || VERDICT_TONE.unmeasured.color,
          }}
        />
      ))}
    </Stack>
  );
}
TrialDotsRow.propTypes = { samples: PropTypes.array };

function RunRow({
  cell,
  baseCell,
  index,
  row,
  run,
  identity,
  columns,
  evals,
  view,
  env,
  envState,
  py,
  onOpen,
  last,
}) {
  const isBase = index === 0;
  const f = cellFactsOf(cell, baseCell, evals, (task) =>
    subGoalCountOf(subGoalResultsOf(task, env, envState).items),
  );
  const tone = toneOf(cell.status);
  const flaky = cell.status === "flaky";
  const samples = cell.task?.samples;
  const hasSamples = Array.isArray(samples) && samples.length > 1;
  const label = rowLabelOf(run, index);
  const cellSx = { py };
  return (
    <Box
      {...clickable(() => onOpen(row))}
      sx={{
        display: "grid",
        gridTemplateColumns: columns.template,
        alignItems: "stretch",
        borderBottom: "1px solid",
        borderColor: "divider",
        cursor: "pointer",
        ...(last && { borderBottom: "none" }),
        "&:hover": { bgcolor: "action.hover" },
        ...focusRing,
      }}
    >
      <Cell first sx={cellSx}>
        <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
          <CompareLetter letter={cell.letter} color={cell.color} />
          <Box minWidth={0}>
            <Typography noWrap sx={{ ...KEY, fontWeight: 600 }}>
              {label}
              {isBase && (
                <Box
                  component="span"
                  sx={{ color: "text.tertiary", fontWeight: 500 }}
                >
                  {` · ${runLabelOf(run)}`}
                </Box>
              )}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
              title={identity.label}
            >
              {identity.agentLabel}
            </Typography>
          </Box>
        </Stack>
      </Cell>

      <Cell sx={cellSx}>
        <Stack spacing={0.5} alignItems="flex-start">
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                flexShrink: 0,
                bgcolor: tone.color,
              }}
            />
            <Typography
              sx={{
                ...KEY,
                fontWeight: 600,
                color: flaky ? "status.warning.fg" : tone.text || tone.color,
              }}
            >
              {tone.label}
            </Typography>
          </Stack>
          {/* Who the failure belongs to, on the row that reports it — an
              unmeasured row must never read as the agent's. */}
          {cell.domain && !cell.measured && (
            <DomainChip domain={cell.domain} dense />
          )}
        </Stack>
      </Cell>

      <Cell sx={cellSx}>
        {cell.task && cell.measured && cell.repeats ? (
          <Stack direction="row" alignItems="center" spacing={0.75}>
            {hasSamples ? <TrialDotsRow samples={samples} /> : null}
            <Tooltip
              arrow
              title={`${cell.passes ?? 0} of ${cell.repeats} trials passed`}
            >
              <Typography
                sx={{ ...KEY, fontWeight: 600, ...NUM }}
              >{`${cell.passes ?? 0}/${cell.repeats}`}</Typography>
            </Tooltip>
            <DeltaMark d={f.passesDelta} amberWhenWorse={flaky} />
            {f.trialsNote ? (
              <Typography
                component="span"
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  whiteSpace: "nowrap",
                }}
              >
                {`of ${f.trialsNote.n} · baseline had ${f.trialsNote.baseN}`}
              </Typography>
            ) : null}
          </Stack>
        ) : (
          <Typography sx={{ ...KEY, color: "text.disabled" }}>—</Typography>
        )}
      </Cell>

      <Cell sx={cellSx} align="right">
        {f.subGoals ? (
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Tooltip
              arrow
              title={`${f.subGoals.met} of ${f.subGoals.total} sub-goals met`}
            >
              <Typography
                sx={{ ...KEY, fontWeight: 600, ...NUM }}
              >{`${f.subGoals.met}/${f.subGoals.total}`}</Typography>
            </Tooltip>
            <DeltaMark d={f.subGoalsDelta} />
          </Stack>
        ) : (
          <Typography sx={{ ...KEY, color: "text.disabled" }}>—</Typography>
        )}
      </Cell>

      {columns.showScorers &&
        f.evals.map((e) => (
          <Cell key={e.id} sx={cellSx} align="right">
            {e.pts == null ? (
              <Typography sx={{ ...KEY, color: "text.disabled" }}>—</Typography>
            ) : (
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography
                  sx={{
                    ...KEY,
                    fontWeight: 600,
                    color: e.passed ? "text.primary" : "status.danger.fg",
                    ...NUM,
                  }}
                >{`${e.pts}%`}</Typography>
                <DeltaMark d={e.delta} />
              </Stack>
            )}
          </Cell>
        ))}

      {columns.metrics.map((m) => (
        <Cell key={m.key} sx={cellSx} align="right">
          <Typography
            noWrap
            sx={{ typography: "s2", color: "text.secondary", ...NUM }}
          >
            {m.get(cell)}
          </Typography>
        </Cell>
      ))}

      <Cell sx={cellSx}>
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
            overflow: "hidden",
            minWidth: 0,
          }}
          title={cell.deciding.text}
        >
          {view.diff && !isBase ? (
            <DiffText diff={behaviourDiff(baseCell?.task, cell.task)} />
          ) : (
            <>
              {cell.deciding.kind === "claim" && (
                <Box
                  component="span"
                  sx={{ color: "status.danger.fg", fontWeight: 600 }}
                >
                  Said, not done —{" "}
                </Box>
              )}
              {cell.deciding.text}
            </>
          )}
        </Typography>
      </Cell>
    </Box>
  );
}
RunRow.propTypes = {
  cell: PropTypes.object,
  baseCell: PropTypes.object,
  index: PropTypes.number,
  row: PropTypes.object,
  run: PropTypes.object,
  identity: PropTypes.object,
  columns: PropTypes.object,
  evals: PropTypes.array,
  view: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  py: PropTypes.number,
  onOpen: PropTypes.func,
  last: PropTypes.bool,
};

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

function GroupBand({ group, bandAction }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
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
                    minWidth: 30,
                    ...NUM,
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
  );
}
GroupBand.propTypes = { group: PropTypes.object, bandAction: PropTypes.func };

function ScenarioHeading({ row, view, onOpen }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      {...clickable(() => onOpen(row))}
      sx={{
        px: 2,
        py: 1,
        bgcolor: "background.neutral",
        borderBottom: "1px solid",
        borderColor: "divider",
        cursor: "pointer",
        ...focusRing,
      }}
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography
            title={row.title}
            sx={{
              ...KEY,
              fontWeight: 600,
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {row.title}
          </Typography>
          {row.critical && (
            <Tooltip arrow title="Must-pass scenario">
              <Box sx={{ display: "flex" }}>
                <Iconify
                  icon="solar:danger-triangle-linear"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </Box>
            </Tooltip>
          )}
        </Stack>
        {view.rowHeight === "large" && (
          <>
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
            <Box sx={{ mt: 0.75 }}>
              <PersonaBadge persona={row.persona} compact />
            </Box>
          </>
        )}
      </Box>
      <OutcomeLine
        pair={row.pair}
        cells={row.cells}
        extra={
          view.rowHeight !== "large" && typeof row.persona?.name === "string"
            ? row.persona.name
            : null
        }
      />
      <Tooltip arrow title="Compare the transcripts, trial by trial">
        <Box sx={{ display: "flex", flexShrink: 0 }}>
          <Iconify
            icon="solar:alt-arrow-right-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </Box>
      </Tooltip>
    </Stack>
  );
}
ScenarioHeading.propTypes = {
  row: PropTypes.object,
  view: PropTypes.object,
  onOpen: PropTypes.func,
};

export default function CompareScenarioTable({
  groups,
  view,
  rowPad,
  evals,
  runs,
  env,
  envState,
  identityOf,
  bandAction,
  onOpen,
}) {
  const metrics = METRICS.filter((m) => view.columns[m.key]);
  const showScorers = !!view.columns.scorers && (evals?.length || 0) > 0;
  const columns = {
    metrics,
    showScorers,
    template: [
      "minmax(168px, 210px)",
      "112px",
      "136px",
      "104px",
      ...(showScorers ? evals.map(() => "96px") : []),
      ...metrics.map(() => "72px"),
      "minmax(150px, 1fr)",
    ].join(" "),
  };
  const py = Math.max(0.5, rowPad * 0.66);

  return (
    <Box>
      {/* Fixed while the rows scroll: solid, and above every row. */}
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 3,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
          display: { xs: "none", lg: "block" },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {comparedWithText(runs)}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {
              "▲ better · ▼ worse vs baseline · changes under 5 pts aren't marked"
            }
          </Typography>
        </Stack>
        <Box sx={{ display: "grid", gridTemplateColumns: columns.template }}>
          <HeadCell first>Run</HeadCell>
          <HeadCell>Result</HeadCell>
          <HeadCell>Trials</HeadCell>
          <HeadCell align="right">Sub-goals</HeadCell>
          {showScorers &&
            evals.map((e) => (
              <HeadCell key={e.id} align="right">
                {e.name}
              </HeadCell>
            ))}
          {metrics.map((m) => (
            <HeadCell key={m.key} align="right">
              {m.label}
            </HeadCell>
          ))}
          <HeadCell>
            {view.diff
              ? "What changed against the baseline"
              : "What decided it"}
          </HeadCell>
        </Box>
      </Box>

      {groups.map((group) => (
        <Fragment key={group.id}>
          {group.label && <GroupBand group={group} bandAction={bandAction} />}
          {group.rows.map((row) => (
            <Box
              key={row.id}
              sx={{ borderBottom: "1px solid", borderColor: "divider" }}
            >
              <ScenarioHeading row={row} view={view} onOpen={onOpen} />
              {row.cells.map((cell, i) => {
                const run = runs.find((r) => r.id === cell.runId) || {};
                return (
                  <RunRow
                    key={cell.runId}
                    cell={cell}
                    baseCell={row.cells[0]}
                    index={i}
                    row={row}
                    run={run}
                    identity={identityOf(run)}
                    columns={columns}
                    evals={evals}
                    view={view}
                    env={env}
                    envState={envState}
                    py={py}
                    onOpen={onOpen}
                    last={i === row.cells.length - 1}
                  />
                );
              })}
            </Box>
          ))}
        </Fragment>
      ))}
    </Box>
  );
}
CompareScenarioTable.propTypes = {
  groups: PropTypes.array,
  view: PropTypes.object,
  rowPad: PropTypes.number,
  evals: PropTypes.array,
  runs: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  identityOf: PropTypes.func,
  bandAction: PropTypes.func,
  onOpen: PropTypes.func,
};
