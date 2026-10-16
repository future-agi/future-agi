import React, { Suspense, lazy, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  LinearProgress,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { keyframes } from "@mui/material/styles";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { insightsReportLink } from "src/sections/projects/observeHandoff";
import { ANSWER_TYPES, readOption } from "../instantEvalPayloads";
import { answerText, quoted } from "../instantEvalAnswers";
import { formatWhen, unitMeta } from "../instantEvalUnits";
import KeepAnswering from "../results/KeepAnswering";
import { AskHeatmap, AskPatterns, AskTimeline, answerState } from "./viz";
import AskRowsTable from "./AskRowsTable";
import { formatElapsed, scopeWords, windowLabel } from "./askFormat";
import { visibleRows } from "./askRows";
import {
  answerGridLink,
  rowsGridLink,
  sourceViewLink,
  sourceViewTitle,
  userPageLink,
} from "./askLinks";

const TracesDrawer = lazy(() => import("../../../TracesDrawer/TracesDrawer"));

const pulse = keyframes`
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
`;

const CONTENT_WIDTH = 1180;

function StatusLine({ run, unit, creating }) {
  const meta = unitMeta(unit);
  const running = run.status === "running" || run.status === "loading";
  const failed = run.records.filter((record) =>
    run.questions.some(
      (question) => answerState(record, question.configId).state === "failed",
    ),
  ).length;
  let text;
  if (creating) text = "Saving the question and starting Jev…";
  else if (run.status === "loading") text = "Loading the answers…";
  else if (run.status === "done")
    text = `${run.answered.toLocaleString()}${
      run.answered < run.total ? ` of ${run.total.toLocaleString()}` : ""
    } ${run.total === 1 ? meta.noun : meta.plural} answered${
      run.elapsedMs != null
        ? ` in ${formatElapsed(run.elapsedMs)}`
        : run.startedAt
          ? ` · ran ${formatWhen(run.startedAt)}`
          : ""
    }`;
  else
    text = `Answering ${run.answered.toLocaleString()} of ${run.total.toLocaleString()}${
      run.elapsedMs != null ? ` · ${formatElapsed(run.elapsedMs)}` : ""
    }`;
  const progress = run.total ? (run.answered / run.total) * 100 : 0;
  return (
    <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0, maxWidth: 520 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box
          aria-hidden
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flexShrink: 0,
            bgcolor:
              run.status === "error"
                ? "error.main"
                : running || creating
                  ? "info.main"
                  : "success.main",
            ...((running || creating) && {
              animation: `${pulse} 1.6s ease-in-out infinite`,
              "@media (prefers-reduced-motion: reduce)": { animation: "none" },
            }),
          }}
        />
        <Typography
          variant="body2"
          aria-live="polite"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {text}
          {failed > 0 && run.status === "done" && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {` · ${failed} couldn't be answered`}
            </Box>
          )}
        </Typography>
      </Stack>
      {(running || creating) && (
        <LinearProgress
          variant={creating || !run.total ? "indeterminate" : "determinate"}
          value={progress}
          aria-label="Answers so far"
          sx={{ height: 4, borderRadius: 2 }}
        />
      )}
    </Stack>
  );
}

StatusLine.propTypes = {
  run: PropTypes.object.isRequired,
  unit: PropTypes.string.isRequired,
  creating: PropTypes.bool,
};

