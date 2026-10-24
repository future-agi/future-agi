import { Typography } from "@mui/material";
import PropTypes from "prop-types";

const VersionBadge = ({ version = "V1" }) => (
  <Typography
    component="span"
    sx={{
      typography: "s2",
      color: "text.tertiary",
      fontVariantNumeric: "tabular-nums",
      whiteSpace: "nowrap",
    }}
  >
    {version}
  </Typography>
);

VersionBadge.propTypes = {
  version: PropTypes.string,
};

export default VersionBadge;
