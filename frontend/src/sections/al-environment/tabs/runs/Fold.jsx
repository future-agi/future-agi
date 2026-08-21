import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";

/**
 * A collapsed section. Four scenarios of transcripts and tool calls unfolded at once is the
 * wall this replaced. The content is built now rather than on first open: the audio player is
 * the only expensive part and it already loads nothing until played, so deferring the rest
 * buys nothing and lets a broken section stay invisible until somebody clicks it.
 */
const Fold = ({ label, children }) => (
  <Box
    component="details"
    sx={{
      my: 0.35,
      borderTop: "1px solid",
      borderColor: "divider",
      "& > summary": {
        cursor: "pointer",
        fontFamily: ALK_MONO,
        fontSize: 11.6,
        color: "text.secondary",
        py: 0.4,
      },
      "& > summary:hover": { color: "text.primary" },
      "&[open] > summary": { fontWeight: 600 },
    }}
  >
    <Box component="summary">{label}</Box>
    {children}
  </Box>
);

Fold.propTypes = { label: PropTypes.node, children: PropTypes.node };

export default Fold;
