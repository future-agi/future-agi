import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { UNITS } from "src/sections/projects/observePopulation";
import {
  ASK_NEW_PARAM,
  ASK_SEED_PARAM,
  readHandoff,
} from "src/sections/projects/observeHandoff";
import {
  HISTORY_KEY,
  startAsk,
  useInstantEvalHistory,
  useInstantEvals,
} from "../useInstantEval";
import { answerLabels } from "../instantEvalPayloads";
import AskComposer from "./AskComposer";
import AskRail from "./AskRail";
import AskRunView from "./AskRunView";
import { useAskRun } from "./useAskRun";
import { lastWeekScope } from "./askScope";
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
}) {
  const run = useAskRun({
    projectId,
    taskId,
    unit,
    seedItems: launch?.items,
    questions: launch?.questions,
    startedAt: launch?.startedAt,
    window: launch?.window,
  });
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
        run.status === "done" && run.questions.every((q) => q.configId)
          ? () => onRunAgain(run)
          : undefined
      }
      rerunning={rerunning}
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
};

function Welcome({ onNew }) {
  return (
    <Box sx={{ px: 3, pt: 6, maxWidth: 640, mx: "auto" }}>
      <Typography
        variant="h4"
        component="h1"
        sx={{ fontSize: 22, lineHeight: "30px", fontWeight: 600 }}
      >
        Ask Jev a question about every row
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        Open Traces, Sessions or Users, narrow the view to the rows you care
        about, then choose Evaluate → Ask a question. Jev answers for each row
        in seconds, and the answers land on a timeline where the patterns show.
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
  // A new question without rows handed over starts from the past week.
  const lastWeek = useMemo(() => lastWeekScope(unit), [unit]);
  const seed = handedOff || lastWeek;

  // Runs started on this page, by task id: the rows handed over and the
  // scope, so the timeline has every square from the start.
  const [launches, setLaunches] = useState({});
  const [pending, setPending] = useState(null);
  const [startError, setStartError] = useState(null);
  const [rerunning, setRerunning] = useState(false);

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

  const launchRun = async ({
    questions,
    existingItems,
    reads,
    items,
    filters,
    handedOver,
    window,
    label,
  }) => {
    const startedAt = new Date().toISOString();
    const shown = {
      items,
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
      const started = await startAsk({
        projectId: observeId,
        unit,
        questions,
        existingItems,
        reads,
        items,
        filters,
        handedOver,
      });
      if (started.empty) {
        setPending(null);
        setStartError(started.empty);
        return;
      }
      setLaunches((current) => ({
        ...current,
        [started.taskId]: {
          ...shown,
          items: started.items,
          questions: started.questions,
        },
      }));
      setPending(null);
      refreshHistory();
      // Replace the new-question entry, so Back from the run skips it.
      go(
        { [ASK_RUN_PARAM]: started.taskId, [ASK_UNIT_PARAM]: unit },
        { replace: isNew },
      );
    } catch (error) {
      setPending(null);
      setStartError(
        getSafeActionErrorMessage(
          error,
          "Jev couldn't start. Check the question and try again.",
        ),
      );
    }
  };

  const runAgain = async (run) => {
    setRerunning(true);
    try {
      await launchRun({
        existingItems: run.questions,
        items: run.records.map((record) => ({
          id: record.id,
          time: record.time,
          user_id: record.user_id,
          trace_id: record.trace_id,
        })),
        filters: launches[runId]?.filters || [],
        handedOver: true,
        window: run.window,
        label: launches[runId]?.label,
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
          onChange={(_, next) => next && newQuestion(next)}
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
        key={`${seedKey || "own"}-${unit}`}
        projectId={observeId}
        unit={unit}
        seed={seed}
        starting={Boolean(pending)}
        error={startError}
        onAsk={(ask) => launchRun({ ...ask, label: seed?.label })}
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
      />
    );
  } else if (!latest.isLoading && !latest.runs.length) {
    content = <Welcome onNew={newQuestion} />;
  } else {
    content = null;
  }

  return (
    <Box sx={{ display: "flex", height: "100%", minHeight: 0 }}>
      <AskRail
        projectId={observeId}
        configsById={configsById}
        selectedId={isNew ? null : runId}
        collapsed={collapsed}
        onToggle={toggleRail}
        onNew={() => newQuestion()}
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
