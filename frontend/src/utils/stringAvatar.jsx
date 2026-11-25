import React from "react";
import { Typography } from "@mui/material";

function stringAvatar(name) {
  if (name) {
    const nameArr = name.split(" ");
    const nameStr = nameArr.map((item) => item[0] || "");
    if (nameStr.length > 2) {
      nameStr.length = 2;
    }
    return {
      children: (
        <Typography
          typography="s3"
          fontWeight={"fontWeightMedium"}
          color="status.discovery.fg"
        >
          {nameStr.join("")}
        </Typography>
      ),
    };
  }
  return { children: name };
}

export default stringAvatar;
