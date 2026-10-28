import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { paths } from "src/routes/paths";
import { actionErrorMessage } from "src/sections/projects/requestErrors";
import { UNITS } from "src/sections/projects/observePopulation";
import {
  ASK_NEW_PARAM,
  ASK_SEED_PARAM,
  readHandoff,
} from "src/sections/projects/observeHandoff";
import {
  scopeForUnit,
  scopeFromSeed,
} from "src/sections/projects/observeScope";
import {
  AskBusy,
  AskKeyMissing,
  AskNoCredits,
  AskRefused,
  AskSampleTooLarge,
  HISTORY_KEY,
  SAVED_QUESTIONS_KEY,
  startAskRun,
  useInstantEvalHistory,
  useInstantEvals,
} from "../useInstantEval";
import { answerLabels } from "../instantEvalPayloads";
import { draftsFromQuestions } from "../instantEvalUnits";
import AskComposer from "./AskComposer";
import AskRail from "./AskRail";
import AskRunView from "./AskRunView";
import { RERUNNABLE_STATES, isNotFound, useAskRun } from "./useAskRun";
import { rerunScope, seedScope } from "./askScope";
import {
  ASK_RUN_PARAM,
  ASK_UNIT_PARAM,
  OBSERVE_TAB_PARAM,
  tabForUnit,
} from "./askLinks";

// Instant eval's page. The URL is the source of truth, and every move keeps
// `tab=<the view the rows came from>` so the tab bar stays on that view:
//   ?new=1&seed=<key>&unit=<unit>   a new question about rows handed over
//   ?run=<task id>                  a run, live or past
//   (none)                          the latest run, or how to start one
const RAIL_KEY = "ask-rail-collapsed";
// Below this width the rail and the charts don't fit side by side.
const RAIL_FITS_WIDTH = 1440;
const UNIT_CHOICES = ["traces", "spans", "sessions", "users"];

const readRailCollapsed = () => {
  try {
    const saved = globalThis.localStorage?.getItem(RAIL_KEY);
    return saved == null ? null : saved === "1";
  } catch {
    return null;
  }
};

function RunPane({
  projectId,
  taskId,
  unit,
  launch,
  onRunAgain,
  onFinished,
  onUnitKnown,
  rerunning,
  runAgainConfirm,
  onCancelRunAgain,
  configsLoading = false,
  onProgress,
  evaluatorGone = () => false,
}) {
  const run = useAskRun({
    projectId,
    taskId,
    unit,
    questions: launch?.questions,
    startedAt: launch?.startedAt,
  });
  // History's card for this run counts, and says where it stands, from
  // this summary too.
  const settled = run.answered + run.failed;
  const counts = run.summary?.counts;
  useEffect(() => {
    if (taskId && counts)
      onProgress?.({
        taskId,
        settled,
        total: run.total,
        counts,
        status: run.status,
      });
  }, [taskId, settled, run.total, counts, run.status, onProgress]);
  // A run opened by its link alone learns its unit from the server, so the
  // URL (and the tab bar) can follow it.
  useEffect(() => {
    if (taskId && run.knownUnit) onUnitKnown?.(run.knownUnit);
  }, [taskId, run.knownUnit, onUnitKnown]);
  // A run seen finishing here tells History at once, not at its next poll.
  const sawRunning = useRef(false);
  useEffect(() => {
    if (run.status === "running") sawRunning.current = true;
    else if (run.status === "done" && sawRunning.current) {
      sawRunning.current = false;
      onFinished?.();
    }
  }, [run.status, onFinished]);
  return (
    <AskRunView
      projectId={projectId}
      run={run}
      unit={run.unit || unit}
      scope={launch ? { label: launch.label, filters: launch.filters } : null}
      creating={!taskId}
      onRunAgain={
        RERUNNABLE_STATES.has(run.status) &&
        run.questions.every((q) => q.configId)
          ? (options) => onRunAgain(run, options)
          : undefined
      }
      rerunning={rerunning || configsLoading}
      needsEvaluator={run.questions.some(evaluatorGone)}
      runAgainConfirm={runAgainConfirm}
      onCancelRunAgain={onCancelRunAgain}
    />
  );
}

