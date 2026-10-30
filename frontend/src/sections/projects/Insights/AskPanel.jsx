import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputBase,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { UNITS } from "src/sections/projects/observePopulation";
import {
  VIEWER_CANT_RUN,
  useCanStartRuns,
} from "src/sections/projects/runPermission";
import { CitationChip, EvidenceQuote } from "./Citation";
import {
  askRun,
  actionCreditsBlocked,
  insightsCreditRefusal,
  insightsErrorMessage,
  useActionCredits,
} from "./insightsApi";
import { assignAskRefs } from "./insightsCitations";
import { categoriesFor } from "./insightsDistribution";
import { formatCount, formatSpan } from "./insightsFormat";
import { useElapsedSeconds } from "./insightsHooks";
import {
  ASK_INTERRUPTED,
  ASK_QUESTION_MAX,
  askHistory,
  normalizeAnswer,
  readAskThread,
  settleAskTurn,
  suggestedQuestions,
  writeAskThread,
} from "./insightsReport";
import { useReport } from "./reportContext";
import { CategoryLink, ErrorText } from "./parts";
import ActionCreditNote from "./ActionCreditNote";

// `line` keeps the chips on one row, each shrinking to fit.
function Chips({ items, onPick, disabled, line }) {
  return (
    <Stack
      direction="row"
      useFlexGap
      flexWrap={line ? "nowrap" : "wrap"}
      gap={0.75}
      sx={{ minWidth: 0 }}
    >
      {items.map((text) => (
        <Box
          key={text}
          component="button"
          type="button"
          disabled={disabled}
          onClick={() => onPick(text)}
          sx={{
            px: 1.25,
            py: 0.375,
            minWidth: 0,
            flexShrink: line ? 1 : 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 4,
            bgcolor: "background.paper",
            color: "text.primary",
            font: "inherit",
            fontSize: 12,
            cursor: disabled ? "default" : "pointer",
            opacity: disabled ? 0.6 : 1,
            "&:hover": disabled ? undefined : { borderColor: "text.disabled" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: 1,
            },
          }}
        >
          {text}
        </Box>
      ))}
    </Stack>
  );
}

Chips.propTypes = {
  items: PropTypes.array.isRequired,
  onPick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  line: PropTypes.bool,
};

