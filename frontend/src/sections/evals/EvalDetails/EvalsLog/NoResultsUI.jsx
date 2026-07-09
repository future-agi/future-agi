import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

export default function NoResultsUI({
  title = "No evaluations has been logged",
  description = "Test evaluations on the playground section to view the logs",
}) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "400px",
      }}
    >
      <img
        style={{
          height: "109px",
          width: "117px",
        }}
        alt="no dataset added ui"
        src="/assets/illustrations/no-dataset-added.svg"
      />
      <Stack direction="column" rowGap={0} alignItems="center">
        <Typography
          variant="s1"
          color="text.primary"
          fontWeight="fontWeightSemiBold"
          sx={{ mt: 2.5 }}
        >
          {title}
        </Typography>
        {description && (
          <Typography
            variant="s1"
            color="text.secondary"
            fontWeight="fontWeightRegular"
          >
            {description}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

NoResultsUI.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
};

NoResultsUI.displayName = "NoResultsUI";
