import React, { useId, useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import Iconify from "../iconify";

// ----------------------------------------------------------------------

// Elements a person can type into, pick, follow or press.
const CONTROL_ROLES = [
  "button",
  "checkbox",
  "combobox",
  "link",
  "listbox",
  "menuitem",
  "radio",
  "searchbox",
  "slider",
  "spinbutton",
  "switch",
  "tab",
  "textbox",
];

const CONTROL_SELECTOR = [
  "a[href]",
  "area[href]",
  "button",
  "input:not([type='hidden'])",
  "select",
  "textarea",
  "iframe",
  "audio[controls]",
  "video[controls]",
  "[contenteditable]:not([contenteditable='false'])",
  "[tabindex]:not([tabindex='-1'])",
  ...CONTROL_ROLES.map((role) => `[role='${role}']`),
].join(", ");

const CONTROL_ATTRIBUTES = [
  "contenteditable",
  "controls",
  "href",
  "role",
  "tabindex",
  "type",
];

// Whether the rendered content holds a control, kept current while the
// content changes on its own (a field that appears after a choice).
function useHoldsControl(node) {
  const [holdsControl, setHoldsControl] = useState(false);
  const lastRef = useRef(false);

  useLayoutEffect(() => {
    const update = (next) => {
      if (next === lastRef.current) return;
      lastRef.current = next;
      setHoldsControl(next);
    };
    if (!node) {
      update(false);
      return undefined;
    }
    const check = () => update(Boolean(node.querySelector(CONTROL_SELECTOR)));
    check();
    if (typeof MutationObserver === "undefined") return undefined;
    const observer = new MutationObserver(check);
    observer.observe(node, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: CONTROL_ATTRIBUTES,
    });
    return () => observer.disconnect();
  }, [node]);

  return holdsControl;
}

export default function ConfirmDialog({
  title,
  content,
  action,
  open,
  onClose,
  closeDisabled = false,
  cancelLabel = "Cancel",
  ...other
}) {
  const theme = useTheme();
  const contentId = useId();
  const [contentNode, setContentNode] = useState(null);
  // A description is read out as one flat string after the dialog's name,
  // so content that holds a form or another control does not describe the
  // dialog: its fields are reached and read one by one instead.
  const holdsControl = useHoldsControl(contentNode);
  return (
    <Dialog
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      open={open}
      onClose={onClose}
      aria-describedby={content && !holdsControl ? contentId : undefined}
      {...other}
    >
      <Box sx={{ padding: 2 }}>
        {/* The dialog is named by the title element (aria-labelledby), so the
            close button sits beside it, not inside it. The title may shrink
            and wrap so the button stays in the row. */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <DialogTitle sx={{ padding: 0, margin: 0, flex: "0 1 auto" }}>
            <Typography color="text.primary" fontWeight={600} typography="m2">
              {title}
            </Typography>
          </DialogTitle>
          <IconButton
            aria-label="Close dialog"
            sx={{
              color: "text.primary",
            }}
            onClick={onClose}
            disabled={closeDisabled}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>

        {content && (
          <DialogContent
            ref={setContentNode}
            id={contentId}
            sx={{
              color: "text.secondary",
              marginTop: theme.spacing(1),
              padding: 0,
            }}
          >
            {content}
          </DialogContent>
        )}

        <DialogActions sx={{ padding: 0, marginTop: 3 }}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            disabled={closeDisabled}
          >
            {cancelLabel}
          </Button>
          {action}
        </DialogActions>
      </Box>
    </Dialog>
  );
}

ConfirmDialog.propTypes = {
  action: PropTypes.node,
  cancelLabel: PropTypes.string,
  closeDisabled: PropTypes.bool,
  content: PropTypes.node,
  onClose: PropTypes.func,
  open: PropTypes.bool,
  title: PropTypes.string,
};
