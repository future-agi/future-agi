import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import { RouterLink } from "src/routes/components";

import { GatewayIcon } from "../../shell/gateway-icons";

// Every AI Gateway page: an optional back link, an 18 px semibold title (the
// native Future AGI pages' weight: Datasets, Keys) with one line of
// description, the page's actions on the right, then the content.
// Spacing and type are the Future AGI list pages' own (p: 2, gap 1.5, m2 /
// s1). Below sm the actions wrap under the title. A page with a long
// toolbar before its table can pass `skipLink` ({ label, onActivate }): the
// page's first Tab stop, hidden until focused, that jumps to the table.
// Hidden, it is the visually-hidden 1 px box. The sizes are strings: in MUI
// sx a width or height of 1 means 100%, and a full-width absolute box made
// Logs scroll sideways.
const SKIP_LINK_SX = {
  position: "absolute",
  width: "1px",
  height: "1px",
  margin: "-1px",
  padding: 0,
  border: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  "&:focus, &:focus-visible": {
    position: "static",
    width: "auto",
    height: "auto",
    margin: 0,
    overflow: "visible",
    clip: "auto",
    alignSelf: "flex-start",
    px: 1,
    py: 0.5,
    borderRadius: 1,
    border: 1,
    borderColor: "primary.main",
    bgcolor: "background.paper",
    typography: "s2",
    fontWeight: 500,
  },
};

// Future AGI's Falcon button floats over every page's bottom right
// (sections/falcon-ai/components/FalconAIFab: fixed, 72 px up, 40 px tall).
// Every gateway page ends its content 8 px above it, so the button never
// covers a grid's last row, a price or a count.
//
// The room is a box after the page's content that cannot shrink, not the
// frame's bottom padding: the content box is a flex item with minHeight 0,
// so on a page taller than its parent the content overflows it and the
// padding stays behind (seen at 390 px: catalog, MCP tools, Model
// comparison). The box is the content box's last child and overflows with
// the content, so the page scrolls to 120 px past its last row: the 12 px
// gap and 108 px of box.
const FLOATING_BUTTON_CLEARANCE = 72 + 40 + 8;
const FRAME_GAP_PX = 12;
const CLEARANCE_BOX_SX = {
  flexShrink: 0,
  height: `${FLOATING_BUTTON_CLEARANCE - FRAME_GAP_PX}px`,
};

export default function GatewayPageFrame({
  title,
  documentTitle,
  description,
  actions,
  back,
  children,
  testId,
  skipLink,
}) {
  return (
    <Box
      data-testid={testId}
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: `${FRAME_GAP_PX}px`,
        p: 2,
      }}
    >
      {skipLink && (
        <Link
          href="#"
          underline="none"
          onClick={(event) => {
            event.preventDefault();
            skipLink.onActivate();
          }}
          sx={SKIP_LINK_SX}
        >
          {skipLink.label}
        </Link>
      )}
      <Helmet>
        <title>
          {documentTitle || (typeof title === "string" ? title : "AI Gateway")}
        </title>
      </Helmet>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        {back && (
          <Link
            component={RouterLink}
            href={back.href}
            underline="none"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              alignSelf: "flex-start",
              typography: "s2",
              fontWeight: 500,
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
          >
            <GatewayIcon name="back" size={14} />
            {back.label}
          </Link>
        )}
        <Box
          sx={{
            display: "flex",
            alignItems: { xs: "flex-start", sm: "center" },
            justifyContent: "space-between",
            flexDirection: { xs: "column", sm: "row" },
            gap: 1.5,
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              component="h1"
              typography="m2"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {title}
            </Typography>
            {description && (
              <Typography typography="s1" color="text.secondary">
                {description}
              </Typography>
            )}
          </Box>
          {actions && (
            <Box
              sx={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: 1,
                flexShrink: 0,
              }}
            >
              {actions}
            </Box>
          )}
        </Box>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          gap: `${FRAME_GAP_PX}px`,
          // A child with overflow: hidden would otherwise shrink to zero
          // height on a page taller than the screen. A grid that fills the
          // page sets its own flex and still grows into the room left.
          "& > *": { flexShrink: 0 },
        }}
      >
        {children}
        {/* Last in the box that overflows, so the room overflows with it. */}
        <Box
          aria-hidden
          data-clearance="floating-button"
          sx={CLEARANCE_BOX_SX}
        />
      </Box>
    </Box>
  );
}

GatewayPageFrame.propTypes = {
  title: PropTypes.node.isRequired,
  documentTitle: PropTypes.string,
  description: PropTypes.node,
  actions: PropTypes.node,
  back: PropTypes.shape({
    href: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
  }),
  children: PropTypes.node,
  testId: PropTypes.string,
  skipLink: PropTypes.shape({
    label: PropTypes.string.isRequired,
    onActivate: PropTypes.func.isRequired,
  }),
};
