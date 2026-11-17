import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import { ariaKeyShortcuts, withKeyHint } from "src/utils/keymap";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { GatewayIcon } from "src/sections/gateway/shell/gateway-icons";

const SEARCH_COMMANDS = "Search commands";

// The command palette's own button, at the top of the side navigation on
// every dashboard page (Cmd/Ctrl+K opens Falcon AI). Its tooltip names the
// palette's key, except in a browser that keeps that key for itself; the
// button opens the palette either way. Collapsed (the mini rail, the
// horizontal bar) it is an icon with the same tooltip.
export default function SearchCommandsButton({
  collapsed = false,
  placement = "right",
}) {
  const setPaletteOpen = useShortcutStore((s) => s.setPaletteOpen);
  const open = (event) => {
    // Some browsers do not focus a clicked button. The palette returns focus
    // to the element that had it when it opened.
    event.currentTarget.focus();
    setPaletteOpen(true);
  };
  const buttonProps = {
    "aria-label": SEARCH_COMMANDS,
    "aria-haspopup": "dialog",
    "aria-keyshortcuts": ariaKeyShortcuts("palette"),
    "data-testid": "search-commands",
    onClick: open,
  };
  const title = withKeyHint(SEARCH_COMMANDS, "palette");

  if (collapsed) {
    return (
      <Tooltip title={title} placement={placement}>
        <IconButton
          {...buttonProps}
          size="small"
          sx={{
            width: 36,
            height: 32,
            borderRadius: "6px",
            color: "text.secondary",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <GatewayIcon name="search" size={16} />
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <Box sx={{ px: 0.5 }}>
      <Tooltip title={title} placement={placement}>
        <Button
          {...buttonProps}
          color="inherit"
          fullWidth
          startIcon={<GatewayIcon name="search" size={16} />}
          sx={{
            height: 32,
            px: 0.75,
            justifyContent: "flex-start",
            borderRadius: "6px",
            color: "text.secondary",
            fontSize: "12px",
            fontWeight: 500,
            textTransform: "none",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          {SEARCH_COMMANDS}
        </Button>
      </Tooltip>
    </Box>
  );
}

SearchCommandsButton.propTypes = {
  collapsed: PropTypes.bool,
  placement: PropTypes.string,
};
