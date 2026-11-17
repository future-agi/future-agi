import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { notify } from "src/sections/common/ux/toast";
import {
  collabError,
  useAssignments,
  useCreateAssignments,
} from "src/api/develop/row-collab";
import {
  assignTitle,
  assignedToast,
  dateInputValue,
  dueFromDateInput,
  dueQuickPicks,
  moveLines,
} from "./assignModel";

/**
 * W4-2 AssignDialog: "Assign 5 rows" · To [member ▾] (people who can't
 * view the dataset listed disabled, "No access") · Due (Today · Tomorrow ·
 * Friday · Next week, or a date) · Note. Rows already assigned to someone
 * else say they move. Members, access and the row limit come from the
 * assignments read; `rowIds` are dataset row ids (`runId` for result rows).
 */
const AssignDialog = ({
  open,
  onClose,
  datasetId,
  rowIds,
  runId = null,
  defaultAssigneeId = "",
  initialDueAt = null,
  initialNote = "",
  onAssigned,
}) => {
  const q = useAssignments(open ? datasetId : undefined, {
    assignee: "anyone",
    status: "open",
    ...(runId ? { run_id: runId } : {}),
  });
  const create = useCreateAssignments(datasetId);
  const [assigneeId, setAssigneeId] = useState(defaultAssigneeId);
  // "Change assignee…" keeps the row's due date and note unless changed.
  const [due, setDue] = useState(dateInputValue(initialDueAt));
  const [note, setNote] = useState(initialNote ?? "");
  useEffect(() => {
    if (open) {
      setAssigneeId(defaultAssigneeId);
      setDue(dateInputValue(initialDueAt));
      setNote(initialNote ?? "");
    }
  }, [open, defaultAssigneeId, initialDueAt, initialNote]);
  const picks = useMemo(() => dueQuickPicks(Date.now()), [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const members = q.data?.members ?? [];
  const assignee = members.find((m) => m.id === assigneeId) ?? null;
  const lines = assignee
    ? moveLines(
        rowIds,
        q.data?.assignments,
        assignee.name,
        assignee.id,
        q.data?.me?.id,
      )
    : [];
  const max = q.data?.limits?.max_rows;
  const tooMany = Number.isFinite(max) && rowIds.length > max;
  const n = rowIds.length;

  const submit = async () => {
    if (!assignee) return;
    try {
      const dueAt = dueFromDateInput(due);
      await create.mutateAsync({
        row_ids: rowIds,
        ...(runId ? { run_id: runId } : {}),
        assignee_id: assignee.id,
        ...(note.trim() ? { note: note.trim() } : {}),
        ...(dueAt ? { due_at: dueAt } : {}),
      });
      notify(assignedToast(n, assignee.name, dueAt), { variant: "success" });
      onAssigned?.();
      onClose();
    } catch (e) {
      notify(collabError(e, "Couldn't assign the rows."), { variant: "error" });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      data-testid="assign-dialog"
      onKeyDown={(e) => e.stopPropagation()}
    >
      <DialogTitle sx={{ typography: "m3", pb: 1 }}>
        {assignTitle(n)}
      </DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 0.5 }}>
          <TextField
            select
            size="small"
            label="To"
            value={assigneeId}
            onChange={(e) => setAssigneeId(e.target.value)}
            inputProps={{ "data-testid": "assign-to" }}
            SelectProps={{
              MenuProps: { PaperProps: { "data-testid": "assign-to-menu" } },
            }}
          >
            {members.map((m) => (
              <MenuItem
                key={m.id}
                value={m.id}
                disabled={!m.can_view}
                sx={{ typography: "s1" }}
              >
                {m.name}
                {!m.can_view && (
                  <Box
                    component="span"
                    sx={{ ml: 0.75, color: "text.disabled", typography: "s2" }}
                  >
                    No access
                  </Box>
                )}
              </MenuItem>
            ))}
          </TextField>
          <Box>
            <Stack direction="row" alignItems="center" gap={1}>
              <TextField
                type="date"
                size="small"
                label="Due (optional)"
                value={due}
                onChange={(e) => setDue(e.target.value)}
                InputLabelProps={{ shrink: true }}
                inputProps={{ "data-testid": "assign-due" }}
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack
              direction="row"
              gap={0.5}
              sx={{ mt: 0.75, flexWrap: "wrap" }}
            >
              {picks.map((p) => {
                const v = dateInputValue(p.iso);
                const on = due === v;
                return (
                  <Chip
                    key={p.key}
                    size="small"
                    label={p.label}
                    variant={on ? "filled" : "outlined"}
                    color={on ? "primary" : "default"}
                    onClick={() => setDue(on ? "" : v)}
                    data-testid={`assign-due-${p.key}`}
                    sx={{ height: 22, typography: "s3" }}
                  />
                );
              })}
            </Stack>
          </Box>
          <TextField
            size="small"
            label="Note (optional)"
            placeholder="Check the refund timeline"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            multiline
            maxRows={4}
            inputProps={{ "data-testid": "assign-note" }}
          />
          {lines.map((l) => (
            <Typography
              key={l}
              typography="s2"
              color="text.secondary"
              data-testid="assign-move-line"
            >
              {l}
            </Typography>
          ))}
          {tooMany && (
            <Typography typography="s2" color="error.main">
              {`Assign up to ${max.toLocaleString("en-US")} rows at a time.`}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!assignee || tooMany || create.isPending || !n}
          onClick={submit}
          data-testid="assign-submit"
        >
          {assignTitle(n)}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AssignDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string.isRequired,
  rowIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  runId: PropTypes.string,
  defaultAssigneeId: PropTypes.string,
  initialDueAt: PropTypes.string,
  initialNote: PropTypes.string,
  onAssigned: PropTypes.func,
};

export default AssignDialog;
