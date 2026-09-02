import { Box, Typography } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";

export default function CardWrapper({ children, title, sx = {}, expanded }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: "4px 4px 12px 0px #0000000A",
        /*
          `height: 220px` capped every card regardless of what was in it, so a
          card scrolled internally and sliced its own content — the metric that
          read "0" with "words/min" cut off below it, and a donut chart with its
          bottom half missing. (`expanded` set "fir-content", a typo, so the
          expanded state was only ever auto-height by accident.)

          height 100% makes the three cards match the tallest in the row, which
          is what they were reaching for, without any of them clipping.
        */
        height: "100%",
        minHeight: expanded ? 0 : 220,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        bgcolor: "background.paper",
        ...sx,
      }}
    >
      {/*
        A card header, not a line of shouting text: the same treatment the rest
        of the app gives one — muted, tracked out, and separated from the body
        by a rule. It was eating 50px of a 220px card in full-strength primary.
      */}
      <Typography
        typography="s3"
        color="text.subtitle"
        fontWeight={"fontWeightBold"}
        sx={{
          flexShrink: 0,
          bgcolor: "background.paper",
          padding: (theme) => theme.spacing(1.25, 2),
          letterSpacing: 0.4,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {_.toUpper(title)}
      </Typography>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: (theme) => theme.spacing(2),
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

CardWrapper.propTypes = {
  children: PropTypes.node.isRequired,
  title: PropTypes.string.isRequired,
  sx: PropTypes.object,
  expanded: PropTypes.bool,
};
