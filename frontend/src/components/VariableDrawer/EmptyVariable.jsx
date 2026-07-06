import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import SVGColor from "src/components/svg-color";

const EmptyVariable = () => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: "14px",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          padding: 2,
          border: "2.3px solid",
          borderColor: "divider",
          borderRadius: "9.2px",
          lineHeight: 0,
        }}
      >
        <SVGColor
          src="/assets/icons/components/ic_empty_brackets.svg"
          sx={{
            width: 36,
            height: 36,
            background: `linear-gradient(0deg, ${theme.palette.primary.main} 0%, ${theme.palette.pink[500]} 100%)`,
          }}
        />
      </Box>
      <Typography
        variant="m3"
        fontWeight="fontWeightMedium"
        color="text.secondary"
        sx={{ width: "386px", textAlign: "center" }}
      >
        Create variables within the prompt using double curly braces {"{{}}"} to
        insert the datapoints.
      </Typography>
    </Box>
  );
};

export default EmptyVariable;
