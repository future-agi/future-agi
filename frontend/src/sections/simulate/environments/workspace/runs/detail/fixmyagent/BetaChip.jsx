import { Chip } from "@mui/material";
import { alpha } from "@mui/material/styles";

export default function BetaChip() {
  return (
    <Chip
      label="BETA"
      size="small"
      variant="outlined"
      sx={{
        ml: 0.75,
        height: 18,
        fontSize: 10,
        fontWeight: 700,
        borderColor: (t) => alpha(t.palette.info.main, 0.4),
        color: "info.main",
      }}
    />
  );
}
