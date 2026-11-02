import PropTypes from "prop-types";
import { useEffect, useId, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";

import { ConfirmDialog } from "src/components/custom-dialog";
import { relativeTime } from "src/utils/format-time";
import {
  useDiscardDraft,
  useDrafts,
  useSaveDrafts,
} from "src/api/simulate-environments/versions";
import { refusalText } from "../evals/refusalText";
import { DRAFTS_COPY as COPY } from "../workspace.constants";
import { changeCount } from "./changeCount";

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
}) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data: drafts } = useDrafts(env?.id, { enabled });
  const save = useSaveDrafts(env?.id);
  const discard = useDiscardDraft(env?.id);
  const [confirming, setConfirming] = useState(false);
  const staleId = useId();
  const busyId = useId();
  const holdId = useId();

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
  const saveReasons = [staleReason && staleId, ...holdReasons].filter(Boolean);

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

  return (
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
        bgcolor: (t) =>
          drafts.stale
            ? alpha(t.palette.warning.main, 0.12)
            : t.palette.background.neutral,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ flexWrap: "wrap", rowGap: 1 }}
      >
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: "fontWeightBold" }}>
            {COPY.title}:
          </Box>{" "}
          {drafts.pieces
            .map((piece) => pieceLine(piece, drafts[piece]))
            .join(", ")}
          {latest.updatedAt &&
            ` — ${COPY.lastEdited(latest.lastEditedBy, relativeTime(latest.updatedAt))}`}
        </Typography>
        <Button
          size="small"
          color="inherit"
          onClick={() => onViewChanges?.(drafts.pieces[0], open[0].id)}
          sx={{ textTransform: "none" }}
        >
          {COPY.viewChanges}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          disabled={holdReasons.length > 0 || pending}
          aria-describedby={holdReasons.join(" ") || undefined}
          onClick={() => setConfirming(true)}
          sx={{ textTransform: "none" }}
        >
          {discard.isPending ? COPY.discarding : COPY.discard}
        </Button>
        <Button
          size="small"
          variant="contained"
          disabled={saveReasons.length > 0 || pending}
          aria-describedby={saveReasons.join(" ") || undefined}
          onClick={onSave}
          sx={{ textTransform: "none" }}
        >
          {save.isPending ? COPY.saving : COPY.save}
        </Button>
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
          sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
        >
          {error}
        </Typography>
      )}
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={COPY.discardTitle}
        content={
          <Typography component="span" sx={{ typography: "s2" }}>
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
};
