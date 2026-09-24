import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// One entry in the pre-flight estimate strip: an icon, a muted label and the
// estimated value (duration, concurrency or cost).
export default function EstimateRow({ icon, label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Iconify icon={icon} width={15} sx={{ color: "text.subtitle" }} />
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{label}</Typography>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{value}</Typography>
    </Stack>
  );
}

EstimateRow.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  value: PropTypes.node,
};