function MoreMenu({ questions, onCopyLink }) {
  const [anchor, setAnchor] = useState(null);
  const navigate = useNavigate();
  const close = () => setAnchor(null);
  const editable = questions.filter((question) => question.templateId);
  return (
    <>
      <IconButton
        size="small"
        aria-label="More actions"
        onClick={(event) => setAnchor(event.currentTarget)}
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        <Iconify icon="mdi:dots-horizontal" width={18} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {editable.map((question, index) => (
          <MenuItem
            key={question.configId}
            onClick={() => {
              close();
              navigate(`/dashboard/evaluations/${question.templateId}`);
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:pencil-outline" width={18} />
            </ListItemIcon>
            <ListItemText>
              Edit evaluator{editable.length > 1 ? ` ${index + 1}` : ""}
            </ListItemText>
          </MenuItem>
        ))}
        <MenuItem
          onClick={() => {
            close();
            onCopyLink();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:link-variant" width={18} />
          </ListItemIcon>
          <ListItemText>Copy link to this run</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
}

MoreMenu.propTypes = {
  questions: PropTypes.array.isRequired,
  onCopyLink: PropTypes.func.isRequired,
};

function SelectionBar({
  count,
  unit,
  label,
  onFindPatterns,
  gridLink,
  onClear,
}) {
  const meta = unitMeta(unit);
  return (
    <Paper
      elevation={8}
      role="region"
      aria-label="Selected rows"
      sx={{
        position: "sticky",
        bottom: 16,
        zIndex: 2,
        mx: "auto",
        px: 2,
        py: 1.25,
        borderRadius: 1.5,
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        flexWrap: "wrap",
        maxWidth: 880,
      }}
    >
      <Typography
        variant="body2"
        fontWeight={600}
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {count.toLocaleString()} {count === 1 ? meta.noun : meta.plural}
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        noWrap
        sx={{ flex: 1, minWidth: 0 }}
        title={label}
      >
        {label}
      </Typography>
      {onFindPatterns && (
        <Button
          size="small"
          variant="contained"
          startIcon={<Iconify icon="mdi:chart-box-outline" width={16} />}
          onClick={onFindPatterns}
        >
          Find patterns
        </Button>
      )}
      {gridLink && (
        <Button
          size="small"
          variant="outlined"
          component={RouterLink}
          to={gridLink}
          startIcon={<Iconify icon="mdi:filter-outline" width={16} />}
        >
          Show in Traces
        </Button>
      )}
      <Button size="small" onClick={onClear}>
        Clear
      </Button>
    </Paper>
  );
}

SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  unit: PropTypes.string.isRequired,
  label: PropTypes.string,
  onFindPatterns: PropTypes.func,
  gridLink: PropTypes.string,
  onClear: PropTypes.func.isRequired,
};

/**
 * One Instant eval run, full page: the question, where Jev is, every row on
 * a timeline filling in with its answer, what stands out, the heatmap, and
 * the rows themselves. Focusing an answer or selecting rows on a chart
 * narrows the table and offers the hand-offs.
 */
export default function AskRunView({
  projectId,
  run,
  unit,
  scope,
  creating,
  onRunAgain,
  rerunning,
}) {
  const navigate = useNavigate();
  const meta = unitMeta(unit);
  const { questions, records } = run;
  const [focus, setFocus] = useState({ configId: null, answer: null });
  const [selection, setSelection] = useState(null);
  const [opened, setOpened] = useState(null);
  const [copied, setCopied] = useState(false);

  // Focus the first question once the questions are known.
  const firstConfigId = questions[0]?.configId || null;
  useEffect(() => {
    setFocus((current) =>
      current.configId && questions.some((q) => q.configId === current.configId)
        ? current
        : { configId: firstConfigId, answer: null },
    );
  }, [firstConfigId, questions]);

  const question =
    questions.find((item) => item.configId === focus.configId) || questions[0];
  const shown = useMemo(
    () => visibleRows(records, { selection, focus }),
    [records, selection, focus],
  );
  const running = run.status === "running" || Boolean(creating);
  const reads = question?.reads || run.reads;
  const metaLine = [
    sourceViewTitle(unit),
    scopeWords(scope?.label) || windowLabel(run.window),
    run.total
      ? `${run.total.toLocaleString()} ${run.total === 1 ? meta.noun : meta.plural}`
      : null,
    reads && `Jev reads ${readOption(unit, reads).label.toLowerCase()}`,
  ].filter(Boolean);

  const filterLabel = selection
    ? selection.label || "Selected on the chart"
    : focus.answer && question
      ? `${answerText(focus.answer)} · ${question.question}`
      : null;
  const clearFilter = () => {
    setSelection(null);
    setFocus((current) => ({ ...current, answer: null }));
  };

  const openRow = (id) => {
    const record = records.find((item) => item.id === id);
    if (!record) return;
    if (unit === "users") {
      const userId = record.user_id || record.label;
      if (userId) navigate(userPageLink(userId));
      return;
    }
    setOpened(record);
  };

  const findPatterns =
    unit === "spans" || !shown.length || !question
      ? null
      : () => {
          const noun = shown.length === 1 ? meta.noun : meta.plural;
          const subject = shown.length === 1 ? `this ${noun}` : `these ${noun}`;
          navigate(
            insightsReportLink(projectId, {
              unit,
              population: shown.map((record) => ({
                id: record.id,
                time: record.time || "",
                user_id: record.user_id || "",
              })),
              question: focus.answer
                ? `Why did ${subject} get ${quoted(answerText(focus.answer))} for ${quoted(question.question)}?`
                : `What do ${subject} have in common?`,
              label: filterLabel || question.question,
              source: `Instant eval: ${question.question}`,
            }),
          );
        };

  const copyLink = () => {
    try {
      globalThis.navigator?.clipboard?.writeText(globalThis.location.href);
      setCopied(true);
    } catch {
      // Copying is a convenience; the address bar has the link.
    }
  };

  const showInTraces =
    unit === "traces" && question && focus.answer
      ? answerGridLink({
          projectId,
          question,
          answer: focus.answer,
          window: run.window,
        })
      : null;

  return (
    <Box sx={{ px: 3, pt: 2.5, pb: 4, maxWidth: CONTENT_WIDTH, mx: "auto" }}>
      <Stack spacing={2.5}>
        <Box>
          <Link
            component={RouterLink}
            to={sourceViewLink(projectId, unit)}
            underline="hover"
            color="text.secondary"
            variant="body2"
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
          >
            <Iconify icon="mdi:arrow-left" width={16} />
            {sourceViewTitle(unit)}
          </Link>
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="flex-start"
            sx={{ mt: 1 }}
          >
            <Typography
              variant="h4"
              component="h1"
              sx={{
                fontSize: 22,
                lineHeight: "30px",
                fontWeight: 600,
                flex: 1,
              }}
            >
              {question?.question || <Skeleton variant="text" width={420} />}
            </Typography>
            {question && (
              <Chip
                size="small"
                variant="outlined"
                label={
                  question.answerType === ANSWER_TYPES.CATEGORY
                    ? `${question.labels.length} categories`
                    : "Yes / No"
                }
                sx={{ mt: 0.5 }}
              />
            )}
          </Stack>
          {questions.length > 1 && (
            <Stack
              direction="row"
              gap={0.75}
              flexWrap="wrap"
              sx={{ mt: 1 }}
              role="tablist"
              aria-label="Questions"
            >
              {questions.map((item, index) => (
                <Chip
                  key={item.configId || index}
                  role="tab"
                  aria-selected={item.configId === question?.configId}
                  size="small"
                  label={`${index + 1}. ${item.question}`}
                  title={item.question}
                  color={
                    item.configId === question?.configId ? "primary" : "default"
                  }
                  variant={
                    item.configId === question?.configId ? "filled" : "outlined"
                  }
                  onClick={() => {
                    setFocus({ configId: item.configId, answer: null });
                    setSelection(null);
                  }}
                  sx={{ maxWidth: 320 }}
                />
              ))}
            </Stack>
          )}
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.5, fontSize: 13, fontVariantNumeric: "tabular-nums" }}
          >
            {metaLine.join(" · ")}
          </Typography>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          flexWrap="wrap"
          useFlexGap
        >
          <StatusLine run={run} unit={unit} creating={creating} />
          <Box sx={{ flex: 1 }} />
          {onRunAgain && run.status === "done" && (
            <Button
              size="small"
              variant="outlined"
              disabled={rerunning}
              startIcon={<Iconify icon="mdi:refresh" width={16} />}
              onClick={onRunAgain}
            >
              {rerunning ? "Starting…" : "Run again"}
            </Button>
          )}
          {showInTraces && (
            <Button
              size="small"
              variant="outlined"
              component={RouterLink}
              to={showInTraces}
              startIcon={<Iconify icon="mdi:filter-outline" width={16} />}
            >
              Show in Traces
            </Button>
          )}
          {(unit === "traces" || unit === "spans") &&
            scope?.filters &&
            run.status === "done" && (
              <KeepAnswering
                projectId={projectId}
                run={{ items: questions, scopeFilters: scope.filters }}
                navigate={navigate}
                unit={unit}
              />
            )}
          <MoreMenu questions={questions} onCopyLink={copyLink} />
        </Stack>
        {copied && (
          <Alert severity="success" onClose={() => setCopied(false)}>
            Link copied. It opens this run with its answers.
          </Alert>
        )}

        {run.status === "error" ? (
          <Alert
            severity="error"
            action={
              <Button size="small" onClick={run.refetch}>
                Retry
              </Button>
            }
          >
            This run couldn&apos;t be loaded. It may have been deleted, or the
            connection dropped.
          </Alert>
        ) : (
          <>
            <AskTimeline
              records={records}
              questions={questions}
              focus={focus}
              onFocus={(next) => {
                setFocus(next);
                setSelection(null);
              }}
              onSelect={(ids, label) => setSelection({ ids, label })}
              onOpen={openRow}
              running={running}
              window={run.window}
              unit={unit}
            />
            <AskPatterns
              records={records}
              questions={questions}
              focus={focus}
              onFocus={setFocus}
              onSelect={(ids, label) => setSelection({ ids, label })}
              onOpen={openRow}
              running={running}
              window={run.window}
              unit={unit}
            />
            <AskHeatmap
              records={records}
              questions={questions}
              focus={focus}
              onFocus={setFocus}
              onSelect={(ids, label) => setSelection({ ids, label })}
              onOpen={openRow}
              running={running}
              window={run.window}
              unit={unit}
            />
            <AskRowsTable
              records={shown}
              questions={questions}
              unit={unit}
              onOpen={openRow}
              filterLabel={filterLabel}
              onClearFilter={clearFilter}
              total={records.length}
            />
          </>
        )}

        {filterLabel && shown.length > 0 && (
          <SelectionBar
            count={shown.length}
            unit={unit}
            label={filterLabel}
            onFindPatterns={findPatterns}
            gridLink={rowsGridLink(projectId, unit, shown)}
            onClear={clearFilter}
          />
        )}
      </Stack>

      {opened && unit === "sessions" && (
        <Suspense fallback={null}>
          <TracesDrawer
            key={opened.id}
            open
            onClose={() => setOpened(null)}
            rowData={{ session_id: opened.id }}
          />
        </Suspense>
      )}
      {opened && (unit === "traces" || unit === "spans") && (
        <TraceDetailDrawerV2
          open
          onClose={() => setOpened(null)}
          projectId={projectId}
          traceId={unit === "spans" ? opened.trace_id : opened.id}
          initialSpanId={unit === "spans" ? opened.id : undefined}
        />
      )}
    </Box>
  );
}

AskRunView.propTypes = {
  projectId: PropTypes.string.isRequired,
  run: PropTypes.object.isRequired,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]).isRequired,
  scope: PropTypes.shape({
    label: PropTypes.string,
    filters: PropTypes.array,
  }),
  creating: PropTypes.bool,
  onRunAgain: PropTypes.func,
  rerunning: PropTypes.bool,
};
