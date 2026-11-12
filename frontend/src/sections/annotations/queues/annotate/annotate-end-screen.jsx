import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, Typography } from "@mui/material";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const rowCount = (count) => `${count} ${count === 1 ? "row" : "rows"}`;

// The annotator's own counts when they have assigned rows, else the queue's.
function resolveEndScreenCounts(progress) {
  const userProgress = progress?.user_progress;
  const source = userProgress?.total > 0 ? userProgress : progress;
  return {
    completed: source?.completed ?? 0,
    skipped: source?.skipped ?? 0,
    // Open rows besides the skipped ones (not started or not finished).
    remaining: (source?.pending ?? 0) + (source?.in_progress ?? 0),
    total: source?.total ?? 0,
    queueCompleted: progress?.completed ?? 0,
  };
}

// Where the annotator can still reach open rows: "workspace" (the first open
// row from the top of the queue opens here), "queue-page" (a completed queue,
// where only skipped rows reopen, one at a time from the queue page) or null
// (nothing is open for this annotator: All done).
function resolveOpenRowsAt({ isReviewMode, endCheck, remaining, skipped }) {
  if (isReviewMode || !endCheck) return null;
  if (endCheck.openRow) return "workspace";
  const hasOpenCounts = remaining + skipped > 0;
  // The check failed: trust the counts; the button checks again on click.
  if (endCheck.failed) return hasOpenCounts ? "workspace" : null;
  if (endCheck.queueClosed && hasOpenCounts) return "queue-page";
  return null;
}

function openRowsMessage({ openRowsAt, remaining, skipped }) {
  if (openRowsAt === "queue-page") {
    return skipped > 0
      ? `${rowCount(skipped)} skipped. Open skipped rows from the queue page.`
      : `${rowCount(remaining)} still open. A queue manager can reopen the queue to annotate them.`;
  }
  if (remaining > 0) {
    const open = `${rowCount(remaining + skipped)} still open`;
    return skipped > 0 ? `${open}, ${skipped} skipped.` : `${open}.`;
  }
  if (skipped > 0) return `${rowCount(skipped)} skipped.`;
  return "Some rows are still open.";
}

/**
 * Where the annotate workspace ends — right after the
 * last row is saved, or when nothing is left to open. It says how many rows
 * are done, says "All done" only when no row is open for the annotator (else
 * how many are still open, with a button that opens the first), where the
 * dataset export is (or who can export), and leads back to the queue. Focus
 * moves to its heading so the change is announced.
 *
 * `endCheck` is the workspace's check of the whole queue from the top (the
 * work cursor never wraps): `{ openRow, queueClosed, failed }`. Without it
 * (the workspace opened on an empty queue) the first-item lookup already
 * found nothing open.
 */
export default function AnnotateEndScreen({
  isReviewMode,
  progress,
  endCheck,
  onAnnotateOpenRows,
  isOpeningOpenRows,
  onExport,
  backLabel,
  onBack,
  summary,
}) {
  const { role } = useAuthContext();
  const headingRef = useRef(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const { completed, skipped, remaining, total, queueCompleted } =
    resolveEndScreenCounts(progress);
  const openRowsAt = resolveOpenRowsAt({
    isReviewMode,
    endCheck,
    remaining,
    skipped,
  });
  // Rows the counts call open, but none this annotator can open: they are
  // assigned to or reserved by other annotators.
  const othersHaveOpenRows =
    !isReviewMode && !openRowsAt && remaining + skipped > 0;
  const openRowStatus = endCheck?.openRow?.status;
  const opensSkippedOnly =
    remaining === 0 &&
    skipped > 0 &&
    (!openRowStatus || openRowStatus === "skipped");
  const hasExportableRows =
    !isReviewMode && (completed > 0 || queueCompleted > 0);
  // Adding rows to a dataset is a dataset write (members and admins).
  const canExport = Boolean(RolePermission.DATASETS[PERMISSIONS.CREATE][role]);

  let description = "No items are waiting for review.";
  if (!isReviewMode) {
    description =
      completed > 0 || openRowsAt || othersHaveOpenRows
        ? `${completed} of ${rowCount(total)} done.`
        : "No more pending items in this queue.";
    if (othersHaveOpenRows) {
      description += " The other open rows are with other annotators.";
    }
  }

  return (
    <EmptyState
      icon="solar:check-circle-linear"
      title={openRowsAt ? "End of the queue" : "All done"}
      titleProps={{ ref: headingRef, tabIndex: -1 }}
      description={description}
      action={
        <Stack spacing={2} alignItems="center">
          {openRowsAt && (
            <Alert
              severity="info"
              sx={{ maxWidth: 480 }}
              action={
                openRowsAt === "workspace" ? (
                  <Button
                    color="inherit"
                    size="small"
                    onClick={onAnnotateOpenRows}
                    disabled={isOpeningOpenRows}
                  >
                    {opensSkippedOnly
                      ? "Annotate skipped rows"
                      : "Annotate remaining rows"}
                  </Button>
                ) : null
              }
            >
              {openRowsMessage({ openRowsAt, remaining, skipped })}
            </Alert>
          )}
          {summary}
          {hasExportableRows &&
            (canExport ? (
              <Button
                variant="text"
                startIcon={
                  <Iconify icon="mdi:database-plus-outline" width={18} />
                }
                onClick={onExport}
              >
                Add corrected items to a dataset
              </Button>
            ) : (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ maxWidth: 400 }}
              >
                Workspace members and admins can add corrected items to a
                dataset from Export on the queue page.
              </Typography>
            ))}
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={onBack}
          >
            {backLabel}
          </Button>
        </Stack>
      }
      sx={{ minHeight: "80vh" }}
    />
  );
}

AnnotateEndScreen.propTypes = {
  isReviewMode: PropTypes.bool,
  progress: PropTypes.object,
  endCheck: PropTypes.shape({
    openRow: PropTypes.shape({
      id: PropTypes.string,
      status: PropTypes.string,
    }),
    queueClosed: PropTypes.bool,
    failed: PropTypes.bool,
  }),
  onAnnotateOpenRows: PropTypes.func,
  isOpeningOpenRows: PropTypes.bool,
  onExport: PropTypes.func,
  backLabel: PropTypes.string.isRequired,
  onBack: PropTypes.func.isRequired,
  // Extra end-of-queue content, e.g. the experiment review summary.
  summary: PropTypes.node,
};
