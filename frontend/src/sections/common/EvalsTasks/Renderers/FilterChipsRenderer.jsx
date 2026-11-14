import { Box, Typography } from "@mui/material";
import React from "react";

const FilterChipsRenderer = (params) => {
  const items = params.value || [];
  const firstItem = items[0];
  const remainingItems = items.length - 1;

  if (!firstItem) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        No Filters applied
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        height: "100%",
      }}
    >
      {firstItem && (
        <Typography
          component="span"
          sx={{ color: "text.primary", fontSize: "12px", fontWeight: 500 }}
        >
          {firstItem}
        </Typography>
      )}
      {remainingItems > 0 && (
        <Typography
          component="span"
          sx={{
            color: "text.tertiary",
            fontSize: "12px",
            pl: "10px",
          }}
        >
          +{remainingItems} other{remainingItems > 1 ? "s" : ""}
        </Typography>
      )}
    </Box>
  );
};

export default FilterChipsRenderer;
