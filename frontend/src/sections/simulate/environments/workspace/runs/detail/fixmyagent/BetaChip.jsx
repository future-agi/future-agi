import { Chip } from "@mui/material";

// A neutral tag: the theme sets its size, type and colour.
export default function BetaChip() {
  return <Chip label="Beta" size="small" sx={{ ml: 0.75 }} />;
}
