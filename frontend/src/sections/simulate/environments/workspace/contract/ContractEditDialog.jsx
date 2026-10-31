import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import UiOnlyFlag from "../../components/UiOnlyFlag";

const MONO = "ui-monospace, Menlo, monospace";
const OP_WORD = { add: "Add", edit: "Edit", remove: "Remove" };
const TARGET_WORD = { rule: "rule", useCase: "use case" };

// Vel's contract edit: the cost said first, then the change. Contract edits
// have no endpoint yet, so it's flagged UI only and the parent saves nothing.
export default function ContractEditDialog({ edit, onClose, onApply }) {
  const arg = edit.target === "toolArg" ? edit.arg : null;
  const [text, setText] = useState(edit.text || "");
  const [values, setValues] = useState(arg?.values ? arg.values.join(", ") : "");
  const changed =
    edit.op === "remove" ||
    (arg ? values.trim() && values !== (arg.values || []).join(", ") : text.trim() && text.trim() !== (edit.text || "").trim());

  const heading = arg ? (
    <>
      Allowed values for{" "}
      <Box component="span" sx={{ fontFamily: MONO }}>{`${arg.tool}.${arg.name}`}</Box>
    </>
  ) : (
    `${OP_WORD[edit.op]} ${TARGET_WORD[edit.target]}${edit.id ? ` ${edit.id}` : ""}`
  );

  return (
    <Dialog open onClose={onClose} maxWidth={false} fullWidth PaperProps={{ sx: { maxWidth: 720 } }}>
      <DialogTitle component="div" sx={{ typography: "m3", fontWeight: 600 }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>{heading}</span>
          <UiOnlyFlag feature="contractEdits" />
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Box sx={{ px: 1.5, py: 1.25, borderRadius: 1, bgcolor: "background.neutral" }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              This creates the next environment version once it&apos;s a change.
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
              Scenarios that rest on it still run, but count toward no decision until they&apos;re re-proved. Agent,
              scenario set and evaluations don&apos;t move.
            </Typography>
          </Box>
          {!arg && edit.op !== "remove" && (
            <TextField
              autoFocus
              fullWidth
              multiline
              minRows={2}
              size="small"
              label={edit.target === "rule" ? "Rule" : "Use case"}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          )}
          {edit.op === "remove" && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{edit.text}</Typography>
          )}
          {arg && (
            <TextField
              autoFocus
              fullWidth
              size="small"
              label="Allowed values, comma-separated"
              value={values}
              onChange={(e) => setValues(e.target.value)}
              InputProps={{ sx: { fontFamily: MONO } }}
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button size="small" variant="contained" color="primary" disabled={!changed} onClick={onApply}>
          Create the next environment version
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ContractEditDialog.propTypes = {
  edit: PropTypes.shape({
    target: PropTypes.oneOf(["rule", "useCase", "toolArg"]).isRequired,
    op: PropTypes.oneOf(["add", "edit", "remove"]),
    id: PropTypes.string,
    text: PropTypes.string,
    arg: PropTypes.object,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
};
