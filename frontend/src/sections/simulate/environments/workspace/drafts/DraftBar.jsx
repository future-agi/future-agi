import PropTypes from "prop-types";
import { useId, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { ConfirmDialog } from "src/components/custom-dialog";
import { relativeTime } from "src/utils/format-time";
import {
  DRAFTS_COPY as COPY,
  REVIEW_COPY,
  WORKSPACE_COPY,
} from "../workspace.constants";
import ViewerLock from "../../components/ViewerLock";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { changeCount } from "./changeCount";
import useDraftSaveFlow from "./useDraftSaveFlow";

const pieceLine = (piece, draft) =>
  `${COPY.pieces[piece]} · ${
    piece === "suite"
      ? COPY.suiteChanges(draft.changes)
      : COPY.environmentChanges(changeCount(draft.changes))
  }`;

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
  onReview,
}) {
  const flow = useDraftSaveFlow(env);
  const { canEdit } = useSimulationAccess();
  const [confirming, setConfirming] = useState(false);
  // A validated save makes a new version, and the server asks for its name.
  const [versionName, setVersionName] = useState("");
  const staleId = useId();
  const busyId = useId();
  const holdId = useId();

  if (!flow.open) return null;

  const {
    drafts,
    needsValidate,
    validating,
    canSaveValidated,
    pending,
    error,
    validationNote,
    failedScenarios,
  } = flow;
  const open = drafts.pieces.map((piece) => drafts[piece]);
  const latest = open.reduce((a, b) =>
    new Date(b.updatedAt || 0) > new Date(a.updatedAt || 0) ? b : a,
  );
  const staleReason = flow.stale;
  const holdReasons = [busy && busyId, rebuilding && holdId].filter(Boolean);
  const saveReasons = [staleReason && staleId, ...holdReasons].filter(Boolean);

  const onValidate = flow.onValidate;
  const onSaveValidated = () => flow.onSaveValidated(versionName);
  const onSave = flow.onSave;
  const onDiscard = () => {
    setConfirming(false);
    flow.onDiscard();
  };

  let primary;
  if (!needsValidate) {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled={!canEdit || saveReasons.length > 0 || pending}
        aria-describedby={saveReasons.join(" ") || undefined}
        onClick={onSave}
      >
        {flow.savePending ? COPY.saving : COPY.save}
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
        disabled={
          !canEdit || saveReasons.length > 0 || pending || !versionName.trim()
        }
        aria-describedby={saveReasons.join(" ") || undefined}
        onClick={onSaveValidated}
      >
        {flow.savePending ? COPY.saving : COPY.save}
      </Button>
    );
  } else {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled={!canEdit || saveReasons.length > 0 || pending}
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
        {!onReview && (
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
            {flow.discardPending ? COPY.discarding : COPY.discard}
          </Button>
        </ViewerLock>
        {/* With the review drawer, saving happens there: accept, validate, name, save. The bar only opens it;
            someone who can't edit still opens it to read the changes. */}
        {onReview && (
          <Button
            size="small"
            variant="contained"
            disabled={saveReasons.length > 0 || pending}
            aria-describedby={saveReasons.join(" ") || undefined}
            onClick={onReview}
          >
            {REVIEW_COPY.reviewAndSave}
          </Button>
        )}
        {!onReview && canSaveValidated && !validating && (
          <TextField
            size="small"
            value={versionName}
            onChange={(event) => setVersionName(event.target.value)}
            placeholder={COPY.versionName}
            disabled={!canEdit}
            inputProps={{ "aria-label": COPY.versionName, maxLength: 80 }}
            sx={{ width: 200 }}
          />
        )}
        {!onReview && (
          <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.drafts}>
            {primary}
          </ViewerLock>
        )}
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
  // Open the review of every change, where they are accepted, validated and saved.
  onReview: PropTypes.func,
};
