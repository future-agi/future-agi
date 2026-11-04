import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { runAsk, whenOf } from "../../_mock/trialQuestions";
import AskTrialsComposer from "./AskTrialsComposer";
import TrialAnswersView from "./TrialAnswersView";

/*
  The run's Evaluate tab (evals.md, Run → Evaluate selected evidence) — the
  same Instant eval as Observe: past questions on the left, ask or read the
  answers on the right. Nothing runs until the user asks; each ask is saved
  with the run and never rewrites its task checks.
*/
const TICK_MS = 120;

export default function EvaluateTab({
  envState,
  patch,
  runId,
  runLabel,
  tasks,
  onOpenTask,
}) {
  const asks = envState.trialEvals?.[runId] || [];
  const [openId, setOpenId] = useState(() => asks[0]?.id || null);
  const [composing, setComposing] = useState(() => !asks.length);
  const [seed, setSeed] = useState(null);
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

  const ask = (draft) => {
    const record = runAsk({ ...draft, tasks });
    patch({
      trialEvals: {
        ...(envState.trialEvals || {}),
        [runId]: [record, ...asks],
      },
    });
    setOpenId(record.id);
    setComposing(false);
    setSeed(null);
    setFresh({
      id: record.id,
      n: 0,
      total: Object.keys(record.answers.q1 || {}).length,
    });
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
            Instant eval
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" width={14} />}
            onClick={() => {
              setSeed(null);
              setComposing(true);
            }}
            sx={{ color: "text.primary" }}
          >
            New question
          </Button>
        </Stack>
        {!asks.length ? (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Questions you ask about this run are kept here with their answers.
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
                    {a.questions[0].text}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {a.questions.length > 1
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
        {composing || !open ? (
          <AskTrialsComposer
            key={seed?.id || "new"}
            tasks={tasks}
            runLabel={runLabel}
            initial={seed}
            onCancel={asks.length ? () => setComposing(false) : undefined}
            onAsk={ask}
          />
        ) : (
          <TrialAnswersView
            key={open.id}
            ask={open}
            tasks={tasks}
            runLabel={runLabel}
            answering={fresh?.id === open.id ? fresh.n : undefined}
            onAskAgain={() => {
              setSeed(open);
              setComposing(true);
            }}
            onOpenTask={onOpenTask}
          />
        )}
      </Box>
    </Box>
  );
}
EvaluateTab.propTypes = {
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
  tasks: PropTypes.array.isRequired,
  onOpenTask: PropTypes.func,
};
