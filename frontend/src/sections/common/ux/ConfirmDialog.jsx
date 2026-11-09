import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
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
import { LoadingButton } from "@mui/lab";

const MAX_ITEMS = 5;

/**
 * ConfirmDialog — destructive and consequential actions, named:
 * `Delete "Refund policy review"?`; the body states consequences with counts;
 * `items` lists up to 5 names + `+3 more`; `typedConfirm` asks to type the
 * name (datasets used by experiments). `Keep` has default focus (left); the
 * confirm button sits right (red when `danger`). Enter confirms when the
 * typed name matches; Esc keeps.
 *
 * Props (frozen):
 * @param {boolean} props.open
 * @param {string} props.title  names the object(s)
 * @param {React.ReactNode} [props.body]  consequences, with counts
 * @param {string[]} [props.items]
 * @param {string} [props.confirmLabel="Delete"]
 * @param {string} [props.cancelLabel="Keep"]
 * @param {boolean} [props.danger=true]
 * @param {string} [props.typedConfirm]  the text to type
 * @param {boolean} [props.busy]
 * @param {Function} props.onConfirm
 * @param {Function} props.onClose
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  items = [],
  confirmLabel = "Delete",
  cancelLabel = "Keep",
  danger = true,
  typedConfirm,
  busy = false,
  onConfirm,
  onClose,
}) {
  const [typed, setTyped] = useState("");
  const keepRef = useRef(null);
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);
  const ok = !typedConfirm || typed.trim() === String(typedConfirm).trim();
  const shown = items.slice(0, MAX_ITEMS);
  const more = items.length - shown.length;
  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onClose}
      maxWidth="xs"
      fullWidth
      TransitionProps={{
        onEntered: () => {
          if (!typedConfirm) keepRef.current?.focus();
        },
      }}
      onKeyDown={(e) => {
        if (
          e.key === "Enter" &&
          ok &&
          !busy &&
          e.target?.tagName !== "BUTTON"
        ) {
          e.preventDefault();
          onConfirm?.();
        }
      }}
      PaperProps={{ "aria-label": title }}
    >
      <DialogTitle sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}>
        {title}
      </DialogTitle>
      <DialogContent>
        {body && (
          <Typography typography="s1" color="text.secondary" component="div">
            {body}
          </Typography>
        )}
        {shown.length > 0 && (
          <Box
            component="ul"
            sx={{
              m: 0,
              mt: 1.25,
              pl: 2.5,
              typography: "s1",
              color: "text.primary",
            }}
          >
            {shown.map((name) => (
              <li key={name}>{name}</li>
            ))}
            {more > 0 && (
              <Box
                component="li"
                sx={{ listStyle: "none", color: "text.secondary", ml: -2.5 }}
              >
                +{more} more
              </Box>
            )}
          </Box>
        )}
        {typedConfirm && (
          <Stack gap={0.75} sx={{ mt: 2 }}>
            <Typography typography="s2" color="text.secondary">
              Type <b>{typedConfirm}</b> to confirm.
            </Typography>
            <TextField
              autoFocus
              size="small"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              inputProps={{ "aria-label": `Type ${typedConfirm} to confirm` }}
            />
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button
          ref={keepRef}
          variant="outlined"
          color="inherit"
          onClick={onClose}
          disabled={busy}
        >
          {cancelLabel}
        </Button>
        <LoadingButton
          variant="contained"
          color={danger ? "error" : "primary"}
          onClick={onConfirm}
          disabled={!ok}
          loading={busy}
        >
          {confirmLabel}
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

ConfirmDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  body: PropTypes.node,
  items: PropTypes.arrayOf(PropTypes.string),
  confirmLabel: PropTypes.string,
  cancelLabel: PropTypes.string,
  danger: PropTypes.bool,
  typedConfirm: PropTypes.string,
  busy: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
