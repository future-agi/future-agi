import React, { memo } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import BaseNode from "./BaseNode";
import { NODE_TYPES } from "../../utils/constants";

const METHOD_COLORS = {
  GET: "green.600",
  POST: "blue.600",
  PUT: "orange.600",
  PATCH: "purple.600",
  DELETE: "red.600",
};

function truncateUrl(url, maxLength = 32) {
  if (!url) return "";
  return url.length > maxLength ? `${url.slice(0, maxLength)}...` : url;
}

const HttpRequestNode = ({ id, data, isConnectable, selected }) => {
  const config = data?.config || {};
  const method = config.method || "GET";
  const url = config.url || "";

  const content = url ? (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        minWidth: 0,
      }}
    >
      <Chip
        label={method}
        size="small"
        sx={{
          height: 18,
          fontSize: 10,
          fontWeight: "fontWeightBold",
          bgcolor: (theme) => theme.palette[METHOD_COLORS[method]] || "grey.600",
          color: "common.white",
          "& .MuiChip-label": { px: 0.75 },
        }}
      />
      <Typography
        typography="s2_1"
        color="text.secondary"
        noWrap
        title={url}
        sx={{ minWidth: 0, flex: 1 }}
      >
        {truncateUrl(url)}
      </Typography>
    </Box>
  ) : null;

  return (
    <BaseNode
      id={id}
      data={data}
      isConnectable={isConnectable}
      selected={selected}
      type={NODE_TYPES.HTTP_REQUEST}
      content={content}
    />
  );
};

HttpRequestNode.propTypes = {
  id: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  isConnectable: PropTypes.bool,
  selected: PropTypes.bool,
};

export default memo(HttpRequestNode);
