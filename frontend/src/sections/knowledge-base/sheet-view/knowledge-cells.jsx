import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getFileIcon } from "./icons";

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
