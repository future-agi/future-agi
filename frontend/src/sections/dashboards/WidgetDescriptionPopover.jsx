import PropTypes from "prop-types";
import { Button, Popover, Stack, TextField, Typography } from "@mui/material";

/** Editor for a widget's description. The value is held by the caller and
 *  persists with the widget on save, so closing here only dismisses. */
export default function WidgetDescriptionPopover({
  open,
  anchorEl,
  value,
  onChange,
  onClose,
}) {
  const handleKeyDown = (e) => {
    // Enter inserts a line break, so the shortcut is modifier+Enter. Escape is
    // already handled by the Popover's own modal.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onClose();
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
        value={value}
        onChange={(e) => onChange(e.target.value)}
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
        <Button size="small" variant="contained" onClick={onClose}>
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
