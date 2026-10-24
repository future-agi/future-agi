import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";

const Legend = ({ series = [] }) => {
  if (!series || series.length === 0) {
    return null;
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 2,
      }}
    >
      {series.map((item) => (
        <Box
          key={item.name}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Box
            sx={{
              width: "16px",
              height: "16px",
              borderRadius: "4px",
              backgroundColor: item.color,
              flexShrink: 0,
            }}
          />
          <Typography typography="s3">{item.name}</Typography>
        </Box>
      ))}
    </Box>
  );
};

Legend.propTypes = {
  series: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      color: PropTypes.string,
    }),
  ),
};

export default Legend;
