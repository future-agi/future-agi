import PropTypes from "prop-types";
import { useEffect, useId, useRef } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { useDraftReview } from "src/api/simulate-environments/draftReview";
import {
  useDrafts,
  useEnvironmentVersions,
  useSaveDrafts,
} from "src/api/simulate-environments/versions";
import { refusalText } from "../evals/refusalText";
import { DRAFTS_COPY as COPY, REVIEW_COPY } from "../workspace.constants";
import { keptChanges, saveLine } from "./reviewView";

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

const noteSx = { typography: "s3", color: "text.secondary", flexBasis: "100%" };

/**
 * The environment's unsaved edits, in the version bar: how many and what saving
 * them makes, opening Review changes, and Save (every open piece at once). Shown
 * only with versioning on and a draft open.
 */
export default function DraftBar({
  env,
  busy = false,
  rebuilding = false,
  onReview,
}) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data: drafts } = useDrafts(env?.id, { enabled });
  const { data: review } = useDraftReview(env?.id, {
    enabled: enabled && Boolean(drafts?.any),
  });
  const { data: versions } = useEnvironmentVersions(env?.id, { enabled });
  const save = useSaveDrafts(env?.id);
  const staleId = useId();
  const busyId = useId();
  const holdId = useId();

  // A refusal is about the drafts it was sent for, so it goes when they
  // change. draft_changed stays: it asks to review the drafts it refetched.
  const signature = drafts?.any
    ? drafts.pieces.map((p) => `${drafts[p].id}:${drafts[p].revision}`).join()
    : "";
  const failed = useRef(false);
  failed.current = save.isError && save.error?.error !== "draft_changed";
  const { reset: resetSave } = save;
  useEffect(() => {
    if (failed.current) resetSave();
  }, [signature, resetSave]);

  if (!enabled || !drafts?.any) return null;

  const kept = keptChanges(review?.changes);
  const failedCount = review?.failed?.length || 0;
  if (!kept.length && !failedCount) return null;

  const line = saveLine(review?.changes, versions);
  const staleReason = drafts.stale ? staleText(drafts) : null;
  const holdReasons = [busy && busyId, rebuilding && holdId].filter(Boolean);
  const saveReasons = [staleReason && staleId, ...holdReasons].filter(Boolean);

  const onSave = () =>
    save.mutate(
      drafts.pieces.map((piece) => ({
        piece,
        parentId: drafts[piece].parentId,
        revision: drafts[piece].revision,
      })),
    );

  return (
    <>
      {/* The dashboard stays 1200 wide and scrolls sideways below that, so there
          the bar takes its own row at the left to keep Save on screen. Above it,
          the line gives way before Save does; the drawer's footer repeats it. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        data-testid="draft-bar"
        sx={{
          flex: { xs: "1 1 100%", lg: "1 1 0" },
          minWidth: { xs: 0, lg: 260 },
          justifyContent: { xs: "flex-start", lg: "flex-end" },
        }}
      >
        <Button
          size="small"
          color="inherit"
          onClick={onReview}
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{ color: "text.secondary", whiteSpace: "nowrap", minWidth: 0 }}
        >
          <Box
            component="span"
            sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {kept.length ? (
              <>
                <Box component="span" sx={{ fontWeight: "fontWeightRegular" }}>
                  {REVIEW_COPY.unsavedEdits(kept.length)}
                </Box>
                <Box
                  component="span"
                  sx={{ display: { xs: "none", lg: "inline" } }}
                >
                  <Box
                    component="span"
                    sx={{ fontWeight: "fontWeightRegular" }}
                  >
                    {" · "}
                  </Box>
                  <Box
                    component="span"
                    sx={{ ml: 0.5, fontWeight: "fontWeightSemiBold" }}
                  >
                    {REVIEW_COPY.saveLine(line)}
                  </Box>
                </Box>
              </>
            ) : (
              REVIEW_COPY.couldntApply(failedCount)
            )}
          </Box>
        </Button>
        {kept.length > 0 && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={saveReasons.length > 0 || save.isPending}
            aria-describedby={saveReasons.join(" ") || undefined}
            onClick={onSave}
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            {save.isPending ? COPY.saving : REVIEW_COPY.save(line.next)}
          </Button>
        )}
      </Stack>
      {staleReason && (
        <Typography id={staleId} sx={noteSx}>
          {staleReason}
        </Typography>
      )}
      {busy && (
        <Typography id={busyId} sx={noteSx}>
          {COPY.busy}
        </Typography>
      )}
      {rebuilding && (
        <Typography id={holdId} sx={noteSx}>
          {COPY.errors.rebuild_running}
        </Typography>
      )}
      {save.error && (
        <Typography role="alert" sx={{ ...noteSx, color: "status.danger.fg" }}>
          {errorText(save.error, drafts, COPY.saveError)}
        </Typography>
      )}
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
  // A queued or running rebuild holds Save.
  rebuilding: PropTypes.bool,
  // Open Review changes.
  onReview: PropTypes.func,
};
