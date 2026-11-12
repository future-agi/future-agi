import React from "react";
import IconButton from "@mui/material/IconButton";
import CustomTooltip from "src/components/tooltip";
import FalconIcon from "./FalconIcon";
import useFalconLauncher from "../hooks/useFalconLauncher";

// The Falcon AI launcher as a button in the Omega mobile bar (Agent
// infrastructure routes below md). A floating button there covered page
// content at phone width (MA-N4 gap 4); in the bar it sits in sticky chrome,
// out of the content flow. Same action and same hiding rules as the floating
// button.
export default function FalconAIBarButton() {
  const { openFalconAI, hidden } = useFalconLauncher();
  if (hidden) return null;

  return (
    <CustomTooltip title="Falcon AI" show={true} placement="bottom-end" arrow>
      <IconButton
        onClick={openFalconAI}
        aria-label="Falcon AI"
        data-testid="falcon-ai-launcher"
        data-placement="bar"
        sx={{
          width: 36,
          height: 36,
          flexShrink: 0,
          bgcolor: "#7857FC",
          color: "#fff",
          boxShadow: "0 2px 8px 0 rgba(120, 87, 252, 0.35)",
          "&:hover": { bgcolor: "#6344e0" },
          "&.Mui-focusVisible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 2,
          },
        }}
      >
        <FalconIcon sx={{ fontSize: 18 }} />
      </IconButton>
    </CustomTooltip>
  );
}
