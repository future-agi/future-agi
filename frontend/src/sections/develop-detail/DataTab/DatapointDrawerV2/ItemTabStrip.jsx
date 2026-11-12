import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { withKeyHint } from "src/utils/keymap";
import { copyToClipboard } from "src/utils/utils";

const TOGGLE_SX = {
  "& .MuiToggleButton-root": {
    py: 0.25,
    px: 1.25,
    typography: "s2",
    fontWeight: 500,
    textTransform: "none",
    color: "text.secondary",
    borderColor: "divider",
    "&.Mui-selected": { color: "primary.main", bgcolor: "action.selected" },
  },
};

/**
 * The strip on top of the Conversation / Task / Trajectory tabs: the Row
 * tab's `Formatted | Raw` toggle (R; Raw = the value as JSON in the
 * existing ValueViewer raw mode), Copy as JSON, and tab-specific content
 * on the right. Sticks to the top of the panel body.
 */
const ItemTabStrip = ({
  mode,
  setMode,
  copyValue,
  copyLabel = "Copy as JSON",
  children,
}) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={1}
    sx={{
      position: "sticky",
      top: 0,
      zIndex: 3,
      px: 2,
      py: 0.875,
      bgcolor: "background.paper",
      borderBottom: "1px solid",
      borderColor: "divider",
      minWidth: 0,
    }}
  >
    {setMode && (
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        title={withKeyHint("Formatted or raw JSON", "raw")}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, v) => v && setMode(v)}
          sx={TOGGLE_SX}
        >
          <ToggleButton value="formatted">Formatted</ToggleButton>
          <ToggleButton value="raw">Raw</ToggleButton>
        </ToggleButtonGroup>
      </CustomTooltip>
    )}
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      {children}
    </Box>
    {copyValue != null && (
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        title={copyLabel}
      >
        <IconButton
          size="small"
          aria-label={copyLabel}
          onClick={() => {
            copyToClipboard(
              typeof copyValue === "string"
                ? copyValue
                : JSON.stringify(copyValue, null, 2),
            );
            enqueueSnackbar("JSON copied", { variant: "success" });
          }}
        >
          <Iconify icon="mdi:code-json" width={16} />
        </IconButton>
      </CustomTooltip>
    )}
  </Stack>
);

ItemTabStrip.propTypes = {
  mode: PropTypes.string,
  setMode: PropTypes.func,
  copyValue: PropTypes.any,
  copyLabel: PropTypes.string,
  children: PropTypes.node,
};

export default ItemTabStrip;
