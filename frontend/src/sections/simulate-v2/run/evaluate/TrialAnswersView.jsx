import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import MetricStrip from "src/components/metric-strip/MetricStrip";
import { CustomTabs } from "src/components/tabs/tabs";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { statusColors } from "src/utils/runStatus";
import { StatusChip, StatusDot } from "../../components/primitives";
import { scenarioTitle } from "../../_mock/scenarios";
import {
  ANSWER_TYPES,
  OUTCOME_LABEL,
  aboutCredits,
  answerColorOf,
  askScopeLabelOf,
  byScenario,
  countsOf,
  csvOf,
  outcomeOf,
  readsLabelOf,
  whenOf,
} from "../../_mock/trialQuestions";
import {
  GRADING_ERROR,
  denominatorLine,
  trialLabelOf,
} from "../../_mock/evalEvidence";
import ResultIdentity from "./ResultIdentity";

/*
  One ask's answers — Observe's Instant eval results, for trials: the
  counts, one square per trial grouped by scenario, the answers set beside
  the run's task checks, then every trial with its answer and why.
*/
const NOT_READ = "Not read";
const SQUARE = 14;

const SectionHead = ({ title, sub, right }) => (
  <Stack direction="row" alignItems="flex-end" spacing={2} sx={{ mb: 1 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography sx={{ typography: "m3", fontWeight: 500 }}>
        {title}
      </Typography>
      {sub && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {sub}
        </Typography>
      )}
    </Box>
    {right}
  </Stack>
);
SectionHead.propTypes = {
  title: PropTypes.node,
  sub: PropTypes.node,
  right: PropTypes.node,
};

const cellSx = { typography: "s1", color: "text.secondary", py: 0, px: 1.5 };
const headSx = {
  typography: "s2",
  fontWeight: 500,
  color: "text.tertiary",
  bgcolor: "transparent",
  height: TABLE_HEADER_HEIGHT.default,
  py: 0,
  px: 1.5,
};

export default function TrialAnswersView({
  ask,
  tasks,
  runId,
  runLabel,
  answering,
  onAskAgain,
  onOpenTask,
}) {
  const theme = useTheme();
  const [qi, setQi] = useState(0);
  const [focus, setFocus] = useState(null);
  const q = ask.questions[Math.min(qi, ask.questions.length - 1)];
  useEffect(() => setFocus(null), [qi, ask.id]);

  const chosen = useMemo(() => {
    const ids = new Set(ask.taskIds);
    return tasks.filter((t) => ids.has(t.id));
  }, [ask.taskIds, tasks]);
  const answeredIds = Object.keys(ask.answers[q.id] || {});
  const isError = (id) => !!ask.answers[q.id]?.[id]?.error;
  const readTotal = answeredIds.length;
  const skippedN = Object.keys(ask.skipped).length;
  /* While answering, trials land in run order, a few at a time. */
  const shown = answering == null ? readTotal : Math.min(answering, readTotal);
  const shownIds = useMemo(
    () => new Set(answeredIds.slice(0, shown)),
    [answeredIds, shown],
  );
  /* Grading errors are the judge's, kept apart from every answer. */
  const errShown = answeredIds.filter(
    (id) => shownIds.has(id) && isError(id),
  ).length;
  const answerFor = (t) =>
    shownIds.has(t.id) ? ask.answers[q.id][t.id] : null;

  const counts = useMemo(() => {
    const by = {};
    chosen.forEach((t) => {
      const a = shownIds.has(t.id) ? ask.answers[q.id][t.id] : null;
      if (a && !a.error) by[a.answer] = (by[a.answer] || 0) + 1;
    });
    return countsOf({ answers: { [q.id]: {} } }, q).map((c) => ({
      ...c,
      n: by[c.label] || 0,
    }));
  }, [chosen, shownIds, ask.answers, q]);
  const colorOf = (label) =>
    label === NOT_READ
      ? theme.palette.text.disabled
      : label === GRADING_ERROR
        ? theme.palette.text.secondary
        : answerColorOf(q, label, theme);
  const labelOfAnswer = (a) => (a.error ? GRADING_ERROR : a.answer);

  const groups = useMemo(() => byScenario(chosen), [chosen]);
  /* Answers against the run's task checks: both stay; neither rewrites the other. */
  const outcomes = ["passed", "failed", "unmeasured", "error"].filter((o) =>
    chosen.some((t) => outcomeOf(t) === o),
  );
  const cross = (label, o) =>
    chosen.filter((t) => outcomeOf(t) === o && answerFor(t)?.answer === label)
      .length;

  const rows = chosen.filter((t) => {
    if (!focus) return true;
    const a = answerFor(t);
    return focus === NOT_READ
      ? !a && ask.skipped[t.id]
      : a && labelOfAnswer(a) === focus;
  });

  const exportCsv = () => {
    const blob = new Blob([csvOf(ask, chosen)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${runLabel.replace(/\s+/g, "-").toLowerCase()}-${ask.id}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const evaluated = shown - errShown;
  const pct = (n) =>
    evaluated ? `${Math.round((n / evaluated) * 100)}%` : "—";
  const strip = [
    ...counts.map((c) => ({
      key: c.label,
      label: c.label,
      value: c.n,
      hint: pct(c.n),
      onClick: () => setFocus((f) => (f === c.label ? null : c.label)),
    })),
    ...(errShown
      ? [
          {
            key: GRADING_ERROR,
            label: GRADING_ERROR,
            value: errShown,
            hint: "Judge didn't answer",
            onClick: () =>
              setFocus((f) => (f === GRADING_ERROR ? null : GRADING_ERROR)),
          },
        ]
      : []),
    ...(skippedN
      ? [
          {
            key: NOT_READ,
            label: NOT_READ,
            value: skippedN,
            hint: "No evidence to read",
            onClick: () => setFocus((f) => (f === NOT_READ ? null : NOT_READ)),
          },
        ]
      : []),
  ];

  return (
    <Box>
      {/* ── header ── */}
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "m1", fontWeight: 600 }}>
            {q.text}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            {runLabel} · {askScopeLabelOf(ask)} · {chosen.length} trials · reads{" "}
            {readsLabelOf(ask.reads).toLowerCase()} ·{" "}
            {q.type === ANSWER_TYPES.YES_NO
              ? "Yes / No"
              : `${q.labels.length} categories`}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          onClick={onAskAgain}
          disabled={answering != null}
        >
          Ask again
        </Button>
      </Stack>

      <ResultIdentity
        result={ask}
        trials={chosen}
        runId={runId}
        runLabel={runLabel}
      />

      {ask.questions.length > 1 && (
        <CustomTabs value={qi} onChange={(_, v) => setQi(v)} sx={{ mt: 1.5 }}>
          {ask.questions.map((x, i) => (
            <Tab
              key={x.id}
              value={i}
              label={
                <Tooltip title={x.text}>
                  <Box
                    component="span"
                    sx={{
                      maxWidth: 220,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      display: "block",
                    }}
                  >
                    {i + 1} · {x.text}
                  </Box>
                </Tooltip>
              }
            />
          ))}
        </CustomTabs>
      )}

      {/* ── status ── */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2 }}>
        <StatusDot status={answering != null ? "running" : "passed"} />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {answering != null
            ? `Answering… ${shown} of ${readTotal} trials`
            : `${readTotal} trials read in ${ask.durationS} s`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Started by {ask.by} · {whenOf(ask.at)} · {aboutCredits(ask.credits)}
        </Typography>
      </Stack>

      {/* ── counts ── */}
      <MetricStrip items={strip} sx={{ mt: 1.5 }} />
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {ask.questions.length > 1 ? `Question ${qi + 1}: ` : ""}
        {denominatorLine({
          evaluated,
          requested: chosen.length,
          skipped: skippedN,
          errors: errShown,
        })}
      </Typography>

      {/* ── one square per trial ── */}
      <Box sx={{ mt: 3 }}>
        <SectionHead title="Every trial" sub="Red base = task checks failed" />
        <Stack
          direction="row"
          flexWrap="wrap"
          alignItems="flex-end"
          gap="6px"
          sx={{ pb: 0.75, borderBottom: "1px solid", borderColor: "divider" }}
        >
          {groups.map((g) => (
            <Stack key={g.name} direction="column-reverse" gap="3px">
              {g.rows.map((t) => {
                const a = answerFor(t);
                const label = a
                  ? labelOfAnswer(a)
                  : ask.skipped[t.id]
                    ? NOT_READ
                    : null;
                const dim = focus && label !== focus;
                const failed = outcomeOf(t) === "failed";
                return (
                  <Tooltip
                    key={t.id}
                    title={
                      <>
                        {g.name}
                        <br />
                        {t.persona?.name || t.id} ·{" "}
                        {label || "waiting for an answer"} · task checks{" "}
                        {OUTCOME_LABEL[outcomeOf(t)].toLowerCase()}
                      </>
                    }
                  >
                    <Box
                      component="button"
                      type="button"
                      onClick={() => onOpenTask?.(t)}
                      aria-label={`${g.name}, ${label || "waiting"}`}
                      sx={{
                        width: SQUARE,
                        height: SQUARE,
                        p: 0,
                        border: 0,
                        borderRadius: "2px",
                        cursor: "pointer",
                        bgcolor: label ? colorOf(label) : "action.hover",
                        opacity: dim ? 0.2 : 1,
                        boxShadow: failed
                          ? `inset 0 -3px 0 ${statusColors(theme, "danger").solid}`
                          : "none",
                        transition: "opacity 120ms",
                      }}
                    />
                  </Tooltip>
                );
              })}
            </Stack>
          ))}
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          {groups.length} scenarios · each column is one scenario&apos;s repeats
        </Typography>
        <Stack
          direction="row"
          flexWrap="wrap"
          columnGap={2}
          rowGap={0.5}
          sx={{ mt: 1.5 }}
        >
          {[
            ...counts,
            ...(errShown ? [{ label: GRADING_ERROR, n: errShown }] : []),
            ...(skippedN ? [{ label: NOT_READ, n: skippedN }] : []),
          ].map((c) => (
            <Stack
              key={c.label}
              direction="row"
              alignItems="center"
              spacing={0.75}
              component="button"
              type="button"
              onClick={() => setFocus((f) => (f === c.label ? null : c.label))}
              sx={{
                border: 0,
                p: 0,
                bgcolor: "transparent",
                cursor: "pointer",
                opacity: focus && focus !== c.label ? 0.45 : 1,
              }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "2px",
                  bgcolor: colorOf(c.label),
                }}
              />
              <Typography sx={{ typography: "s1", color: "text.primary" }}>
                {c.label}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {c.label === NOT_READ || c.label === GRADING_ERROR
                  ? c.n
                  : `${pct(c.n)} · ${c.n}`}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>

      {/* ── against the task checks ── */}
      <Box sx={{ mt: 3 }}>
        <SectionHead title="Against the run's task checks" />
        <Table size="small" sx={{ maxWidth: 640 }}>
          <TableHead>
            <TableRow>
              <TableCell sx={headSx}>Answer</TableCell>
              {outcomes.map((o) => (
                <TableCell key={o} align="right" sx={headSx}>
                  Task checks {OUTCOME_LABEL[o].toLowerCase()}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {counts.map((c) => (
              <TableRow key={c.label} sx={{ height: ROW_HEIGHT.default }}>
                <TableCell
                  sx={{ ...cellSx, color: "text.primary", fontWeight: 500 }}
                >
                  {c.label}
                </TableCell>
                {outcomes.map((o) => (
                  <TableCell
                    key={o}
                    align="right"
                    sx={{ ...cellSx, fontVariantNumeric: "tabular-nums" }}
                  >
                    {cross(c.label, o)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      {/* ── the answers ── */}
      <Box sx={{ mt: 3 }}>
        <SectionHead
          title={`Trials ${rows.length}`}
          sub={
            focus ? (
              <>
                Showing {focus}
                {" · "}
                <Box
                  component="button"
                  type="button"
                  onClick={() => setFocus(null)}
                  sx={{
                    border: 0,
                    p: 0,
                    bgcolor: "transparent",
                    color: "text.secondary",
                    cursor: "pointer",
                    typography: "s3",
                    textDecoration: "underline",
                  }}
                >
                  Show all
                </Box>
              </>
            ) : (
              "Click a trial to open its conversation and tool calls"
            )
          }
          right={
            <Button
              size="small"
              variant="outlined"
              startIcon={
                <Iconify icon="solar:download-minimalistic-linear" width={14} />
              }
              onClick={exportCsv}
              disabled={answering != null}
            >
              Export CSV
            </Button>
          }
        />
        <Table size="small" sx={{ tableLayout: "fixed" }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headSx, width: 96 }}>Trial</TableCell>
              <TableCell sx={{ ...headSx, width: "24%" }}>Scenario</TableCell>
              <TableCell sx={{ ...headSx, width: "16%" }}>Persona</TableCell>
              <TableCell sx={{ ...headSx, width: 120 }}>Task checks</TableCell>
              <TableCell sx={{ ...headSx, width: 150 }}>Answer</TableCell>
              <TableCell sx={headSx}>Why</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((t) => {
              const a = answerFor(t);
              const label = a
                ? labelOfAnswer(a)
                : ask.skipped[t.id]
                  ? NOT_READ
                  : null;
              const why = a ? a.reason : ask.skipped[t.id] || "";
              return (
                <TableRow
                  key={t.id}
                  hover
                  onClick={() => onOpenTask?.(t)}
                  sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
                >
                  <TableCell sx={cellSx}>
                    <Typography noWrap sx={{ typography: "s1" }}>
                      {trialLabelOf(t, runId)}
                    </Typography>
                  </TableCell>
                  <TableCell
                    sx={{ ...cellSx, color: "text.primary", fontWeight: 500 }}
                  >
                    <Typography
                      noWrap
                      sx={{ typography: "s1", fontWeight: 500 }}
                    >
                      {scenarioTitle(t.title || t.name)}
                    </Typography>
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <Typography noWrap sx={{ typography: "s1" }}>
                      {t.persona?.name || "—"}
                    </Typography>
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <StatusChip status={t.status} />
                  </TableCell>
                  <TableCell sx={cellSx}>
                    {label ? (
                      <Stack direction="row" alignItems="center" spacing={0.75}>
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "2px",
                            flexShrink: 0,
                            bgcolor: colorOf(label),
                          }}
                        />
                        <Typography noWrap sx={{ typography: "s1" }}>
                          {label}
                        </Typography>
                      </Stack>
                    ) : (
                      <Typography
                        sx={{ typography: "s1", color: "text.disabled" }}
                      >
                        …
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <Tooltip title={why} placement="top-start">
                      <Typography noWrap sx={{ typography: "s1" }}>
                        {why}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}
TrialAnswersView.propTypes = {
  ask: PropTypes.object.isRequired,
  tasks: PropTypes.array.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
  answering: PropTypes.number,
  onAskAgain: PropTypes.func,
  onOpenTask: PropTypes.func,
};
