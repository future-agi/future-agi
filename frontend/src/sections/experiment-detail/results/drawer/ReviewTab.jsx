import React, { useEffect, useId, useMemo, useState } from "react";
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
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { useAnnotationLabelsList } from "src/api/annotation-labels/annotation-labels";
import { useReviewQueueFor } from "src/api/develop/review";
import { useRunColors } from "src/sections/common/Runs/runColors";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import { isTextInput } from "src/utils/keyboard-guards";

// S never activates a focused button, tab or switch, so it works right after
// a click on the Review tab; only text fields and open lists (menus,
// listboxes, sliders) keep it.
const keysBlockedBy = (el) =>
  isTextInput(el) ||
  Boolean(el?.closest?.('[role="listbox"], [role="menu"], [role="slider"]'));
import {
  blindLeft,
  normalizePreference,
  reviewPageUrl,
} from "src/sections/develop-detail/Review/reviewModel";
import { RESULT_DRAWER_TAB_PROPTYPES } from "./ResultDrawerTabs.propTypes";
import PairwiseCard from "./PairwiseCard";
import RubricCard from "./RubricCard";
import RunCommentsSection from "src/sections/common/Collab/RunCommentsSection";
import { ROLES } from "src/utils/rolePermissionMapping";
import CorrectAnswerCard from "./CorrectAnswerCard";
import { myPairVotes } from "./usePairVote";

const AUTO_KEY = "fa.review.autoAdvance";
const DEFAULT_RUBRIC = ["Correct?", "Quality"];

const readAuto = () => {
  try {
    return window.localStorage.getItem(AUTO_KEY) !== "0";
  } catch {
    return true;
  }
};

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
 * rubric card; `Pairwise | Rubric` switches. Saving reviews isn't available
 * in this release: each card's save controls stay on screen turned off, the
 * card says so once, and nothing is sent. The end-of-queue summary shows
 * that sentence in place of a count, and its Review the skipped stays on
 * screen turned off, described by it.
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
  // The drawer locks its header and other tabs while the pairwise card is
  // blind; it needs to know which card is open.
  useEffect(() => {
    onReviewKind?.(isPair ? "pairwise" : "rubric");
  }, [isPair, onReviewKind]);
  const blindPair = Boolean(blind) && isPair;
  const [, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const unavailableId = useId();
  const { user, role } = useAuthContext();
  // Viewer roles may review but not correct the answer under review.
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
  // Saving reviews isn't available in this release: the rubric's answers and
  // the pairwise comment stay on the open row and start empty on the next.
  // Auto-advance shows the setting as the browser has it and writes none.
  const [autoAdvance] = useState(readAuto);
  const [comment, setComment] = useState("");
  const pairKey = `${A}|${B}|${exampleId}`;
  const rubricKey = `${rubricRun?.run_id}|${exampleId}`;
  const [rubric, setRubric] = useState({ values: {}, notes: "" });

  useEffect(() => {
    setComment(myPairVotes.get(pairKey)?.notes ?? "");
  }, [pairKey]);
  useEffect(() => {
    setRubric({ values: {}, notes: "" });
  }, [rubricKey]);

  // A pick held in the session's map for this row shows as the person's pick;
  // nothing in the app writes the map.
  const myChoice = myPairVotes.get(pairKey)?.choice ?? null;

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

  // P6: the queue end.
  const [queueDone, setQueueDone] = useState(false);
  useEffect(() => {
    // A row other than the last one: back to the card.
    if (onNext) setQueueDone(false);
  }, [exampleId, onNext]);

  // ------------------------------------------------------------ keys
  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      if (
        document.querySelector(
          ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root",
        )
      )
        return;
      // Saving reviews isn't available in this release: ⌘↵ / Ctrl↵ and the
      // vote keys 1, 2 and 3 do nothing; S skips.
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (keysBlockedBy(document.activeElement)) return;
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        // Skip; on the last row, the end-of-queue summary.
        if (onNext) onNext();
        else if (isPair) setQueueDone(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isPair, onNext]);

  // P6: end of the queue — the picks on the rows in view, and the next step.
  const tally = useMemo(() => {
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
  }, [rowIds, exampleId, A, B]);

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
            <Typography
              id={unavailableId}
              typography="s2"
              color="text.secondary"
            >
              Saving reviews isn&apos;t available in this release.
            </Typography>
          </Stack>
          <Stack direction="row" gap={1} flexWrap="wrap">
            {tally.skipped.length ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                data-testid="review-skipped"
                disabled
                aria-describedby={unavailableId}
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
                Review the skipped
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
          comment={comment}
          onCommentChange={setComment}
          autoAdvance={autoAdvance}
          mode={mode}
        />
      ) : rubricRun ? (
        <RubricCard
          run={rubricRun}
          result={row.results?.[rubricRun.run_id]}
          color={colorMap[rubricRun.run_id]?.solid}
          labels={rubricLabels}
          labelsLoading={labelsQ.isLoading}
          values={rubric.values}
          onValueChange={(labelId, value) =>
            setRubric((prev) => ({
              ...prev,
              values: { ...prev.values, [labelId]: value },
            }))
          }
          notes={rubric.notes}
          onNotesChange={(notes) => setRubric((prev) => ({ ...prev, notes }))}
          teamValues={teamValuesFor(
            row.results?.[rubricRun.run_id],
            rubricLabels,
          )}
          onSkip={() => onNext?.()}
          mode={mode}
        />
      ) : null}
      {/* W4 §9 (M6, Lane C): correct the answer under review; "Also set as
          expected" suggests it to the dataset. Viewers can't correct. */}
      {/* Both name the run under review; hidden while blind. */}
      {exampleId && commentRun && !blindPair ? (
        <CorrectAnswerCard
          key={`${exampleId}|${commentRun.run_id}`}
          exampleId={exampleId}
          run={commentRun}
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
  /** `pairwise` | `rubric`, the card in view. */
  onReviewKind: PropTypes.func,
};

export default ReviewTab;
