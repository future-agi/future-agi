import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import notify from "src/sections/common/ux/toast";
import { Button as ToastButton } from "@mui/material";
import { lastRowText, pairOf, useReviewMarks } from "../reviewMarks";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { useAnnotationLabelsList } from "src/api/annotation-labels/annotation-labels";
import {
  useReviewQueueFor,
  useSubmitResultReview,
} from "src/api/develop/review";
import { useRunColors } from "src/sections/common/Runs/runColors";
import {
  rowLabel,
  useDatasetKind,
} from "src/sections/common/Datasets/datasetKind";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import { isTextInput } from "src/utils/keyboard-guards";

// Digits and S never activate a focused button, tab or switch, so they work
// right after a click on the Review tab; only text fields and open lists
// (menus, listboxes, sliders) keep them.
const keysBlockedBy = (el) =>
  isTextInput(el) ||
  Boolean(el?.closest?.('[role="listbox"], [role="menu"], [role="slider"]'));
import {
  blindLeft,
  choiceText,
  normalizePreference,
  pairwiseOptions,
  reviewPageUrl,
} from "src/sections/develop-detail/Review/reviewModel";
import { RESULT_DRAWER_TAB_PROPTYPES } from "./ResultDrawerTabs.propTypes";
import PairwiseCard from "./PairwiseCard";
import RubricCard from "./RubricCard";
import RunCommentsSection from "src/sections/common/Collab/RunCommentsSection";
import { ROLES } from "src/utils/rolePermissionMapping";
import CorrectAnswerCard from "./CorrectAnswerCard";
import { myPairVotes, usePairSend } from "./usePairVote";
import { useNoteDraft } from "src/sections/annotations/queues/drafts/useNoteDraft";
import NoteDraftStatus from "src/sections/annotations/queues/drafts/NoteDraftStatus";
import { useEntryRefusals } from "src/sections/annotations/queues/drafts/useEntryRefusals";
import {
  labelProblems,
  problemsText,
} from "src/sections/annotations/queues/utils/label-rules";

const AUTO_KEY = "fa.review.autoAdvance";
const DEFAULT_RUBRIC = ["Correct?", "Quality"];
const ADVANCE_MS = 300;

const readAuto = () => {
  try {
    return window.localStorage.getItem(AUTO_KEY) !== "0";
  } catch {
    return true;
  }
};

// The viewer's own votes this session, so a row shows them again when the
// reviewer comes back to it (V2 answers with the saved state too). HO R5:
// the pairwise map and send live in usePairVote (the Compare tab votes too).
const myRubricValues = new Map();

function resolveRuns(runs, baselineId, focusedRunId) {
  const list = runs ?? [];
  const base = list.find((r) => r.run_id === baselineId) ?? list[0] ?? null;
  const focused =
    list.find((r) => r.run_id === focusedRunId) ??
    list.find((r) => r.run_id !== base?.run_id) ??
    base;
  return { base, focused };
}

const pairMatches = (pref, a, b) =>
  !pref?.pair ||
  [a, b].sort().join(":") === pref.pair.split(":").sort().join(":");

// A run's existing human value per label id (R3 `human{"human:<slug>"}`).
function teamValuesFor(result, labels) {
  const human = result?.human ?? {};
  const out = {};
  labels.forEach((l) => {
    const slug = String(l.name)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "");
    const raw = human[`human:${slug}`] ?? human[l.metric_key];
    const v = raw && typeof raw === "object" ? raw.value ?? raw.avg : raw;
    if (typeof v === "number") out[l.id] = v;
  });
  return out;
}

/**
 * Review tab (L7, §3.11): one card at a time. Two runs in view → the
 * pairwise card (baseline run vs the focused candidate); one run → the
 * rubric card; `Pairwise | Rubric` switches. Votes save through V2 and,
 * with auto-advance on, move to the next row of the filtered, sorted set.
 */
