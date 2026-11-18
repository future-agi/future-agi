import PropTypes from "prop-types";
import { useEffect, useId, useRef, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";

import { ConfirmDialog } from "src/components/custom-dialog";
import { relativeTime } from "src/utils/format-time";
import {
  useDiscardDraft,
  useSaveDrafts,
} from "src/api/simulate-environments/versions";
import { refusalText } from "../evals/refusalText";
import { DRAFTS_COPY as COPY } from "../workspace.constants";
import { changeCount } from "./changeCount";
import { SCENARIOS_COPY } from "../scenarios/scenarios.constants";
import { draftSaveRequests } from "./draftSave";
import { useDraftsValidation, validationStateNote } from "./useDraftsValidation";

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
  const {
    drafts,
    review,
    validate,
    saveValidated,
    state: validationState,
    validating,
    canSaveValidated,
    startValidation,
    saveCandidates,
  } = useDraftsValidation(env?.id, { enabled });
  const save = useSaveDrafts(env?.id);
  const { enqueueSnackbar } = useSnackbar();
  const discard = useDiscardDraft(env?.id);
  const [confirming, setConfirming] = useState(false);
  // A validated save makes a new version, and the server asks for its name.
  const [versionName, setVersionName] = useState("");
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
  // A change accepted or rejected in review makes a new version: it is
  // validated before it can be saved.
  const decided =
    (review?.counts?.accepted ?? 0) + (review?.counts?.rejected ?? 0) > 0;
  const validation = drafts.validation;
  const needsValidate =
    decided ||
    save.error?.error === "validation_required" ||
    Boolean(validation);
  const pending =
    save.isPending ||
    discard.isPending ||
    saveValidated.isPending ||
    validating;
  const staleReason = drafts.stale ? staleText(drafts) : null;
  const holdReasons = [busy && busyId, rebuilding && holdId].filter(Boolean);
  const saveReasons = [staleReason && staleId, ...holdReasons].filter(Boolean);

  const onValidate = () => {
    discard.reset();
    save.reset();
    startValidation();
  };
  const onSaveValidated = () => {
    discard.reset();
    saveCandidates(versionName);
  };
  const onSave = () => {
    discard.reset();
    save.mutate(draftSaveRequests(drafts), {
        // Saving never activates an unvalidated case: it is saved as Draft and does not run.
        onSuccess: (saved) => {
          const drafted = saved?.not_active?.length ?? 0;
          if (drafted) enqueueSnackbar(SCENARIOS_COPY.savedAsDraft(drafted), { variant: "info" });
        },
    });
  };
  const onDiscard = () => {
    setConfirming(false);
    save.reset();
    discard.mutate(drafts.pieces);
  };

  let error = null;
  if (saveValidated.error) {
    error = errorText(saveValidated.error, drafts, COPY.saveError);
  } else if (validate.error) {
    error = errorText(validate.error, drafts, COPY.validateError);
  } else if (save.error && !needsValidate) {
    error = errorText(save.error, drafts, COPY.saveError);
  } else if (discard.error) {
    error = errorText(discard.error, drafts, COPY.discardError);
  }
  const failedScenarios =
    validationState === "failed" ? validation.affectedScenarios : [];
  let validationNote = null;
  if (needsValidate && !validationState && !error) {
    validationNote = COPY.errors.validation_required;
  } else {
    validationNote = validationStateNote(validation);
  }

  let primary;
  if (!needsValidate) {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled={saveReasons.length > 0 || pending}
        aria-describedby={saveReasons.join(" ") || undefined}
        onClick={onSave}
      >
        {save.isPending ? COPY.saving : COPY.save}
      </Button>
    );
  } else if (validating) {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled
        startIcon={<CircularProgress size={12} color="inherit" />}
      >
        {COPY.validating}
      </Button>
    );
  } else if (canSaveValidated) {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled={saveReasons.length > 0 || pending || !versionName.trim()}
        aria-describedby={saveReasons.join(" ") || undefined}
        onClick={onSaveValidated}
      >
        {saveValidated.isPending ? COPY.saving : COPY.save}
      </Button>
    );
  } else {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled={saveReasons.length > 0 || pending}
        aria-describedby={saveReasons.join(" ") || undefined}
        onClick={onValidate}
      >
        {COPY.validate}
      </Button>
    );
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
        <Button
          size="small"
          color="inherit"
          onClick={() => onViewChanges?.(drafts.pieces[0], open[0].id)}
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
        >
          {discard.isPending ? COPY.discarding : COPY.discard}
        </Button>
        {canSaveValidated && !validating && (
          <TextField
            size="small"
            value={versionName}
            onChange={(event) => setVersionName(event.target.value)}
            placeholder={COPY.versionName}
            inputProps={{ "aria-label": COPY.versionName, maxLength: 80 }}
            sx={{ width: 200 }}
          />
        )}
        {primary}
      </Stack>
      {validationNote && (
        <Typography
          role="status"
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {validationNote}
        </Typography>
      )}
      {failedScenarios.length > 0 && (
        <Box
          component="ul"
          sx={{
            m: 0,
            mt: 0.25,
            pl: 2,
            typography: "s3",
            color: "text.secondary",
          }}
        >
          {failedScenarios.map((key) => (
            <li key={key}>{key}</li>
          ))}
        </Box>
      )}
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
