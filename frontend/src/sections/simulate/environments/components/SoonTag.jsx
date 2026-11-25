import PropTypes from "prop-types";
import { Box } from "@mui/material";

// The design system's neutral tag, saying a way in isn't buildable yet:
// 12px medium secondary text on the selected fill, no border.
export default function SoonTag({ sx }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        px: 0.75,
        height: 20,
        borderRadius: 0.5,
        fontSize: 12,
        fontWeight: "fontWeightMedium",
        lineHeight: "20px",
        whiteSpace: "nowrap",
        color: "text.secondary",
        bgcolor: "action.selected",
        ...sx,
      }}
    >
      Coming soon
    </Box>
  );
}
SoonTag.propTypes = { sx: PropTypes.object };
