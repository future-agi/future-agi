import React from "react";
import Typography from "@mui/material/Typography";

import { MCP_RUNTIME_NOTICE } from "./runtimeStrings";

export default function RuntimeNotice() {
  return (
    <Typography typography="s2" color="text.secondary" role="status">
      {MCP_RUNTIME_NOTICE}
    </Typography>
  );
}