// An answer in progress: what it is reading, for how long, and that the
// composer stays free for the next question.
function Thinking({ startedAt, reading, hint }) {
  const seconds = useElapsedSeconds(startedAt, true);
  return (
    <Stack spacing={0.75} sx={{ maxWidth: 520 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <CircularProgress size={12} thickness={5} />
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          Reading {reading}…
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {formatSpan(seconds)}
        </Typography>
      </Stack>
      <LinearProgress
        aria-label="Answering"
        sx={{ height: 2, borderRadius: 1 }}
      />
      {hint && (
        <Typography variant="caption" color="text.secondary">
          Answers take 15–60 seconds and are checked against the report. You can
          ask another question meanwhile.
        </Typography>
      )}
    </Stack>
  );
}

Thinking.propTypes = {
  startedAt: PropTypes.number,
  reading: PropTypes.string.isRequired,
  hint: PropTypes.bool,
};

function Answer({ turn, refs, reading, hint, onFollowUp, onRetry, disabled }) {
  const report = useReport();
  return (
    <Box component="li" data-turn sx={{ py: 1.5 }}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon="mdi:account-circle-outline"
          width={18}
          sx={{ color: "text.secondary", mt: "1px", flexShrink: 0 }}
        />
        <Typography variant="body2" fontWeight={600} sx={{ fontSize: 13 }}>
          {turn.question}
        </Typography>
      </Stack>
      <Box sx={{ pl: 3.25, mt: 0.75 }}>
        {turn.status === "busy" ? (
          <Thinking startedAt={turn.startedAt} reading={reading} hint={hint} />
        ) : turn.status === "error" ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography
              variant="body2"
              color="error.main"
              sx={{ fontSize: 13 }}
            >
              <ErrorText message={turn.error} billing={turn.billing} />
            </Typography>
            <Button
              size="small"
              color="inherit"
              onClick={onRetry}
              disabled={disabled}
            >
              Retry
            </Button>
          </Stack>
        ) : (
          <Stack spacing={1}>
            {(turn.answer || []).map((paragraph, index) => {
              const categories = categoriesFor(
                paragraph.category_ids,
                report.categoriesById,
              );
              return (
                <Box key={index}>
                  <Typography
                    variant="body2"
                    component="p"
                    sx={{ m: 0, fontSize: 13, lineHeight: "21px" }}
                  >
                    {paragraph.text}
                    {paragraph.unit_ids.length > 0 && (
                      <Box
                        component="span"
                        sx={{ whiteSpace: "nowrap", ml: 0.25 }}
                      >
                        {paragraph.unit_ids.map((unitId) => (
                          <CitationChip
                            key={unitId}
                            n={refs.get(unitId)}
                            unitId={unitId}
                          />
                        ))}
                      </Box>
                    )}
                  </Typography>
                  {paragraph.evidence.slice(0, 1).map((e) => (
                    <EvidenceQuote
                      key={e.quote}
                      quote={e.quote}
                      dense
                      sx={{ mt: 0.75 }}
                    />
                  ))}
                  {categories.length > 0 && (
                    <Stack
                      direction="row"
                      spacing={0.5}
                      useFlexGap
                      flexWrap="wrap"
                      sx={{ mt: 0.75 }}
                    >
                      {categories.map((category) => (
                        <CategoryLink
                          key={category.id}
                          category={category}
                          color={report.colorOf(category.id)}
                          onClick={report.openCategory}
                        />
                      ))}
                    </Stack>
                  )}
                </Box>
              );
            })}
            {!turn.answer?.length && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ fontSize: 13 }}
              >
                No answer came back. Try rephrasing the question.
              </Typography>
            )}
            {turn.dropped > 0 && (
              <Typography variant="caption" color="text.secondary">
                {turn.dropped === 1
                  ? "1 statement that did not check out against the report was left out."
                  : `${turn.dropped} statements that did not check out against the report were left out.`}
              </Typography>
            )}
            {turn.followUps?.length > 0 && (
              <Chips
                items={turn.followUps}
                onPick={onFollowUp}
                disabled={disabled}
              />
            )}
          </Stack>
        )}
      </Box>
    </Box>
  );
}