const ReviewTab = ({
  datasetId,
  row,
  runs,
  baselineId,
  focusedRunId,
  blind,
  onNext,
  rowIds,
  onClose,
  onReviewKind,
}) => {
  const { base, focused } = resolveRuns(runs, baselineId, focusedRunId);
  const canPair = Boolean(base && focused && focused.run_id !== base.run_id);
  // The reviewer's pick wins; otherwise two runs in view mean pairwise.
  const [pickedKind, setKind] = useState(null);
  const kind = pickedKind ?? (canPair ? "pairwise" : "rubric");
  const isPair = kind === "pairwise" && canPair;
  // integration (RV-09): the drawer locks its header and other tabs while the
  // pairwise card is blind; it needs to know which card is open.
  useEffect(() => {
    onReviewKind?.(isPair ? "pairwise" : "rubric");
  }, [isPair, onReviewKind]);
  const blindPair = Boolean(blind) && isPair;
  const [searchParams, setSearchParams] = useSearchParams();
  // iterate r1 G3 (P6): the row the URL names; until the drawer shows it,
  // a vote waits (it never lands on the row being left).
  const urlRow = searchParams.get("row");
  const location = useLocation();
  const navigate = useNavigate();
  const { user, role } = useAuthContext();
  // Viewers may review but not correct (W4 C2: the viewer / member split).
  const viewerRole = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const [mode] = useValueViewMode();
  const colorMap = useRunColors(
    (runs ?? []).map((r) => r.run_id),
    base?.run_id,
  );
  const exampleId = row?.example_id;
  const A = base?.run_id;
  const B = focused?.run_id;

  // ------------------------------------------------------------ labels
  const labelsQ = useAnnotationLabelsList({ search: "", limit: 100 });
  const allLabels = useMemo(
    () => (labelsQ.data?.results ?? []).filter((l) => !l.archived),
    [labelsQ.data],
  );
  const pairLabel = allLabels.find((l) => l.settings?.kind === "pairwise");
  const rubricRun = isPair ? null : focused ?? base;
  // The answer the comments are on: B in pairwise, else the rubric's run.
  const commentRun = isPair ? focused : rubricRun;
  const { queue: pairQueue } = useReviewQueueFor(datasetId, {
    mode: "pairwise",
    runA: A,
    runB: B,
  });
  const { queue: rubricQueue } = useReviewQueueFor(datasetId, {
    mode: "rubric",
    runId: rubricRun?.run_id,
  });
  const rubricLabels = useMemo(() => {
    const ids = (rubricQueue?.labels ?? []).map((l) => l.label_id ?? l.id);
    const fromQueue = ids
      .map((id) => allLabels.find((l) => l.id === id))
      .filter(Boolean);
    if (fromQueue.length) return fromQueue;
    return DEFAULT_RUBRIC.map((name) =>
      allLabels.find((l) => l.name === name),
    ).filter(Boolean);
  }, [rubricQueue, allLabels]);

  // ------------------------------------------------------------ state
  const submit = useSubmitResultReview(datasetId);
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const rowName = rowLabel(datasetKind, row?.row_order);
  const [autoAdvance, setAutoAdvance] = useState(readAuto);
  const [comment, setComment] = useState("");
  const [savedAt, setSavedAt] = useState(null);
  const [voteVersion, setVoteVersion] = useState(0);
  const pairKey = `${A}|${B}|${exampleId}`;
  const rubricKey = `${rubricRun?.run_id}|${exampleId}`;
  const [rubric, setRubric] = useState({ values: {}, notes: "" });
  const [rubricSaved, setRubricSaved] = useState(false);
  // KH B1: the one review rule; a refused entry still in a field blocks too.
  const rubricRefusals = useEntryRefusals();
  const [rubricErrors, setRubricErrors] = useState(() => new Set());
  // KH B2: rubric notes save as you type (one rubric queue per run, so the
  // run scopes the key); the server's copy is the last saved note.
  const rubricDraft = useNoteDraft({
    queueId: rubricRun ? `run:${rubricRun.run_id}` : null,
    itemId: exampleId,
    userId: user?.id,
    enabled: !isPair,
    itemNotes: rubric.notes,
    server: { itemNotes: myRubricValues.get(rubricKey)?.notes ?? "" },
    onRestore: ({ itemNotes }) =>
      setRubric((prev) => ({ ...prev, notes: itemNotes })),
  });
  const advanceTimer = useRef(null);

  useEffect(() => {
    const mine = myPairVotes.get(pairKey);
    setComment(mine?.notes ?? "");
    setSavedAt(mine ? mine.at : null);
  }, [pairKey]);
  useEffect(() => {
    const saved = myRubricValues.get(rubricKey);
    setRubric(saved ? { ...saved } : { values: {}, notes: "" });
    setRubricSaved(Boolean(saved));
    setRubricErrors(new Set());
  }, [rubricKey]);
  useEffect(() => () => window.clearTimeout(advanceTimer.current), []);

  const myChoice = myPairVotes.get(pairKey)?.choice ?? null;
  // voteVersion re-renders after the module-level cache changes.
  void voteVersion;

  const preference = useMemo(
    () =>
      normalizePreference(
        pairMatches(row?.preference, A, B) ? row?.preference : null,
        A,
        B,
      ),
    [row?.preference, A, B],
  );
  const left = blind ? blindLeft(`${exampleId}|${user?.id ?? "me"}`) : "A";

  const advance = useCallback(() => {
    window.clearTimeout(advanceTimer.current);
    advanceTimer.current = window.setTimeout(() => onNext?.(), ADVANCE_MS);
  }, [onNext]);

  // P6: votes this session (one toast, updated in place) and the queue end.
  const votesCast = useRef(0);
  const lastVote = useRef(null);
  // Keys pressed while the next row opens, in order (one per row).
  const pendingVote = useRef([]);
  const [queueDone, setQueueDone] = useState(false);
  const settled = Boolean(exampleId) && exampleId === urlRow;
  useEffect(() => {
    // A row other than the last one: back to voting.
    if (onNext) setQueueDone(false);
  }, [exampleId, onNext]);

  const sendPair = usePairSend({
    datasetId,
    A,
    B,
    exampleId,
    pairLabel,
    blind,
    left,
    submit,
    onSaved: useCallback(
      (exId) => {
        if (exId === exampleId) setSavedAt(Date.now());
      },
      [exampleId],
    ),
    onFailed: useCallback(() => {
      setVoteVersion((v) => v + 1);
      notify(`Couldn't save the vote on that row. Vote again.`, {
        key: "review-votes",
        variant: "error",
      });
    }, []),
  });

  const undoLast = useCallback(() => {
    const last = lastVote.current;
    if (!last) return;
    lastVote.current = null;
    votesCast.current = Math.max(0, votesCast.current - 1);
    myPairVotes.delete(last.key);
    useReviewMarks.getState().mark(last.exampleId, null);
    setVoteVersion((v) => v + 1);
    sendPair(null, { notes: "", andAdvance: false, exId: last.exampleId });
    setQueueDone(false);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("row", last.exampleId);
        return next;
      },
      { replace: true },
    );
  }, [sendPair, setSearchParams]);

  // IT-R5 G3 (P3): `Last row in Worse (9) · all reviewed [Review Better
  // (13) next]` — on the last vote and on any vote key after it.
  const lastRowToast = useCallback(() => {
    const { queue, marks } = useReviewMarks.getState();
    const next = queue?.next;
    notify(lastRowText(queue, marks, queue?.rowIds), {
      key: "review-votes",
      variant: "info",
      ...(next
        ? {
            action: () => (
              <ToastButton
                size="small"
                color="inherit"
                onClick={() => next.onGo?.()}
                data-testid="review-next-filter"
                sx={{ typography: "s2", whiteSpace: "nowrap" }}
              >
                {`Review ${next.label} next`}
              </ToastButton>
            ),
          }
        : {}),
    });
  }, []);

  // P6: the vote is recorded and the next row opens at once (no wait for
  // the save); one toast counts the session's votes and offers Undo.
  const choose = useCallback(
    (choice) => {
      if (!settled) {
        pendingVote.current.push(choice);
        return;
      }
      myPairVotes.set(pairKey, { choice, notes: comment, at: Date.now() });
      setVoteVersion((v) => v + 1);
      // IT-R5 G3 (P3): the grid shows the vote by the row number.
      useReviewMarks
        .getState()
        .mark(exampleId, choice, Boolean(blind), pairOf(A, B));
      sendPair(choice, { notes: comment, andAdvance: true });
      votesCast.current += 1;
      lastVote.current = { key: pairKey, exampleId };
      const text = blind
        ? choice === "tie"
          ? "Tie"
          : `Response ${choice === left ? 1 : 2} is better`
        : choiceText(choice, {
            labelA: base?.label,
            labelB: focused?.label,
          });
      const n = votesCast.current;
      if (!onNext && autoAdvance) {
        // IT-R5 G3 (P3): the last row of the filter says so.
        lastRowToast();
        setQueueDone(true);
        return;
      }
      notify(`Saved ${n} ${n === 1 ? "vote" : "votes"} · ${rowName}: ${text}`, {
        key: "review-votes",
        variant: "success",
        undo: { onUndo: undoLast },
      });
      if (!autoAdvance) return;
      if (onNext) onNext();
      else setQueueDone(true);
    },
    [
      settled,
      pairKey,
      comment,
      sendPair,
      exampleId,
      A,
      B,
      blind,
      left,
      base?.label,
      focused?.label,
      rowName,
      undoLast,
      autoAdvance,
      onNext,
      lastRowToast,
    ],
  );
  // A vote pressed while the next row was opening lands on that row (the
  // next one waits for the row after).
  useEffect(() => {
    if (!settled || !pendingVote.current.length) return;
    choose(pendingVote.current.shift());
  }, [settled, choose]);

  const clearChoice = useCallback(() => {
    myPairVotes.delete(pairKey);
    useReviewMarks.getState().mark(exampleId, null);
    setVoteVersion((v) => v + 1);
    setSavedAt(null);
    sendPair(null, { notes: "", andAdvance: false });
  }, [pairKey, sendPair, exampleId]);

  const commitComment = useCallback(() => {
    const mine = myPairVotes.get(pairKey);
    if (!mine || mine.notes === comment) return;
    myPairVotes.set(pairKey, { ...mine, notes: comment });
    sendPair(mine.choice, { notes: comment, andAdvance: false });
  }, [pairKey, comment, sendPair]);

  const submitRubric = useCallback(() => {
    if (!rubricRun || !exampleId) return;
    const values = rubricLabels
      .filter((l) => rubric.values[l.id] != null)
      .map((l) => ({ label_id: l.id, value: rubric.values[l.id] }));
    const problems = labelProblems(
      rubricLabels,
      rubric.values,
      rubricRefusals.pending(),
    );
    if (problems.length) {
      notify(problemsText(problems), {
        variant: "warning",
        key: "rubric-refused",
      });
      setRubricErrors(new Set(problems.map((p) => p.label_id)));
      return;
    }
    submit.mutate(
      {
        example_id: exampleId,
        mode: "rubric",
        run_id: rubricRun.run_id,
        values,
        notes: rubric.notes,
      },
      {
        onSuccess: () => {
          myRubricValues.set(rubricKey, { ...rubric });
          setRubricSaved(true);
          advance();
        },
      },
    );
  }, [
    rubricRun,
    exampleId,
    rubricLabels,
    rubric,
    submit,
    rubricKey,
    advance,
    rubricRefusals,
  ]);

  // ------------------------------------------------------------ keys
  const options = useMemo(
    () =>
      pairwiseOptions({
        left,
        blind,
        labelA: base?.label,
        labelB: focused?.label,
      }),
    [left, blind, base?.label, focused?.label],
  );
  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      if (
        document.querySelector(
          ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root",
        )
      )
        return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "Enter") {
        if (!isPair) {
          e.preventDefault();
          submitRubric();
        }
        return;
      }
      if (mod || e.altKey || keysBlockedBy(document.activeElement)) return;
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        // Skip; on the last row, the end-of-queue summary.
        if (onNext) onNext();
        else if (isPair) setQueueDone(true);
        return;
      }
      if (!isPair) return;
      const opt = options.find((o) => o.key === e.key);
      if (opt && queueDone) {
        // The 10th key on a 9-row filter: say so, never a silent drop.
        e.preventDefault();
        lastRowToast();
      } else if (opt) {
        e.preventDefault();
        choose(opt.choice);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isPair, options, choose, submitRubric, onNext, queueDone, lastRowToast]);

  // P6: end of the queue — what the votes say, and the next step.
  const tally = useMemo(() => {
    void voteVersion;
    const ids = rowIds?.length ? rowIds : exampleId ? [exampleId] : [];
    const out = {
      total: ids.length,
      better: 0,
      worse: 0,
      same: 0,
      skipped: [],
    };
    ids.forEach((id) => {
      const c = myPairVotes.get(`${A}|${B}|${id}`)?.choice;
      // Choices are sides: "B" = the candidate is better, "A" = the baseline.
      if (c == null) out.skipped.push(id);
      else if (c === "B") out.better += 1;
      else if (c === "A") out.worse += 1;
      else out.same += 1;
    });
    return out;
  }, [rowIds, exampleId, A, B, voteVersion]);

  // ------------------------------------------------------------ render
  if (!row || !base) return null;
  const setBlind = (on) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (on) next.set("blind", "1");
        else next.delete("blind");
        return next;
      },
      { replace: true },
    );
  const queue = isPair ? pairQueue : rubricQueue;
  const here = `${location.pathname}${location.search}`;

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" gap={1}>
        {canPair ? (
          <ToggleButtonGroup
            exclusive
            size="small"
            value={isPair ? "pairwise" : "rubric"}
            onChange={(_, v) => v && setKind(v)}
            aria-label="Review type"
          >
            <ToggleButton
              value="pairwise"
              sx={{ px: 1.5, py: 0.25, typography: "s2" }}
            >
              Pairwise
            </ToggleButton>
            <ToggleButton
              value="rubric"
              sx={{ px: 1.5, py: 0.25, typography: "s2" }}
            >
              Rubric
            </ToggleButton>
          </ToggleButtonGroup>
        ) : (
          <Typography typography="s2" color="text.secondary">
            Rubric review of {focused?.label ?? base.label}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {queue ? (
          <Link
            component="button"
            type="button"
            typography="s2"
            underline="hover"
            onClick={() =>
              navigate(reviewPageUrl(datasetId, queue.id, { from: here }))
            }
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
          >
            <Iconify icon="mdi:format-list-checks" width={15} />
            Review in queue
            {Number.isFinite(queue.item_count)
              ? ` (${queue.completed_count ?? 0}/${queue.item_count})`
              : ""}
          </Link>
        ) : null}
      </Stack>

      {isPair && queueDone ? (
        <Stack
          spacing={1.25}
          data-testid="review-queue-done"
          sx={{
            p: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Stack direction="row" alignItems="center" gap={1}>
            <Iconify
              icon="mdi:check-circle"
              width={20}
              sx={{ color: tally.skipped.length ? "amber.500" : "green.600" }}
            />
            <Typography typography="s1" fontWeight={600}>
              {`${
                tally.skipped.length
                  ? `${tally.total - tally.skipped.length} of ${tally.total} reviewed`
                  : `All ${tally.total} reviewed`
              } · ${tally.better} better · ${tally.worse} worse · ${tally.same} same`}
            </Typography>
          </Stack>
          <Typography typography="s2" color="text.secondary">
            {blindPair
              ? "Your votes on the rows in this view (Blind: the sides were shuffled on each row)."
              : `Better / worse = ${focused?.label ?? "the candidate"} vs ${base?.label ?? "the baseline"}, your votes on the rows in this view.`}
          </Typography>
          <Stack direction="row" gap={1} flexWrap="wrap">
            {tally.skipped.length ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                data-testid="review-skipped"
                onClick={() => {
                  setQueueDone(false);
                  setSearchParams(
                    (prev) => {
                      const next = new URLSearchParams(prev);
                      next.set("row", tally.skipped[0]);
                      return next;
                    },
                    { replace: true },
                  );
                }}
              >
                {`Review the ${tally.skipped.length} skipped`}
              </Button>
            ) : null}
            <Button
              size="small"
              variant={tally.skipped.length ? "outlined" : "contained"}
              color="primary"
              onClick={() => onClose?.()}
              data-testid="review-back"
            >
              Back to results
            </Button>
          </Stack>
        </Stack>
      ) : isPair ? (
        <PairwiseCard
          runA={base}
          runB={focused}
          resultA={row.results?.[A]}
          resultB={row.results?.[B]}
          colors={{ a: colorMap[A]?.solid, b: colorMap[B]?.solid }}
          blind={Boolean(blind)}
          onBlindChange={setBlind}
          left={left}
          preference={preference}
          myChoice={myChoice}
          saving={submit.isPending}
          onChoose={choose}
          onClear={clearChoice}
          comment={comment}
          onCommentChange={setComment}
          onCommentCommit={commitComment}
          autoAdvance={autoAdvance}
          onAutoAdvanceChange={(on) => {
            setAutoAdvance(on);
            try {
              window.localStorage.setItem(AUTO_KEY, on ? "1" : "0");
            } catch {
              /* per-viewer convenience */
            }
          }}
          mode={mode}
          savedAt={savedAt}
        />
      ) : rubricRun ? (
        <RubricCard
          run={rubricRun}
          result={row.results?.[rubricRun.run_id]}
          color={colorMap[rubricRun.run_id]?.solid}
          labels={rubricLabels}
          labelsLoading={labelsQ.isLoading}
          values={rubric.values}
          onValueChange={(labelId, value) => {
            setRubricSaved(false);
            setRubricErrors((prev) => {
              const next = new Set(prev);
              next.delete(labelId);
              return next;
            });
            setRubric((prev) => ({
              ...prev,
              values: { ...prev.values, [labelId]: value },
            }));
          }}
          notes={rubric.notes}
          onNotesChange={(notes) => setRubric((prev) => ({ ...prev, notes }))}
          teamValues={teamValuesFor(
            row.results?.[rubricRun.run_id],
            rubricLabels,
          )}
          saving={submit.isPending}
          onSubmit={submitRubric}
          onSkip={() => onNext?.()}
          mode={mode}
          queueName={rubricQueue?.name}
          saved={rubricSaved}
          notesFooter={<NoteDraftStatus draft={rubricDraft} />}
          entryRefFor={rubricRefusals.refFor}
          errorIds={rubricErrors}
        />
      ) : null}
      {/* W4 §9 (M6, Lane C): correct the answer under review; "Also set as
          expected" suggests it to the dataset. Viewers can't correct. */}
      {/* integration (RV-09): both name the run under review; hidden while blind. */}
      {exampleId && commentRun && !blindPair ? (
        <CorrectAnswerCard
          key={`${exampleId}|${commentRun.run_id}`}
          datasetId={datasetId}
          exampleId={exampleId}
          run={commentRun}
          baseRunId={isPair ? base?.run_id : null}
          pairwise={isPair}
          output={row.results?.[commentRun.run_id]?.output}
          canCorrect={!viewerRole}
        />
      ) : null}
      {/* W4 §9 (M6): comments on the answer under review, keyed (row, run). */}
      {exampleId && commentRun && !blindPair ? (
        <RunCommentsSection
          datasetId={datasetId}
          rowId={exampleId}
          runId={commentRun.run_id}
          runLabel={commentRun.label}
          traceId={row.results?.[commentRun.run_id]?.trace_ids?.[0]}
        />
      ) : null}
    </Stack>
  );
};

ReviewTab.propTypes = {
  ...RESULT_DRAWER_TAB_PROPTYPES,
  /** P6: the rows in view, in order (the end-of-queue tally). */
  rowIds: PropTypes.arrayOf(PropTypes.string),
  onClose: PropTypes.func,
  /** integration (RV-09): `pairwise` | `rubric`, the card in view. */
  onReviewKind: PropTypes.func,
};

export default ReviewTab;
