import React from "react";
import PropTypes from "prop-types";
import { Box, Divider, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { GRAPH_NODES } from "../common";

const NodeHeader = ({ type, title }) => {
  const node = GRAPH_NODES.find((n) => n.type === type);
  if (!node) return null;
  const { color, backgroundColor, icon, name } = node;
  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
        }}
      >
        <Box
          sx={{
            backgroundColor,
            padding: 1,
            borderRadius: "2px",
            width: "24px",
            height: "24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor
            src={icon}
            sx={{
              width: "16px",
              height: "16px",
              color,
              flexShrink: 0,
            }}
          />
        </Box>
        <Typography typography="s2" fontWeight="fontWeightMedium">
          {title || name}
        </Typography>
      </Box>
      <Divider />
    </>
  );
};

NodeHeader.propTypes = {
  type: PropTypes.string,
  title: PropTypes.string,
};

export default NodeHeader;
