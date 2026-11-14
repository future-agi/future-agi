import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import { alpha } from "@mui/material/styles";
import ButtonBase from "@mui/material/ButtonBase";

import Iconify from "../../iconify";

// ----------------------------------------------------------------------

export default function StretchOptions({ value, onChange }) {
  return (
    <ButtonBase
      onClick={onChange}
      sx={{
        width: 1,
        height: 80,
        borderRadius: 1,
        color: "text.disabled",
        border: (theme) =>
          `solid 1px ${alpha(theme.palette.text.disabled, 0.08)}`,
        ...(value && {
          bgcolor: "background.paper",
          color: (theme) => theme.palette.primary.main,
          boxShadow: (theme) =>
            `-24px 8px 24px -4px ${alpha(
              theme.palette.mode === "light"
                ? theme.palette.text.disabled
                : theme.palette.text.primary,
              0.08,
            )}`,
        }),
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          width: 0.24,
          transition: (theme) => theme.transitions.create(["width"]),
          ...(value && {
            width: 0.5,
          }),
        }}
      >
        <Iconify
          icon={
            value
              ? "solar:alt-arrow-left-linear"
              : "solar:alt-arrow-right-linear"
          }
          sx={{
            color: (theme) =>
              `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
          }}
        />

        <Box sx={{ flexGrow: 1, borderBottom: `dashed 1.5px currentcolor` }} />

        <Iconify
          icon={
            value
              ? "solar:alt-arrow-right-linear"
              : "solar:alt-arrow-left-linear"
          }
          sx={{
            color: (theme) =>
              `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
          }}
        />
      </Stack>
    </ButtonBase>
  );
}

StretchOptions.propTypes = {
  onChange: PropTypes.func,
  value: PropTypes.bool,
};
