import { Box, Skeleton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const HeadingAndSubHeading = ({
  heading,
  subHeading,
  loading = false,
  required = false,
}) => {
  const theme = useTheme();
  if (loading) {
    return (
      <Box display={"flex"} flexDirection={"column"} gap={0.5}>
        <Skeleton sx={{ width: "10vw" }} />
        <Skeleton sx={{ width: "30vw" }} />
      </Box>
    );
  }
  return (
    <Box>
      <Typography
        sx={{
          typography: "s1",
          fontWeight: "fontWeightMedium",
          display: "flex",
          flexDirection: "row",
          color: "text.primary",
        }}
      >
        {heading}{" "}
        {required && <Typography color={"status.danger.fg"}>*</Typography>}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          color: "text.disabled",
          marginTop: theme.spacing(0.5),
        }}
      >
        {subHeading}
      </Typography>
    </Box>
  );
};

export default HeadingAndSubHeading;

HeadingAndSubHeading.propTypes = {
  heading: PropTypes.string || PropTypes.node || PropTypes.object,
  subHeading: PropTypes.string || PropTypes.node || PropTypes.object,
  loading: PropTypes.bool,
  required: PropTypes.bool,
};
