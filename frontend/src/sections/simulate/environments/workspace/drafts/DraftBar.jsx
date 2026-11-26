import { useSearchParams } from "react-router-dom";
import ReviewChangesFrame from "./ReviewChangesFrame";
import PropTypes from "prop-types";
import { useEffect, useId, useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";

import { ConfirmDialog } from "src/components/custom-dialog";
import { relativeTime } from "src/utils/format-time";
import {
  draftsKey,
  useDiscardDraft,
  useDrafts,
  useSaveDrafts,
} from "src/api/simulate-environments/versions";
import { refusalText } from "../evals/refusalText";
import { DRAFTS_COPY as COPY, WORKSPACE_COPY } from "../workspace.constants";
import ViewerLock from "../../components/ViewerLock";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { changeCount } from "./changeCount";
import {
  sourceGenerationPending,
  useActiveEnvironmentSourceReview,
  useEnvironmentSourceGeneration,
  useEnvironmentSourceReview,
  useEnvironmentSources,
} from "src/api/simulate-environments/sourceImports";

const pieceLine = (piece, draft) =>
  `${COPY.pieces[piece]} · ${
    piece === "suite"
      ? COPY.suiteChanges(draft.changes)
      : COPY.environmentChanges(changeCount(draft.changes))
  }`;

// The piece a stale refusal names, else the first stale one.
const staleText = (drafts, piece) => {
  const stalePiece =
    (piece && drafts[piece] && piece) ||
    drafts.pieces.find((one) => drafts[one].stale) ||
    drafts.pieces[0];
  const draft = drafts[stalePiece];
  return COPY.stale(
    COPY.pieces[stalePiece],
    draft.parentLabel,
    draft.defaultLabel,
  );
};

const errorText = (error, drafts, fallback) => {
  if (error?.error === "draft_stale") return staleText(drafts, error.piece);
  return COPY.errors[error?.error] || refusalText(error, fallback);
};

export function WorkspaceDraftBar(props) {
  const [params, setParams] = useSearchParams();
  const summary = useEnvironmentSources(
    props.env?.versioning?.enabled ? props.env.id : null,
  );
  const changeReview = (open) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (open) next.set("reviewChanges", "1");
        else next.delete("reviewChanges");
        return next;
      },
      { replace: true },
    );
  return (
    <DraftBar
      {...props}
      inline
      changeReview={changeReview}
      reviewOpen={params.has("reviewChanges")}
      summary={summary}
    />
  );
}

WorkspaceDraftBar.propTypes = { env: PropTypes.object };

/**
 * The environment's unsaved changes, under the version bar: what they change,
 * who edited them last, and Save (every open piece at once) or Discard. Shown
 * only with versioning on and a draft open.
 */
