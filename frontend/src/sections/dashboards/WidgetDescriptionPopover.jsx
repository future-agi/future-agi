import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Button, Popover, Stack, TextField, Typography } from "@mui/material";

/** Editor for a widget's description. Edits are held here and only handed to
 *  the caller on Done, so dismissing the popover leaves the widget as it was.
 *  The committed value persists with the widget on save, not here. */
export default function WidgetDescriptionPopover({
  open,
  anchorEl,
  value,
  onChange,
  onClose,
}) {
  const [draft, setDraft] = useState(value);

  // Reopening starts from what the widget currently holds, so a dismissed edit
  // does not linger in the field. The popover outlives its own contents.
  useEffect(() => {
    if (open) setDraft(value);
  }, [open, value]);

  const commit = () => {
    onChange(draft);
    onClose();
  };

  const handleKeyDown = (e) => {
    // Enter inserts a line break, so the shortcut is modifier+Enter. Escape is
    // already handled by the Popover's own modal, and discards the edit.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      // Ctrl+Enter still inserts a newline on Windows/Linux; suppress it so
      // the shortcut cannot edit the text on its way out.
      e.preventDefault();
      commit();
    }
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{ paper: { sx: { width: 380, p: 2, mt: 0.5 } } }}
    >
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: 600,
          color: "text.secondary",
          mb: 1,
        }}
      >
        Description
      </Typography>
      <TextField
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="What does this widget measure?"
        multiline
        minRows={3}
        maxRows={8}
        fullWidth
        autoFocus
        sx={{
          "& .MuiOutlinedInput-root": { p: 1.25 },
          "& .MuiOutlinedInput-input": {
            fontSize: "13px",
            lineHeight: 1.6,
          },
        }}
      />
      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1.5 }}>
        <Button size="small" variant="contained" onClick={commit}>
          Done
        </Button>
      </Stack>
    </Popover>
  );
}

WidgetDescriptionPopover.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.object,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
