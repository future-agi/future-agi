import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getFileIcon, statusIcons } from "./icons";

export function TitleCell(props) {
  const { value } = props;
  const fileType = value.split(".").pop();
  const iconSrc = getFileIcon(fileType);

  return (
    <Stack
      direction={"row"}
      gap={"8px"}
      alignItems={"center"}
      sx={{
        height: "100%",
      }}
    >
      <Box
        component={"img"}
        sx={{
          height: "16px",
          width: "16px",
        }}
        alt="document icon"
        src={iconSrc}
      />
      <Typography fontWeight={"fontWeightRegular"} variant="s1">
        {value}
      </Typography>
    </Stack>
  );
}

TitleCell.propTypes = {
  value: PropTypes.string,
};

const cellStyles = {
  Processing: {
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  Failed: {
    backgroundColor: "status.danger.bg",
    color: "status.danger.fg",
  },
  Completed: {
    backgroundColor: "status.success.bg",
    color: "status.success.fg",
  },
};

export function ProcessingStatusCell(props) {
  const { value } = props;

  const statusIconSrc = statusIcons[value];

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      <Stack
        sx={{
          px: "12px",
          py: "4px",
          gap: "8px",
          height: "24px",
          borderRadius: "12px",
          ...cellStyles[value],
        }}
        direction={"row"}
        alignItems={"center"}
      >
        <Box
          component={"img"}
          sx={{
            height: "12px",
            width: "12px",
          }}
          alt="document icon"
          src={statusIconSrc}
        />
        <Typography variant="s2" fontWeight={"fontWeightMedium"}>
          {value}
        </Typography>
      </Stack>
    </Box>
  );
}

ProcessingStatusCell.propTypes = {
  value: PropTypes.string,
};
