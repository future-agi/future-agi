import PropTypes from "prop-types";
import { Typography } from "@mui/material";

export default function Label({ children }) {
  return (
    <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.secondary" }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
