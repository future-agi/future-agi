import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function ToneChip({ label, color, icon, tooltip }) {
  const chip = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        py: 0.25,
        borderRadius: 0.5,
        flexShrink: 0,
        color,
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.16 : 0.1),
      }}
    >
      {icon && <Iconify icon={icon} width={12} />}
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>
        {label}
      </Typography>
    </Stack>
  );
  return tooltip ? (
    <Tooltip arrow placement="top" title={tooltip}>
      {chip}
    </Tooltip>
  ) : (
    chip
  );
}
ToneChip.propTypes = {
  label: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
  icon: PropTypes.string,
  tooltip: PropTypes.string,
};
