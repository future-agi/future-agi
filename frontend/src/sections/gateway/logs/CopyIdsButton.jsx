import React from "react";
import PropTypes from "prop-types";
import { IconButton, MenuItem, MenuList, Tooltip } from "@mui/material";

import CustomPopover, { usePopover } from "src/components/custom-popover";

import { GatewayIcon } from "../shell/gateway-icons";
import { copyText } from "../models/copyText";

/**
 * The request drawer's one copy action. This gateway records a request's
 * generation id (the id GET /v1/generation looks up) as its request id, so
 * one button copies both. A log whose generation id differs gets one copy
 * menu with both ids, never two identical icons.
 */
export default function CopyIdsButton({ requestId, generationId }) {
  const popover = usePopover();
  const separate = Boolean(generationId) && generationId !== requestId;

  if (!separate) {
    return (
      <Tooltip
        title={
          generationId
            ? "Copy request id (also its generation id)"
            : "Copy request id"
        }
        placement="top"
        arrow
      >
        <IconButton
          size="small"
          aria-label="Copy request id"
          onClick={() => copyText(requestId, "Request id copied")}
        >
          <GatewayIcon name="copy" size={16} />
        </IconButton>
      </Tooltip>
    );
  }

  const copy = (value, label) => {
    popover.onClose();
    copyText(value, `${label} copied`);
  };

  return (
    <>
      <Tooltip title="Copy an id" placement="top" arrow>
        <IconButton
          size="small"
          aria-label="Copy an id"
          aria-haspopup="menu"
          aria-expanded={popover.open ? "true" : undefined}
          onClick={popover.onOpen}
        >
          <GatewayIcon name="copy" size={16} />
        </IconButton>
      </Tooltip>
      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="top-right"
      >
        <MenuList autoFocusItem={Boolean(popover.open)} dense>
          <MenuItem onClick={() => copy(requestId, "Request id")}>
            Request id
          </MenuItem>
          <MenuItem onClick={() => copy(generationId, "Generation id")}>
            Generation id
          </MenuItem>
        </MenuList>
      </CustomPopover>
    </>
  );
}

CopyIdsButton.propTypes = {
  requestId: PropTypes.string.isRequired,
  generationId: PropTypes.string,
};