Answer.propTypes = {
  turn: PropTypes.object.isRequired,
  refs: PropTypes.instanceOf(Map).isRequired,
  reading: PropTypes.string.isRequired,
  hint: PropTypes.bool,
  onFollowUp: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

// Answers still on their way, by run and turn: the panel unmounts when a
// category opens, and its questions keep going (the server answers and
// charges them). A panel mounted again picks up the ones it shows as busy.
const inflight = new Map();

/**
 * "Ask anything about these conversations": a composer pinned to the bottom
 * of the report, with the thread above it. Answers cite examples and
 * categories the same way the findings do. The thread lives for this browser
 * session, per run.
 */
export default function AskPanel({ run, lens }) {
  const report = useReport();
  const runId = run.id;
  const queryClient = useQueryClient();
  const canRun = useCanStartRuns();
  const credits = useActionCredits(run.project_id, "ask");
  // A question costs credits, and the server refuses a Viewer one.
  const blocked = !canRun || actionCreditsBlocked(credits);
  const [thread, setThread] = useState(() => readAskThread(runId));
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const controllers = useRef(new Map());
  const threadList = useRef(null);
  const busyTurns = thread.filter((turn) => turn.status === "busy");
  const busy = busyTurns.length;
  const lastBusyId = busyTurns[busy - 1]?.id;
  const analysed = run.result?.totals?.count ?? run.analysed_count ?? 0;
  const noun =
    run.unit === "sessions"
      ? analysed === 1
        ? "conversation"
        : "conversations"
      : (UNITS[run.unit] || UNITS.traces)[analysed === 1 ? "noun" : "plural"];
  const reading = `${formatCount(analysed)} ${noun}`;
  const suggestions = useMemo(
    () =>
      suggestedQuestions({
        unit: run.unit,
        lens,
        hasPrevious: Boolean(run.result?.changes),
      }),
    [run.unit, lens, run.result?.changes],
  );
  const refs = useMemo(
    () =>
      assignAskRefs(
        thread.flatMap((turn) => turn.answer || []),
        report.refIndex,
      ),
    [thread, report.refIndex],
  );

  useEffect(() => writeAskThread(runId, thread), [runId, thread]);
  const mounted = useRef(true);
  const settle = (id, patch) =>
    setThread((current) =>
      current.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    );
  useEffect(() => {
    mounted.current = true;
    // Questions asked before the panel was last closed: their answers land
    // here when they come. One with nothing in flight in this page (it was
    // reloaded) was cut off, and is asked again.
    readAskThread(runId)
      .filter((turn) => turn.status === "busy")
      .forEach((turn) => {
        const answer = inflight.get(`${runId}:${turn.id}`);
        if (!answer) {
          settle(turn.id, { status: "error", error: ASK_INTERRUPTED });
          return;
        }
        answer.then((patch) => {
          if (patch && mounted.current) settle(turn.id, patch);
        });
      });
    // Leaving doesn't abort a question in flight: it is answered and charged
    // anyway, and its answer is kept with the thread.
    return () => {
      mounted.current = false;
    };
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Show the start of the latest answer, within the panel only.
  useEffect(() => {
    const list = threadList.current;
    const last = list?.querySelector("li[data-turn]:last-of-type");
    if (open && list && last) list.scrollTop = last.offsetTop - list.offsetTop;
  }, [thread, open]);

  // Questions can overlap: each turn has its own request, and the history
  // sent with one is the turns already answered.
  const ask = async (question, replaceId) => {
    const q = question.trim().slice(0, ASK_QUESTION_MAX);
    if (!q || blocked) return;
    const id =
      replaceId || `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const history = askHistory(thread.filter((turn) => turn.id !== replaceId));
    setThread((current) => {
      const turn = { id, question: q, status: "busy", startedAt: Date.now() };
      return replaceId
        ? current.map((t) => (t.id === replaceId ? turn : t))
        : [...current, turn];
    });
    setOpen(true);
    if (!replaceId) setText("");
    const own = new AbortController();
    controllers.current.get(id)?.abort();
    controllers.current.set(id, own);
    const key = `${runId}:${id}`;
    const answered = (async () => {
      try {
        const result = await askRun(
          runId,
          { question: q, history },
          own.signal,
        );
        const { answer, followUps, dropped } = normalizeAnswer(result);
        return { status: "done", answer, followUps, dropped };
      } catch (error) {
        if (own.signal.aborted) return null;
        return {
          status: "error",
          error: insightsErrorMessage(
            error,
            "The question could not be answered.",
            "Couldn't answer right now. Try again in a moment.",
          ),
          billing: Boolean(insightsCreditRefusal(error)),
        };
      }
    })();
    inflight.set(key, answered);
    const patch = await answered;
    queryClient.invalidateQueries({ queryKey: ["insights", "action-credits"] });
    if (inflight.get(key) === answered) inflight.delete(key);
    if (controllers.current.get(id) === own) controllers.current.delete(id);
    if (!patch) return;
    if (mounted.current) settle(id, patch);
    // The panel closed meanwhile: keep the answer with the stored thread.
    else settleAskTurn(runId, id, patch);
  };

  return (
    <Box
      sx={{
        position: "sticky",
        bottom: 0,
        zIndex: 3,
        // Clear of the app's floating assistant buttons at the bottom right.
        pl: 3,
        pr: 10,
        pt: 1,
        pb: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      {open && thread.length > 0 && (
        <Box
          sx={{
            mb: 1.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography variant="subtitle2" sx={{ flex: 1, fontSize: 13 }}>
              Ask about this report
            </Typography>
            <Button
              size="small"
              color="inherit"
              onClick={() => {
                controllers.current.forEach((controller) => controller.abort());
                controllers.current.clear();
                setThread([]);
                setOpen(false);
              }}
              sx={{ fontSize: 12, fontWeight: 500, color: "text.secondary" }}
            >
              Clear
            </Button>
            <Tooltip title="Hide answers" arrow>
              <IconButton
                size="small"
                aria-label="Hide answers"
                onClick={() => setOpen(false)}
              >
                <Iconify icon="mdi:chevron-down" width={18} />
              </IconButton>
            </Tooltip>
          </Stack>
          <Box
            component="ol"
            ref={threadList}
            aria-live="polite"
            sx={{
              position: "relative",
              m: 0,
              px: 1.5,
              listStyle: "none",
              maxHeight: "min(52vh, 520px)",
              overflowY: "auto",
              "& > li:not(:last-of-type)": {
                borderBottom: "1px solid",
                borderColor: "divider",
              },
            }}
          >
            {thread.map((turn) => (
              <Answer
                key={turn.id}
                turn={turn}
                refs={refs}
                reading={reading}
                // The timing note once, on the newest answer in progress.
                hint={turn.id === lastBusyId}
                onFollowUp={(question) => ask(question)}
                onRetry={() => ask(turn.question, turn.id)}
                disabled={blocked}
              />
            ))}
          </Box>
        </Box>
      )}

      <ActionCreditNote
        query={credits}
        label="Each question, including suggestions and retries"
      />
      <Box
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          ask(text);
        }}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          "&:focus-within": { borderColor: "primary.main" },
        }}
      >
        {/* Starting questions live inside the composer, on one line. */}
        {thread.length === 0 && (
          <Box sx={{ px: 1.25, pt: 1 }}>
            <Chips items={suggestions} onPick={ask} line disabled={blocked} />
          </Box>
        )}
        <Stack
          direction="row"
          alignItems="flex-end"
          spacing={1}
          sx={{ pl: 1.5, pr: 0.75, py: 0.75 }}
        >
          <Iconify
            icon="mdi:creation-outline"
            width={18}
            sx={{ color: "primary.main", mb: "7px", flexShrink: 0 }}
          />
          <InputBase
            value={text}
            onChange={(event) =>
              setText(event.target.value.slice(0, ASK_QUESTION_MAX))
            }
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                ask(text);
              }
            }}
            placeholder={
              canRun
                ? `Ask anything about these ${formatCount(analysed)} ${noun}…`
                : VIEWER_CANT_RUN
            }
            disabled={!canRun}
            multiline
            maxRows={4}
            inputProps={{ "aria-label": "Ask about this report" }}
            sx={{ flex: 1, fontSize: 14, py: 0.5 }}
          />
          {thread.length > 0 && !open && (
            <Button
              size="small"
              color="inherit"
              onClick={() => setOpen(true)}
              sx={{
                fontSize: 12,
                fontWeight: 500,
                color: "text.secondary",
                flexShrink: 0,
              }}
            >
              {busy
                ? `${busy} answering`
                : `${thread.length} ${thread.length === 1 ? "answer" : "answers"}`}
            </Button>
          )}
          <IconButton
            type="submit"
            color="primary"
            aria-label="Ask"
            disabled={!text.trim() || blocked}
            sx={{ flexShrink: 0 }}
          >
            <Iconify icon="mdi:arrow-up-circle" width={24} />
          </IconButton>
        </Stack>
      </Box>
    </Box>
  );
}

AskPanel.propTypes = {
  run: PropTypes.object.isRequired,
  lens: PropTypes.string,
};
