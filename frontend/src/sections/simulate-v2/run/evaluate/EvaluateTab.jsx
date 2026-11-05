import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Button, ButtonBase, Stack, Tab, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { runAsk, whenOf } from "../../_mock/trialQuestions";
import { poolOf, resultTitleOf, runEvaluation } from "../../_mock/evalEvidence";
import AskTrialsComposer from "./AskTrialsComposer";
import EvaluationResultView from "./EvaluationResultView";
import TrialAnswersView from "./TrialAnswersView";
import UseEvaluationComposer from "./UseEvaluationComposer";

/*
  The run's Evaluate tab (evals.md, Run → Evaluate selected evidence): saved
  results on the left; on the right, either ask a question (the same Instant
  eval as Observe) or use one of the environment's evaluations. Nothing runs
  until the user asks; each result is saved with the run, with its own id,
  and never rewrites the run's task checks.
*/
const MODES = { ASK: "ask", EVALUATION: "evaluation" };
const TICK_MS = 120;

export default function EvaluateTab({
  env,
  envState,
  patch,
  runId,
  runLabel,
  tasks,
  others,
  voice,
  worldHeld,
  filter,
  selectedIds,
  onOpenTask,
}) {
  const asks = envState.trialEvals?.[runId] || [];
  const [openId, setOpenId] = useState(() => asks[0]?.id || null);
  const [composing, setComposing] = useState(() => !asks.length);
  const [mode, setMode] = useState(MODES.ASK);
  const [seed, setSeed] = useState(null);
  /* This run, and the other runs a result can reach, with what each saved. */
  const ctx = useMemo(
    () => ({ runId, runLabel, tasks, others, voice, worldHeld }),
    [runId, runLabel, tasks, others, voice, worldHeld],
  );
  const pool = useMemo(() => [...poolOf(ctx).values()], [ctx]);
  const openFromThisRun = (t) => {
    if (t?.runId === runId) onOpenTask?.(t.sourceTask);
  };
  /* The ask just started fills in a few trials at a time. */
  const [fresh, setFresh] = useState(null);

  const open = asks.find((a) => a.id === openId) || null;

  useEffect(() => {
    if (!fresh) return undefined;
    const { total } = fresh;
    if (fresh.n >= total) {
      setFresh(null);
      return undefined;
    }
    const id = setTimeout(
      () =>
        setFresh(
          (f) => f && { ...f, n: f.n + Math.max(1, Math.ceil(total / 24)) },
        ),
      TICK_MS,
    );
    return () => clearTimeout(id);
  }, [fresh]);

  /* Saved beside the run's other results; the older ones stay. */
  const save = (record) => {
    patch({
      trialEvals: {
        ...(envState.trialEvals || {}),
        [runId]: [record, ...asks],
      },
    });
    setOpenId(record.id);
    setComposing(false);
    setSeed(null);
  };
  const ask = (draft) => {
    const record = runAsk({ ...draft, ctx });
    save(record);
    setFresh({
      id: record.id,
      n: 0,
      total: Object.keys(record.answers.q1 || {}).length,
    });
  };
  const useEvaluation = (draft) => save(runEvaluation({ ...draft, ctx }));
  const begin = (next) => {
    setSeed(null);
    setMode(next);
    setComposing(true);
  };

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "224px minmax(0, 1fr)",
        columnGap: 3,
        alignItems: "stretch",
      }}
    >
      {/* ── past questions ── */}
      <Box
        sx={{
          borderRight: "1px solid",
          borderColor: "divider",
          pr: 1.5,
          /* The divider runs to the bottom of the window, however short the page. */
          minHeight: "calc(100vh - 142px)",
        }}
      >
        <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
          <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
            Results
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" width={14} />}
            onClick={() => begin(mode)}
            sx={{ color: "text.primary" }}
          >
            New
          </Button>
        </Stack>
        {!asks.length ? (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Questions and evaluations you run on this run&apos;s trials are kept
            here, each with its own id.
          </Typography>
        ) : (
          <Stack spacing={0.25}>
            {asks.map((a) => {
              const selected = !composing && a.id === openId;
              return (
                <ButtonBase
                  key={a.id}
                  onClick={() => {
                    setOpenId(a.id);
                    setComposing(false);
                  }}
                  sx={{
                    display: "block",
                    textAlign: "left",
                    width: "100%",
                    px: 1,
                    py: 0.75,
                    borderRadius: "6px",
                    bgcolor: selected ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: selected ? "action.selected" : "action.hover",
                    },
                  }}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s1",
                      fontWeight: 500,
                      color: "text.primary",
                    }}
                  >
                    {resultTitleOf(a)}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {a.questions?.length > 1
                      ? `${a.questions.length} questions · `
                      : ""}
                    {a.taskIds.length} trials · {whenOf(a.at)}
                  </Typography>
                </ButtonBase>
              );
            })}
          </Stack>
        )}
      </Box>

      {/* ── ask, or read the answers ── */}
      <Box sx={{ minWidth: 0 }}>
        {/* Two ways to evaluate saved trials; reading a result shows the mode it came from. */}
        <SegmentedTabs
          value={
            composing || !open
              ? mode
              : open.kind === "evaluation"
                ? MODES.EVALUATION
                : MODES.ASK
          }
          onChange={(_, v) => begin(v)}
          sx={{ mb: 2.5 }}
        >
          <Tab value={MODES.ASK} label="Ask a question" />
          <Tab value={MODES.EVALUATION} label="Use an evaluation" />
        </SegmentedTabs>
        {composing || !open ? (
          mode === MODES.EVALUATION ? (
            <UseEvaluationComposer
              key={`evaluation-${seed?.id || "new"}`}
              env={env}
              envState={envState}
              ctx={ctx}
              filter={filter}
              selectedIds={selectedIds}
              runLabel={runLabel}
              initial={seed?.kind === "evaluation" ? seed : null}
              onCancel={asks.length ? () => setComposing(false) : undefined}
              onRun={useEvaluation}
            />
          ) : (
            <AskTrialsComposer
              key={`ask-${seed?.id || "new"}`}
              ctx={ctx}
              filter={filter}
              selectedIds={selectedIds}
              runLabel={runLabel}
              initial={seed?.kind === "evaluation" ? null : seed}
              onCancel={asks.length ? () => setComposing(false) : undefined}
              onAsk={ask}
            />
          )
        ) : open.kind === "evaluation" ? (
          <EvaluationResultView
            key={open.id}
            result={open}
            tasks={pool}
            runId={runId}
            runLabel={runLabel}
            onRunAgain={() => {
              setSeed(open);
              setMode(MODES.EVALUATION);
              setComposing(true);
            }}
            onOpenTask={openFromThisRun}
          />
        ) : (
          <TrialAnswersView
            key={open.id}
            ask={open}
            tasks={pool}
            runId={runId}
            runLabel={runLabel}
            answering={fresh?.id === open.id ? fresh.n : undefined}
            onAskAgain={() => {
              setSeed(open);
              setMode(MODES.ASK);
              setComposing(true);
            }}
            onOpenTask={openFromThisRun}
          />
        )}
      </Box>
    </Box>
  );
}
EvaluateTab.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
  tasks: PropTypes.array.isRequired,
  others: PropTypes.array,
  voice: PropTypes.bool,
  worldHeld: PropTypes.bool,
  filter: PropTypes.object,
  selectedIds: PropTypes.array,
  onOpenTask: PropTypes.func,
};