RunPane.propTypes = {
  projectId: PropTypes.string.isRequired,
  taskId: PropTypes.string,
  unit: PropTypes.string.isRequired,
  launch: PropTypes.object,
  onRunAgain: PropTypes.func.isRequired,
  onFinished: PropTypes.func,
  onUnitKnown: PropTypes.func,
  rerunning: PropTypes.bool,
  runAgainConfirm: PropTypes.object,
  onCancelRunAgain: PropTypes.func,
  configsLoading: PropTypes.bool,
  onProgress: PropTypes.func,
  // Whether a question's saved evaluator is gone, so it is recreated.
  evaluatorGone: PropTypes.func,
};

function Welcome({ onNew }) {
  return (
    <Box sx={{ px: 3, pt: 6, maxWidth: 640, mx: "auto" }}>
      <Typography
        variant="h4"
        component="h1"
        sx={{ fontSize: 22, lineHeight: "30px", fontWeight: 600 }}
      >
        Ask Jev a question about each row
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        Open Traces, Sessions or Users, narrow the view to the rows you care
        about, then choose Evaluate → Ask a question. Jev answers for each row
        it reads, a sample of the view or every row. The first answers come in
        seconds and land on a timeline where the patterns show.
      </Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 2.5 }}>
        <Button variant="contained" onClick={() => onNew("sessions")}>
          Ask about recent sessions
        </Button>
        <Button variant="outlined" onClick={() => onNew("traces")}>
          Ask about recent traces
        </Button>
      </Stack>
    </Box>
  );
}

Welcome.propTypes = { onNew: PropTypes.func.isRequired };

