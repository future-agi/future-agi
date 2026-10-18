import PropTypes from "prop-types";
import { Typography } from "@mui/material";

export default function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.4, textTransform: "uppercase", color: "text.subtitle" }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