export default function DraftBar({
  env,
  busy = false,
  rebuilding = false,
  onViewChanges,
  inline,
  changeReview,
  reviewOpen,
  summary,
}) {
  const enabled = Boolean(env?.versioning?.enabled);
  const queryClient = useQueryClient();
  const { data: drafts } = useDrafts(env?.id, { enabled });
  const { reviewId } = useActiveEnvironmentSourceReview(env?.id);
  const { data: review } = useEnvironmentSourceReview(
    enabled ? summary?.data?.generation_review_id || reviewId : null,
  );
  const { data: sourceGeneration } = useEnvironmentSourceGeneration(
    summary?.data?.generation_review_id || review?.id,
    summary?.data?.scenario_generation || review?.scenario_generation,
  );
  const save = useSaveDrafts(env?.id);
  const discard = useDiscardDraft(env?.id);
  const { canEdit } = useSimulationAccess();
  const [confirming, setConfirming] = useState(false);
  const staleId = useId();
  const busyId = useId();
  const holdId = useId();
  const generationId = useId();
  const affectedDraft = Boolean(
    summary?.data?.sources?.some((source) => source.unsaved) ||
      (review?.receipt?.draft?.id &&
        review.receipt.draft.id === drafts?.environment?.id),
  );
  const sealedSuite = sourceGeneration?.suite_draft;
  const awaitingSuite =
    affectedDraft &&
    sourceGeneration?.state === "completed" &&
    (drafts?.suite?.id !== sealedSuite?.id ||
      drafts?.suite?.revision < sealedSuite?.revision);
  const generationBlocked =
    (inline && Boolean(drafts?.environment) && !summary?.data) ||
    (affectedDraft &&
      sourceGeneration &&
      (sourceGeneration.state !== "completed" || awaitingSuite));
  let generationReason = COPY.errors.source_generation_not_ready;
  if (sourceGenerationPending(sourceGeneration)) {
    generationReason = COPY.errors.source_generation_in_progress;
  } else if (awaitingSuite) {
    generationReason = COPY.refreshingGeneratedScenarios;
  }
  useEffect(() => {
    if (awaitingSuite) {
      queryClient.invalidateQueries({ queryKey: draftsKey(env.id) });
    }
  }, [awaitingSuite, env?.id, queryClient]);

  useEffect(() => {
    if (inline && reviewOpen && drafts && !drafts.any) changeReview(false);
  }, [inline, reviewOpen, drafts, changeReview]);

  // A refusal is about the drafts it was sent for, so it goes when they
  // change. draft_changed stays: it asks to review the drafts it refetched.
  const signature = drafts?.any
    ? drafts.pieces.map((p) => `${drafts[p].id}:${drafts[p].revision}`).join()
    : "";
  const failed = useRef({});
  failed.current = {
    save: save.isError && save.error?.error !== "draft_changed",
    discard: discard.isError,
  };
  const { reset: resetSave } = save;
  const { reset: resetDiscard } = discard;
  useEffect(() => {
    if (failed.current.save) resetSave();
    if (failed.current.discard) resetDiscard();
  }, [signature, resetSave, resetDiscard]);

  if (!enabled || !drafts?.any) return null;

  const open = drafts.pieces.map((piece) => drafts[piece]);
  const latest = open.reduce((a, b) =>
    new Date(b.updatedAt || 0) > new Date(a.updatedAt || 0) ? b : a,
  );
  const pending = save.isPending || discard.isPending;
  const staleReason = drafts.stale ? staleText(drafts) : null;
  const holdReasons = [busy && busyId, rebuilding && holdId].filter(Boolean);
  const saveReasons = [
    staleReason && staleId,
    generationBlocked && generationId,
    ...holdReasons,
  ].filter(Boolean);

  const onSave = () => {
    discard.reset();
    save.mutate(
      drafts.pieces.map((piece) => ({
        piece,
        parentId: drafts[piece].parentId,
        revision: drafts[piece].revision,
      })),
    );
  };
  const onDiscard = () => {
    setConfirming(false);
    save.reset();
    discard.mutate(drafts.pieces);
  };

  let error = null;
  if (save.error) error = errorText(save.error, drafts, COPY.saveError);
  else if (discard.error) {
    error = errorText(discard.error, drafts, COPY.discardError);
  }

  const controls = (
    <Box
      component="section"
      aria-label={COPY.title}
      data-testid="draft-bar"
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        bgcolor: drafts.stale ? "status.warning.bg" : "background.neutral",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ flexWrap: "wrap", rowGap: 1 }}
      >
        <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 500 }}>
            {COPY.title}:
          </Box>{" "}
          {drafts.pieces
            .map((piece) => pieceLine(piece, drafts[piece]))
            .join(", ")}
          {latest.updatedAt &&
            ` — ${COPY.lastEdited(latest.lastEditedBy, relativeTime(latest.updatedAt))}`}
        </Typography>
        {!inline && (
          <Button
            size="small"
            color="inherit"
            onClick={() => onViewChanges?.(drafts.pieces[0], open[0].id)}
          >
            {COPY.viewChanges}
          </Button>
        )}
        <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.drafts}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={!canEdit || holdReasons.length > 0 || pending}
            aria-describedby={holdReasons.join(" ") || undefined}
            onClick={() => setConfirming(true)}
          >
            {discard.isPending ? COPY.discarding : COPY.discard}
          </Button>
        </ViewerLock>
        <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.drafts}>
          <Button
            size="small"
            variant="contained"
            disabled={!canEdit || saveReasons.length > 0 || pending}
            aria-describedby={saveReasons.join(" ") || undefined}
            onClick={onSave}
          >
            {save.isPending ? COPY.saving : COPY.save}
          </Button>
        </ViewerLock>
      </Stack>
      {staleReason && (
        <Typography
          id={staleId}
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {staleReason}
        </Typography>
      )}
      {busy && (
        <Typography
          id={busyId}
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {COPY.busy}
        </Typography>
      )}
      {generationBlocked && (
        <Typography
          id={generationId}
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {generationReason}
        </Typography>
      )}
      {rebuilding && (
        <Typography
          id={holdId}
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {COPY.errors.rebuild_running}
        </Typography>
      )}
      {error && (
        <Typography
          role="alert"
          sx={{ typography: "s3", color: "status.danger.fg", mt: 0.5 }}
        >
          {error}
        </Typography>
      )}
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={COPY.discardTitle}
        content={
          <Typography component="span" sx={{ typography: "s1" }}>
            {COPY.discardBody}
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={onDiscard}
          >
            {COPY.discard}
          </Button>
        }
      />
    </Box>
  );
  if (!inline) return controls;
  const unsaved = (summary?.data?.sources || []).filter(
    (source) => source.unsaved,
  );
  const generated = affectedDraft
    ? sourceGeneration?.generated_scenarios || []
    : [];
  const editCount = drafts.pieces.reduce(
    (sum, piece) => sum + changeCount(drafts[piece].changes),
    0,
  );
  return (
    <>
      <Typography variant="caption" color="text.secondary">
        {editCount
          ? editCount + (editCount === 1 ? " unsaved edit" : " unsaved edits")
          : COPY.title}
      </Typography>
      <Button
        size="small"
        variant="outlined"
        onClick={() => changeReview(true)}
      >
        Review and save
      </Button>
      <ReviewChangesFrame
        open={Boolean(reviewOpen)}
        onClose={() => changeReview(false)}
      >
        <Stack spacing={2} sx={{ p: 2 }}>
          {drafts.pieces.map((piece) => (
            <Box key={piece}>
              <Typography variant="subtitle2">{COPY.pieces[piece]}</Typography>
              {Object.entries(drafts[piece].changes || {})
                .filter(([, count]) => Number(count) > 0)
                .map(([kind, count]) => (
                  <Typography key={kind}>
                    {kind.replaceAll("_", " ")} · {count}
                  </Typography>
                ))}
            </Box>
          ))}
          {unsaved.length > 0 && (
            <Box>
              <Typography variant="subtitle2">Sources added</Typography>
              {unsaved.map((source) => (
                <Typography key={source.source_id}>
                  {source.filename || "Source file"}
                </Typography>
              ))}
            </Box>
          )}
          {generated.length > 0 && (
            <Box>
              <Typography variant="subtitle2">Generated scenarios</Typography>
              {generated.map((scenario) => (
                <Typography key={scenario.scenario_key}>
                  {scenario.name}
                </Typography>
              ))}
            </Box>
          )}
          {summary?.isError && (
            <Button onClick={summary.refetch}>
              Retry loading source changes
            </Button>
          )}
          {controls}
        </Stack>
      </ReviewChangesFrame>
    </>
  );
}

DraftBar.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }),
  // Chat is working or still saving its last turn.
  busy: PropTypes.bool,
  // A queued or running rebuild holds Save and Discard.
  rebuilding: PropTypes.bool,
  // (piece, draft version id): show that draft in the Versions tab.
  onViewChanges: PropTypes.func,
  inline: PropTypes.bool,
  changeReview: PropTypes.func,
  reviewOpen: PropTypes.bool,
  summary: PropTypes.object,
};