export default function AskPage() {
  const { observeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get(ASK_RUN_PARAM);
  const isNew = searchParams.get(ASK_NEW_PARAM) === "1";
  const seedKey = searchParams.get(ASK_SEED_PARAM);
  const unitParam = searchParams.get(ASK_UNIT_PARAM);
  const handedOff = useMemo(() => readHandoff(seedKey), [seedKey]);
  const unit = UNITS[unitParam]
    ? unitParam
    : UNITS[handedOff?.unit]
      ? handedOff.unit
      : "sessions";
  // What a new question reads: the hand-over's date and filters (or its
  // rows), else the past week. Edits made here live in state, not the URL,
  // since every move replaces the page's params.
  const scopeKey = seedKey || "own";
  const seededScope = useMemo(
    () => seedScope(handedOff, unit),
    [handedOff, unit],
  );
  const [edited, setEdited] = useState(null); // {key, scope, notice}
  const editedHere =
    edited?.key === scopeKey && edited.scope.unit === unit ? edited : null;
  const scope = editedHere?.scope || seededScope;
  const editScope = (next, notice = null) =>
    setEdited({ key: scopeKey, scope: next, notice });

  // Runs started on this page, by task id: the rows handed over and the
  // scope, so the timeline has every square from the start.
  const [launches, setLaunches] = useState({});
  const [pending, setPending] = useState(null);
  const [startError, setStartError] = useState(null);
  const [rerunning, setRerunning] = useState(false);
  // The open run's counts, for its card in History.
  const [openProgress, setOpenProgress] = useState(null);
  // A Run again the server holds back until its credits are confirmed:
  // {taskId, rows, credits}, confirmed or dropped on that run's page.
  const [rerunConfirm, setRerunConfirm] = useState(null);
  // A new question the server held for its confirm: {args, rows, credits}.
  // Another scope, unit, sample size or read retracts it, as the composer's
  // own confirm is, so Start never launches what is no longer on screen.
  const [composerConfirm, setComposerConfirm] = useState(null);
  // Stable: the composer retracts with it whenever its sample or read
  // changes.
  const dropComposerConfirm = useCallback(() => setComposerConfirm(null), []);
  useEffect(() => setRerunConfirm(null), [runId]);
  useEffect(() => setComposerConfirm(null), [scope, unit]);
  // The questions typed in the composer, carried to the composer of another
  // unit (it mounts again) instead of being cleared.
  const typedQuestions = useRef(null);
  const [carriedQuestions, setCarriedQuestions] = useState(null);
  const [draftVersion, setDraftVersion] = useState(0);
  const onQuestionsChange = useCallback((next) => {
    if (typedQuestions.current && typedQuestions.current !== next)
      setComposerConfirm(null);
    typedQuestions.current = next;
  }, []);

  const railFits = useMediaQuery(`(min-width:${RAIL_FITS_WIDTH}px)`, {
    noSsr: true,
  });
  const [savedCollapsed, setCollapsed] = useState(readRailCollapsed);
  const collapsed = savedCollapsed ?? !railFits;
  const toggleRail = () => {
    const next = !collapsed;
    try {
      globalThis.localStorage?.setItem(RAIL_KEY, next ? "1" : "0");
    } catch {
      // The choice lasts until the page reloads.
    }
    setCollapsed(next);
  };

  // Each move replaces the page's params but keeps the tab bar on the view
  // the rows came from.
  const go = useCallback(
    (params, options) => {
      const nextUnit = params[ASK_UNIT_PARAM] || unit;
      setSearchParams(
        {
          ...params,
          [ASK_UNIT_PARAM]: nextUnit,
          [OBSERVE_TAB_PARAM]: tabForUnit(nextUnit),
        },
        options,
      );
    },
    [setSearchParams, unit],
  );

  const queryClient = useQueryClient();
  const refreshHistory = useCallback(
    () =>
      queryClient.invalidateQueries({
        queryKey: [HISTORY_KEY, observeId],
      }),
    [queryClient, observeId],
  );

  const saved = useInstantEvals(observeId);
  const configsById = useMemo(
    () => Object.fromEntries(saved.all.map((item) => [item.configId, item])),
    [saved.all],
  );
  // A question started here has its evaluator, even before the saved list
  // is read again: else a run that just finished offered "Recreate
  // question", and "Run again" after a reload.
  const evaluatorGone = (question) =>
    !configsById[question.configId] &&
    !Object.values(launches).some((launched) =>
      launched.questions?.some((item) => item.configId === question.configId),
    );

  // With nothing chosen, open the most recent run.
  const latest = useInstantEvalHistory(observeId, configsById, {
    enabled: !runId && !isNew,
  });
  const latestId = latest.runs[0]?.taskId;
  useEffect(() => {
    if (runId || isNew || !latestId) return;
    go(
      { [ASK_RUN_PARAM]: latestId, [ASK_UNIT_PARAM]: latest.runs[0].unit },
      { replace: true },
    );
  }, [runId, isNew, latestId, latest.runs, go]);

  // Keep `unit` and `tab` in the URL in step with the run's real unit, for
  // links that carry only ?run=.
  const tabParam = searchParams.get(OBSERVE_TAB_PARAM);
  const syncRunUnit = useCallback(
    (runUnit) => {
      if (unitParam === runUnit && tabParam === tabForUnit(runUnit)) return;
      go(
        { [ASK_RUN_PARAM]: runId, [ASK_UNIT_PARAM]: runUnit },
        { replace: true },
      );
    },
    [unitParam, tabParam, runId, go],
  );

  const newQuestion = (nextUnit = unit) => {
    setStartError(null);
    go({ [ASK_NEW_PARAM]: "1", [ASK_UNIT_PARAM]: nextUnit });
  };
  // A fresh question from the rail or the welcome: the past week again.
  const startOver = (nextUnit = unit) => {
    setEdited(null);
    setCarriedQuestions(null);
    newQuestion(nextUnit);
  };
  const recreateQuestions = (questions, nextUnit) => {
    setDraftVersion((version) => version + 1);
    setEdited({
      key: "own",
      scope: seedScope(null, nextUnit),
      notice:
        "The saved evaluator is no longer available. Review the question, labels, inputs and date range before creating a new evaluator.",
    });
    setCarriedQuestions(
      draftsFromQuestions(
        questions.map((question) => ({
          ...question,
          question: question.fullQuestion || question.question,
        })),
      ),
    );
    newQuestion(nextUnit);
  };
  // Another unit keeps the date; filters it can't read are dropped, and the
  // page says so.
  const switchUnit = (nextUnit) => {
    if (nextUnit === unit) return;
    const moved = scopeForUnit(scope, nextUnit);
    // newQuestion drops the hand-over's seed from the URL, so the moved scope
    // is kept under the key the page reads next ("own"), not the seed's: under
    // the seed it was ignored and the composer fell back to the past week.
    setEdited({ key: "own", scope: moved.scope, notice: moved.notice });
    setCarriedQuestions(typedQuestions.current);
    newQuestion(nextUnit);
  };

  const launchRun = async ({
    questions,
    existingItems,
    reads,
    filters,
    search,
    ids,
    sampleSize,
    confirm,
    rows,
    window,
    datePreset,
    label,
    onNeedsConfirm,
  }) => {
    const startedAt = new Date().toISOString();
    const shown = {
      questions:
        existingItems ||
        questions.map((q) => ({
          question: q.question.trim(),
          answerType: q.answerType,
          labels: answerLabels(q),
        })),
      startedAt,
      window,
      filters,
      label,
    };
    setPending(shown);
    setStartError(null);
    try {
      const started = await startAskRun({
        projectId: observeId,
        unit,
        questions,
        existingItems,
        reads,
        filters,
        search,
        ids,
        sampleSize,
        confirm,
        confirmedRows: confirm ? rows : undefined,
        datePreset,
        label,
      });
      setLaunches((current) => ({
        ...current,
        [started.taskId]: { ...shown, questions: started.questions },
      }));
      setPending(null);
      refreshHistory();
      // New questions were saved as evaluators.
      queryClient.invalidateQueries({
        queryKey: [SAVED_QUESTIONS_KEY, observeId],
      });
      // Replace the new-question entry, so Back from the run skips it.
      go(
        { [ASK_RUN_PARAM]: started.taskId, [ASK_UNIT_PARAM]: unit },
        { replace: isNew },
      );
    } catch (error) {
      setPending(null);
      if (onNeedsConfirm && error?.name === "AskNeedsConfirm") {
        onNeedsConfirm(error);
        return;
      }
      setStartError(
        error instanceof AskNoCredits ? (
          <>
            {error.message}{" "}
            <Link component={RouterLink} to={paths.dashboard.settings.billing}>
              Open billing
            </Link>
          </>
        ) : error instanceof AskKeyMissing ? (
          // The questions run on Jev and the organization's TypeSafe key
          // isn't saved: the server's words, and where to add it.
          <>
            {error.message}{" "}
            <Link component={RouterLink} to={error.settingsPath}>
              Open AI Providers
            </Link>
          </>
        ) : error instanceof AskBusy ||
          error instanceof AskSampleTooLarge ||
          error instanceof AskRefused ||
          error?.name === "AskNeedsConfirm" ? (
          error.message
        ) : (
          // A fault on the server's side isn't the question's.
          actionErrorMessage(error, {
            fallback: "Jev couldn't start. Check the question and try again.",
            retry: "Couldn't start right now. Try again in a moment.",
          })
        ),
      );
    }
  };

  const askFromComposer = (args) =>
    launchRun({
      ...args,
      onNeedsConfirm: (held) =>
        setComposerConfirm({ args, rows: held.rows, credits: held.credits }),
    });

  // The same questions over the same filters and sample (the same
  // hash-ordered rows), a date preset re-anchored to now, or the same rows
  // handed over. A window moved to now can hold more rows than were
  // confirmed (or than whoever started the run confirmed), so a run past
  // the confirm size restates its credits here first, as the composer does.
  const runAgain = async (run, { confirm = false } = {}) => {
    if (run.questions.some(evaluatorGone)) {
      recreateQuestions(run.questions, run.unit);
      return;
    }
    // What the person confirmed: the server asks again if it would read more.
    const confirmedRows = confirm ? rerunConfirm?.rows : undefined;
    const asked = run.summary?.scope || {};
    const again = rerunScope(asked, {
      label: launches[runId]?.label,
      window: run.window,
    });
    setRerunConfirm(null);
    setRerunning(true);
    try {
      await launchRun({
        existingItems: run.questions,
        filters: again.filters,
        search: asked.search || undefined,
        ids: asked.ids || undefined,
        sampleSize: asked.sample_size ?? null,
        confirm,
        rows: confirmedRows ?? undefined,
        window: again.window,
        datePreset: again.datePreset,
        label: again.label,
        onNeedsConfirm: (held) =>
          setRerunConfirm({
            taskId: run.taskId,
            rows: held.rows,
            credits: held.credits,
          }),
      });
    } finally {
      setRerunning(false);
    }
  };

  // The composer stays mounted while its run starts, so a failed start
  // keeps the question as typed.
  const composer = isNew && (
    <Box
      sx={{
        px: 3,
        pt: 2.5,
        pb: 4,
        maxWidth: 1180,
        mx: "auto",
        display: pending ? "none" : "block",
      }}
    >
      {seedKey && !handedOff && (
        <Alert severity="info" sx={{ mb: 2 }}>
          The rows handed over from the other view are no longer available in
          this tab, so this question starts from the past week instead.
        </Alert>
      )}
      {!handedOff && (
        <ToggleButtonGroup
          exclusive
          size="small"
          value={unit}
          onChange={(_, next) => next && switchUnit(next)}
          aria-label="What to ask about"
          sx={{ mb: 2.5 }}
        >
          {UNIT_CHOICES.map((choice) => (
            <ToggleButton key={choice} value={choice} sx={{ px: 2 }}>
              {UNITS[choice].title}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      )}
      <AskComposer
        key={`${scopeKey}-${unit}-${draftVersion}`}
        projectId={observeId}
        unit={unit}
        seed={handedOff}
        initialQuestions={carriedQuestions}
        onQuestionsChange={onQuestionsChange}
        scope={scope}
        onScopeChange={(next) => editScope(next)}
        scopeNotice={editedHere?.notice}
        onUseView={
          scope.ids && handedOff?.filters?.length
            ? () => editScope(scopeFromSeed(handedOff, unit))
            : undefined
        }
        starting={Boolean(pending)}
        error={startError}
        // The server counts again at the start: when it would read past what
        // was confirmed (or past the confirm size the estimate didn't reach)
        // it asks, and the composer confirms its numbers.
        serverConfirm={composerConfirm}
        onServerConfirm={() => {
          const held = composerConfirm;
          setComposerConfirm(null);
          askFromComposer({ ...held.args, confirm: true, rows: held.rows });
        }}
        onServerConfirmCancel={dropComposerConfirm}
        onAsk={askFromComposer}
      />
    </Box>
  );

  let content;
  if (pending) {
    content = (
      <RunPane
        projectId={observeId}
        taskId={null}
        unit={unit}
        launch={pending}
        onRunAgain={runAgain}
      />
    );
  } else if (isNew) {
    content = null;
  } else if (runId) {
    content = (
      <RunPane
        key={runId}
        projectId={observeId}
        taskId={runId}
        unit={unit}
        launch={launches[runId]}
        onRunAgain={runAgain}
        onFinished={refreshHistory}
        onUnitKnown={syncRunUnit}
        rerunning={rerunning}
        runAgainConfirm={
          rerunConfirm?.taskId === runId ? rerunConfirm : undefined
        }
        onCancelRunAgain={() => setRerunConfirm(null)}
        configsLoading={saved.isLoading}
        onProgress={setOpenProgress}
        evaluatorGone={evaluatorGone}
      />
    );
  } else if (latest.isError) {
    content = (
      <Box sx={{ px: 3, pt: 2.5, maxWidth: 1180, mx: "auto" }}>
        <Alert severity="error">
          {isNotFound(latest.error)
            ? "This project's questions can't be opened. It was deleted, or it belongs to another organization."
            : "Past questions couldn't be loaded. Try again in a moment."}
        </Alert>
      </Box>
    );
  } else if (!latest.isLoading && !latest.runs.length) {
    content = <Welcome onNew={startOver} />;
  } else {
    content = null;
  }

  return (
    <Box sx={{ display: "flex", height: "100%", minHeight: 0 }}>
      <AskRail
        projectId={observeId}
        configsById={configsById}
        selectedId={isNew ? null : runId}
        openProgress={openProgress}
        collapsed={collapsed}
        onToggle={toggleRail}
        onNew={() => startOver()}
        onRecreate={(runItem) => recreateQuestions(runItem.items, runItem.unit)}
        onOpen={(runItem) =>
          go({
            [ASK_RUN_PARAM]: runItem.taskId,
            [ASK_UNIT_PARAM]: runItem.unit,
          })
        }
      />
      <Box sx={{ flex: 1, minWidth: 0, overflowY: "auto" }}>
        {startError && !isNew && (
          <Alert
            severity="error"
            onClose={() => setStartError(null)}
            sx={{ mx: 3, mt: 2.5 }}
          >
            {startError}
          </Alert>
        )}
        {composer}
        {content}
      </Box>
    </Box>
  );
}
