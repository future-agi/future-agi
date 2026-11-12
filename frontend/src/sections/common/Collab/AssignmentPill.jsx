import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Menu,
  MenuItem,
  Stack,
  TextField,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { notify } from "src/sections/common/ux/toast";
import { collabError, usePatchAssignment } from "src/api/develop/row-collab";
import AssignDialog from "./AssignDialog";
import {
  dateInputValue,
  dueFromDateInput,
  dueLabel,
  dueQuickPicks,
  pillOf,
} from "./assignModel";

const PILL_SX = {
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
  maxWidth: 360,
  whiteSpace: "nowrap",
};

function DueDialog({ open, onClose, assignment, onSave }) {
  const [due, setDue] = useState(dateInputValue(assignment?.due_at));
  const picks = dueQuickPicks(Date.now());
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      onKeyDown={(e) => e.stopPropagation()}
    >
      <DialogTitle sx={{ pb: 1 }}>Change due date</DialogTitle>
      <DialogContent>
        <Stack gap={1} sx={{ pt: 0.5 }}>
          <TextField
            type="date"
            size="small"
            label="Due"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "data-testid": "assign-change-due" }}
          />
          <Stack direction="row" gap={0.5} sx={{ flexWrap: "wrap" }}>
            {picks.map((p) => (
              <Chip
                key={p.key}
                size="small"
                label={p.label}
                variant={due === dateInputValue(p.iso) ? "filled" : "outlined"}
                onClick={() => setDue(dateInputValue(p.iso))}
                sx={{ height: 22, typography: "s3" }}
              />
            ))}
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onSave(null)}>No due date</Button>
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!due}
          onClick={() => onSave(dueFromDateInput(due))}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

DueDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  assignment: PropTypes.object,
  onSave: PropTypes.func,
};

/**
 * W4-2: the Row view's assignment pill: "Assigned to Meera Iyer · due Fri
 * 24 Oct", red "… · Overdue · 2 days", or "Assigned to you by Meera Iyer ·
 * due …" with a primary Mark done. Its menu: Mark done · Change assignee… ·
 * Change due date… · Unassign (people with edit); the assignee can always
 * mark done.
 */
const AssignmentPill = ({ datasetId, assignment, meId, canEdit }) => {
  const [menu, setMenu] = useState(null);
  const [reassign, setReassign] = useState(false);
  const [dueOpen, setDueOpen] = useState(false);
  const patch = usePatchAssignment(datasetId);
  const pill = pillOf(assignment, meId, Date.now());
  if (!pill) return null;
  const mine = pill.mine;
  const canDone = mine || canEdit;
  const run = async (change, done) => {
    try {
      await patch.mutateAsync({ assignmentId: assignment.id, patch: change });
      if (done) notify(done, { key: `asg-${assignment.id}` });
    } catch (e) {
      notify(collabError(e, "Couldn't change the assignment."), {
        variant: "error",
      });
    }
  };
  const markDone = () => run({ status: "done" }, "Marked done");
  const toneSx =
    pill.tone === "red"
      ? {
          borderColor: "transparent",
          bgcolor: (t) => alpha(t.palette.error.main, 0.12),
          color: "error.dark",
          fontWeight: 500,
        }
      : pill.tone === "amber"
        ? {
            borderColor: "transparent",
            bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
            color: "warning.darker",
            fontWeight: 500,
          }
        : {};
  const menuItems = [
    canDone && { key: "done", label: "Mark done", run: markDone },
    canEdit && {
      key: "assignee",
      label: "Change assignee…",
      run: () => setReassign(true),
    },
    canEdit && {
      key: "due",
      label: "Change due date…",
      run: () => setDueOpen(true),
    },
    canEdit && {
      key: "unassign",
      label: "Unassign",
      run: () => run({ unassign: true }, "Unassigned"),
    },
  ].filter(Boolean);
  return (
    <>
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        title={assignment.note ? `Note: ${assignment.note}` : "Assigned row"}
      >
        <ButtonBase
          onClick={(e) => menuItems.length && setMenu(e.currentTarget)}
          data-testid="assignment-pill"
          sx={{ ...PILL_SX, ...toneSx }}
        >
          <Iconify icon="mdi:account-arrow-right-outline" width={14} />
          <Box
            component="span"
            sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {pill.text}
          </Box>
          {menuItems.length > 0 && (
            <Iconify icon="eva:chevron-down-fill" width={14} />
          )}
        </ButtonBase>
      </CustomTooltip>
      {mine && (
        <Button
          size="small"
          variant="contained"
          onClick={markDone}
          disabled={patch.isPending}
          data-testid="assignment-mark-done"
          sx={{ height: 22, typography: "s3", px: 1, minWidth: 0 }}
        >
          Mark done
        </Button>
      )}
      <Menu anchorEl={menu} open={Boolean(menu)} onClose={() => setMenu(null)}>
        {menuItems.map((m) => (
          <MenuItem
            key={m.key}
            data-testid={`assignment-menu-${m.key}`}
            sx={{ typography: "s1" }}
            onClick={() => {
              setMenu(null);
              m.run();
            }}
          >
            {m.label}
          </MenuItem>
        ))}
      </Menu>
      {reassign && (
        <AssignDialog
          open={reassign}
          onClose={() => setReassign(false)}
          datasetId={datasetId}
          rowIds={[assignment.row_id]}
          runId={assignment.run_id ?? null}
          initialDueAt={assignment.due_at ?? null}
          initialNote={assignment.note ?? ""}
        />
      )}
      {dueOpen && (
        <DueDialog
          open={dueOpen}
          onClose={() => setDueOpen(false)}
          assignment={assignment}
          onSave={async (dueAt) => {
            setDueOpen(false);
            await run(
              { due_at: dueAt },
              dueAt ? `Due ${dueLabel(dueAt)}` : "Due date removed",
            );
          }}
        />
      )}
    </>
  );
};

AssignmentPill.propTypes = {
  datasetId: PropTypes.string.isRequired,
  assignment: PropTypes.object,
  meId: PropTypes.string,
  canEdit: PropTypes.bool,
};

export default AssignmentPill;
