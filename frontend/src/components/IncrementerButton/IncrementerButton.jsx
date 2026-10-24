import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";

import Stack from "@mui/material/Stack";
import IconButton from "@mui/material/IconButton";

import Iconify from "src/components/iconify";
import { Typography } from "@mui/material";

// ----------------------------------------------------------------------

const IncrementerButton = forwardRef(
  (
    {
      quantity,
      onIncrease,
      onDecrease,
      disabledIncrease,
      disabledDecrease,
      sx,
      ...other
    },
    ref,
  ) => (
    <Stack
      ref={ref}
      flexShrink={0}
      direction="row"
      alignItems="center"
      // justifyContent="space-between"
      sx={{
        gap: (theme) => theme.spacing(1),
        p: (theme) => theme.spacing(0.5, 1),
        minWidth: (theme) => theme.spacing(8.375),
        borderRadius: (theme) => theme.spacing(0.5),
        typography: "subtitle2",
        border: (theme) => `solid 1px ${theme.palette.divider}`,
        mr: -3,
        ...sx,
        // mr:-3
      }}
      {...other}
    >
      <IconButton
        size="small"
        onClick={onDecrease}
        disabled={disabledDecrease}
        sx={{ flex: 1 }}
      >
        <Iconify
          sx={{
            color: "text.disabled",
          }}
          icon="solar:minus-linear"
          width={16}
        />
      </IconButton>

      <Typography
        sx={{
          flex: 1,
          textAlign: "center",
        }}
        variant="s2"
        color={"text.primary"}
        fontWeight={"fontWeightRegular"}
      >
        {quantity == null ? "-" : isNaN(quantity) ? "Select" : quantity}
      </Typography>

      <IconButton
        size="small"
        onClick={onIncrease}
        disabled={disabledIncrease}
        sx={{ flex: 1 }}
      >
        <Iconify
          sx={{
            color: "text.disabled",
          }}
          icon="solar:add-linear"
          width={16}
        />
      </IconButton>
    </Stack>
  ),
);

IncrementerButton.displayName = "IncrementerButton";

IncrementerButton.propTypes = {
  disabledDecrease: PropTypes.bool,
  disabledIncrease: PropTypes.bool,
  onDecrease: PropTypes.func,
  onIncrease: PropTypes.func,
  quantity: PropTypes.number,
  sx: PropTypes.object,
};

export default IncrementerButton;
