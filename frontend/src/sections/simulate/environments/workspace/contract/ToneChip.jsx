import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function ToneChip({ label, icon, tooltip }) {
  const chip = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        height: 18,
        borderRadius: "4px",
        flexShrink: 0,
        color: "text.secondary",
        bgcolor: "action.selected",
        cursor: tooltip ? "default" : "inherit",
      }}
    >
      {icon && <Iconify icon={icon} width={12} />}
      <Typography sx={{ typography: "s3", fontWeight: 500 }}>
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
  icon: PropTypes.string,
  tooltip: PropTypes.string,
};
