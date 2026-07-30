import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const statusMessage = {
  Processing: {
    label: "Processing New Files",
    message: (count) =>
      `You've added ${count} ${count > 1 ? "files" : "file"}. We're updating the knowledge base to reflect the new data.`,
  },
  Deleting: {
    label: "Updating Knowledge Base",
    message: (count) =>
      `You've deleted ${count} ${count > 1 ? "files" : "file"}. We're processing the changes to update the knowledge base.`,
  },
};
export default function Status({ sx, status }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "blue.200",
        backgroundColor: "blue.o5",
        padding: "12px",
        borderRadius: "4px",
        ...sx,
      }}
    >
      <Stack direction={"column"} gap={"4px"}>
        <Stack direction={"row"} alignItems={"center"} gap={"8px"}>
          <Iconify
            sx={{
              color: "blue.500",
              height: "16px",
              width: "16px",
            }}
            icon="solar:clock-circle-linear"
          />
          <Typography
            variant="s1"
            color={"blue.500"}
            fontWeight={"fontWeightSemiBold"}
          >
            {statusMessage[status?.status]?.label}
          </Typography>
        </Stack>
        <Typography
          variant="s2"
          fontWeight={"fontWeightRegular"}
          color={"blue.500"}
        >
          {statusMessage[status?.status]?.message(status?.status_count)}
        </Typography>
      </Stack>
    </Box>
  );
}

Status.propTypes = {
  sx: PropTypes.object,
  status: PropTypes.object,
};
