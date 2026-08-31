import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

/** A labelled block. The label is the visual grammar of every populated card. */
const Field = ({ label, children }) => (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
    {label && (
      <Typography
        component="span"
        sx={{
          fontFamily: ALK_MONO,
          fontSize: 10.6,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "text.secondary",
        }}
      >
        {label}
      </Typography>
    )}
    {children}
  </Box>
);

Field.propTypes = { label: PropTypes.string, children: PropTypes.node };

export default Field;
