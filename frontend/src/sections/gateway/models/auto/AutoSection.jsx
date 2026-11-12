import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

const CARD_SX = { border: 1, borderColor: "divider", borderRadius: 1 };

// Running text on the Auto routing page: 12 px secondary, at most about 72
// characters a line.
export function AutoNote({ children, component }) {
  return (
    <Typography
      component={component}
      typography="s1"
      color="text.secondary"
      sx={{ maxWidth: "72ch" }}
    >
      {children}
    </Typography>
  );
}

AutoNote.propTypes = {
  children: PropTypes.node,
  component: PropTypes.elementType,
};

// A block of the Auto routing page. Every block has the same heading: a
// 13 px semibold title (m3), an optional 12 px note held to reading width,
// and actions on the right that wrap under it when the row is narrow.
// `card` draws the block as a bordered card whose children set their own
// padding, so a card's parts can run edge to edge between dividers. Without
// it the block is open: its content brings its own frame (a grid, an empty
// state, a code sample).
export function AutoSection({
  title,
  note,
  actions,
  card = false,
  children,
  sectionRef,
  headingRef,
}) {
  return (
    <Box
      component="section"
      aria-label={title}
      ref={sectionRef}
      sx={{
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        ...(card ? CARD_SX : { gap: 1.5 }),
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "flex-start",
          justifyContent: "space-between",
          columnGap: 2,
          rowGap: 1,
          ...(card && { px: 2, pt: 2 }),
        }}
      >
        <Box sx={{ minWidth: 0, flex: "1 1 280px" }}>
          <Typography
            ref={headingRef}
            tabIndex={headingRef ? -1 : undefined}
            component="h2"
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            {title}
          </Typography>
          {note && <AutoNote>{note}</AutoNote>}
        </Box>
        {actions && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexShrink: 0,
            }}
          >
            {actions}
          </Box>
        )}
      </Box>
      {children}
    </Box>
  );
}

AutoSection.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.node,
  actions: PropTypes.node,
  card: PropTypes.bool,
  children: PropTypes.node,
  sectionRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
  headingRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
};
