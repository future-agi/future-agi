import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

/**
 * The expandable card the reference leans on everywhere: a summary row of title, tags and a
 * right-hand meta caption, opening onto an indented body. Built on <details> rather than a
 * MUI Accordion so the summary can carry a full row of its own content.
 */
const XCard = ({ title, tags, meta, open, children }) => (
  <Box
    component="details"
    open={open || undefined}
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1,
      // The summary's hover fill is square, so without clipping it spills past the card's
      // rounded corners. Safe for the sticky table header inside, which scrolls in its own box.
      overflow: "hidden",
      mb: 1,
      bgcolor: "background.paper",
      "&[open] > summary .alk-mark": { transform: "rotate(90deg)" },
    }}
  >
    <Box
      component="summary"
      sx={{
        listStyle: "none",
        "&::-webkit-details-marker": { display: "none" },
        cursor: "pointer",
        px: 1.25,
        py: 0.9,
        display: "flex",
        alignItems: "center",
        // A generated use-case can be a sentence; clipping it hides the one line
        // that says what the scenario is about, so the row wraps instead.
        flexWrap: "wrap",
        gap: 1,
        rowGap: 0.5,
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Box
        className="alk-mark"
        component="span"
        aria-hidden
        sx={{ color: "text.secondary", fontSize: 11, transition: "transform 120ms" }}
      >
        ▸
      </Box>
      <Typography component="span" sx={{ fontFamily: ALK_MONO, fontSize: 13, color: "text.primary" }}>
        {title}
      </Typography>
      {tags}
      <Box sx={{ flexGrow: 1 }} />
      {meta && (
        <Typography
          component="span"
          sx={{ fontFamily: ALK_MONO, fontSize: 11, color: "text.secondary" }}
        >
          {meta}
        </Typography>
      )}
    </Box>
    {/* Explicit px, matching the reference's .body padding. Without a top gap the summary's
        hover wash butts straight against the content and the card reads as one block. */}
    <Stack spacing={2.25} sx={{ pt: "13px", pr: "16px", pb: "15px", pl: "30px" }}>
      {children}
    </Stack>
  </Box>
);

XCard.propTypes = {
  title: PropTypes.node,
  tags: PropTypes.node,
  meta: PropTypes.node,
  open: PropTypes.bool,
  children: PropTypes.node,
};

export default XCard;
