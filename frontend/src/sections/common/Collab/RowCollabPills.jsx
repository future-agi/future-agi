import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAssignments, useRowComments } from "src/api/develop/row-collab";
import AssignDialog from "./AssignDialog";
import AssignmentPill from "./AssignmentPill";
import { openAssignmentOf, openAssignmentOnAnyRun } from "./assignModel";
import {
  ALL_RUN_THREADS,
  commentDeepLink,
  openCommentsText,
} from "./commentModel";
import { isPendingRowId } from "src/sections/develop-detail/DataTab/Versions/pendingRows";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { isSurfaceShown } from "src/launch/launchSwitch";

const pillSx = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  height: 22,
  px: 0.75,
  borderRadius: 0.5,
  border: "1px solid",
  borderColor: "divider",
  typography: "s3",
  color: "text.secondary",
  whiteSpace: "nowrap",
  "&:hover": { borderColor: "text.disabled" },
};

/**
 * W4-1 / W4-2 on Row view header line 2: "2 open comments" (opens
 * Activity › Comments), the assignment pill, or a dashed "+ Assign" chip
 * for people who can edit (both the screen's edit state and the server's
 * permission must allow it).
 */
const RowCollabPills = ({ datasetId, rowId, canEdit }) => {
  const commentsShown = isSurfaceShown("dataset.collab.comments");
  const assignmentsShown = isSurfaceShown("dataset.collab.assignments");
  const [, setParams] = useSearchParams();
  const [assignOpen, setAssignOpen] = useState(false);
  // Open threads on the row and on its result rows (the footer counts both).
  // A hidden part only switches its read off: the hooks keep their own
  // guards (no read for a pending row or a missing id).
  const comments = useRowComments(datasetId, rowId, {
    runId: ALL_RUN_THREADS,
    ...(commentsShown ? {} : { enabled: false }),
  });
  const asg = useAssignments(
    datasetId,
    { assignee: "anyone", status: "open" },
    assignmentsShown ? {} : { enabled: false },
  );
  const live = Boolean(datasetId && rowId && !isPendingRowId(rowId));
  // `compose` (⌘K "Comment on this row") also puts the caret in the
  // composer: `rcompose` is a one-shot token the Activity tab clears.
  const openComments = (compose = false) =>
    setParams((p) => commentDeepLink(p, null, Date.now(), { compose }), {
      replace: true,
    });
  const serverEdit = Boolean(asg.data?.me?.perms?.edit);
  // v2.1 g8 (assign:results-selection): a row assigned from the results
  // (Assign to… on a run's rows) shows that assignment here too, when the
  // row has none of its own.
  const assignment = live
    ? openAssignmentOf(asg.data?.assignments, rowId) ??
      openAssignmentOnAnyRun(asg.data?.assignments, rowId)
    : null;
  useRegisterCommands(
    "w4-row-collab",
    live
      ? [
          {
            id: "row.comment",
            group: "Rows",
            label: "Comment on this row",
            icon: "mdi:comment-plus-outline",
            keywords: ["comment", "mention", "@", "discuss"],
            run: () => openComments(true),
            when: () =>
              commentsShown && comments.data?.me?.perms?.view !== false,
          },
          {
            id: "row.assign",
            group: "Rows",
            label: "Assign this row…",
            icon: "mdi:account-plus-outline",
            keywords: ["assign", "teammate", "due"],
            run: () => setAssignOpen(true),
            when: () =>
              assignmentsShown && canEdit && serverEdit && !assignment,
          },
        ]
      : [],
    [live, rowId, commentsShown, assignmentsShown],
  );
  // A pending new row has no id the server knows yet.
  if (!live) return null;
  const open = comments.data?.open ?? 0;
  const meId = asg.data?.me?.id ?? comments.data?.me?.id;
  return (
    <>
      {commentsShown && open > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          arrow
          title="Open the threads in Activity"
        >
          <ButtonBase
            onClick={() => openComments()}
            sx={pillSx}
            data-testid="row-open-comments"
          >
            <Iconify icon="mdi:comment-outline" width={14} />
            {openCommentsText(open)}
          </ButtonBase>
        </CustomTooltip>
      )}
      {assignmentsShown && assignment ? (
        <AssignmentPill
          datasetId={datasetId}
          assignment={assignment}
          meId={meId}
          canEdit={canEdit && serverEdit}
        />
      ) : assignmentsShown && canEdit && serverEdit ? (
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          arrow
          title="Assign this row to a teammate, with a due date"
        >
          <ButtonBase
            onClick={() => setAssignOpen(true)}
            aria-label="Assign this row"
            data-testid="row-assign-chip"
            sx={{
              height: 20,
              px: 0.75,
              gap: 0.25,
              borderRadius: 0.5,
              border: "1px dashed",
              borderColor: "text.disabled",
              color: "text.secondary",
              "&:hover": { borderColor: "primary.main", color: "primary.main" },
            }}
          >
            <Iconify icon="mdi:plus" width={12} />
            <Typography component="span" typography="s3" fontWeight={500}>
              Assign
            </Typography>
          </ButtonBase>
        </CustomTooltip>
      ) : null}
      {assignmentsShown && assignOpen && (
        <Box>
          <AssignDialog
            open={assignOpen}
            onClose={() => setAssignOpen(false)}
            datasetId={datasetId}
            rowIds={[rowId]}
          />
        </Box>
      )}
    </>
  );
};

RowCollabPills.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  canEdit: PropTypes.bool,
};

export default RowCollabPills;
