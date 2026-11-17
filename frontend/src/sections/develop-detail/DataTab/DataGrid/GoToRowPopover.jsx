import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Popover, TextField, Typography } from "@mui/material";
import { fmtCount } from "src/sections/common/format/fmt";

/** `Go to row…` (⌘G, DG#35): a row number in this view, Enter jumps. */
export default function GoToRowPopover({
  open,
  anchorEl,
  onClose,
  onGo,
  total,
}) {
  const [value, setValue] = useState("");
  useEffect(() => {
    if (open) setValue("");
  }, [open]);
  const n = Number(value);
  const valid = Number.isInteger(n) && n >= 1 && (!total || n <= total);
  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      PaperProps={{ sx: { p: 1.5, width: 240 } }}
    >
      <Box
        component="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          onGo(n);
          onClose();
        }}
      >
        <TextField
          autoFocus
          size="small"
          fullWidth
          label="Go to row"
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/[^\d]/g, ""))}
          error={Boolean(value) && !valid}
          inputProps={{ inputMode: "numeric", "aria-label": "Row number" }}
        />
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ mt: 0.75, display: "block" }}
        >
          {total ? `1 to ${fmtCount(total)} · Enter jumps` : "Enter jumps"}
        </Typography>
      </Box>
    </Popover>
  );
}

GoToRowPopover.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  onGo: PropTypes.func,
  total: PropTypes.number,
};
