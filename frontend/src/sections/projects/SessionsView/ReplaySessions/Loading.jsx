import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Loader from "../../../../components/Loader/Loader";
import { ShowComponent } from "src/components/show";

export default function Loading({ title, description }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        flex: 1,
        width: "100%",
        flexDirection: "column",
        border: "1px solid",
        borderColor: "background.neutral",
        borderRadius: 0.5,
      }}
    >
      <Loader />
      <Stack gap={0.25}>
        <ShowComponent condition={title}>
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
            sx={{
              textAlign: "center",
            }}
          >
            {title}
          </Typography>
        </ShowComponent>
        <ShowComponent condition={description}>
          <Typography
            typography={"s2_1"}
            color={"text.secondary"}
            sx={{
              textAlign: "center",
            }}
          >
            {description}
          </Typography>
        </ShowComponent>
      </Stack>
    </Box>
  );
}

Loading.propTypes = {
  title: PropTypes.oneOfType([PropTypes.string, PropTypes.node]).isRequired,
  description: PropTypes.oneOfType([PropTypes.string, PropTypes.node])
    .isRequired,
};
