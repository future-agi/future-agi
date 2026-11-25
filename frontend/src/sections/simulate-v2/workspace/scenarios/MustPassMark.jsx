import { Box, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/* The must-pass mark in a table row: a small shield before the name, nothing
   for an optional scenario. Marking and unmarking is in the row's ⋯ menu. */
export default function MustPassMark() {
  return (
    <Tooltip arrow title="Must pass: a failure fails the run">
      <Box
        role="img"
        aria-label="Must pass"
        sx={{ display: "inline-flex", flexShrink: 0, color: "text.primary" }}
      >
        <Iconify icon="solar:shield-check-linear" width={16} />
      </Box>
    </Tooltip>
  );
}
