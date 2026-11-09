import React from "react";
import { IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import useVersionUrl from "./useVersionUrl";

/**
 * The Data toolbar `History` button (PRODUCT-SPEC §3.2): toggles the docked
 * Version history panel (`?history=1`).
 */
const VersionHistoryButton = () => {
  const { historyOpen, toggleHistory } = useVersionUrl();
  return (
    <CustomTooltip
      show
      title={historyOpen ? "Hide version history" : "Version history"}
      placement="bottom"
      arrow
      size="small"
    >
      <IconButton
        size="small"
        aria-label="Version history"
        aria-pressed={historyOpen}
        onClick={toggleHistory}
        sx={{
          color: "text.secondary",
          ...(historyOpen && { bgcolor: "action.selected" }),
        }}
      >
        <Iconify icon="mdi:history" width={18} sx={{ color: "text.primary" }} />
      </IconButton>
    </CustomTooltip>
  );
};

export default VersionHistoryButton;
