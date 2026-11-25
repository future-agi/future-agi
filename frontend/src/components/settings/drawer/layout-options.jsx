import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import ButtonBase from "@mui/material/ButtonBase";
import { alpha, useTheme } from "@mui/material/styles";

// ----------------------------------------------------------------------

export default function LayoutOptions({ options, value, onChange }) {
  const theme = useTheme();

  const renderNav = (option, selected) => {
    const background = `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`;

    const baseStyles = {
      flexShrink: 0,
      borderRadius: "4px",
      bgcolor: "text.disabled",
    };

    const circle = (
      <Box
        sx={{
          ...baseStyles,
          width: 8,
          height: 8,
          ...(selected && { background }),
        }}
      />
    );

    const primaryItem = (
      <Box
        sx={{
          ...baseStyles,
          width: 1,
          height: 3,
          opacity: 0.48,
          ...(option === "horizontal" && {
            width: 12,
          }),
          ...(selected && { background }),
        }}
      />
    );

    const secondaryItem = (
      <Box
        sx={{
          ...baseStyles,
          width: 1,
          height: 3,
          maxWidth: 12,
          opacity: 0.24,
          ...(option === "horizontal" && {
            width: 8,
          }),
          ...(selected && { background }),
        }}
      />
    );

    return (
      <Stack
        spacing={0.5}
        flexShrink={0}
        direction={option === "horizontal" ? "row" : "column"}
        sx={{
          p: 0.5,
          width: 28,
          height: 1,
          borderRight: `solid 1px ${alpha(theme.palette.text.disabled, 0.08)}`,
          ...(option === "mini" && {
            width: 16,
          }),
          ...(option === "horizontal" && {
            width: 1,
            height: 16,
            alignItems: "center",
            borderRight: "unset",
            borderBottom: `solid 1px ${alpha(theme.palette.text.disabled, 0.08)}`,
          }),
        }}
      >
        {circle}
        {primaryItem}
        {secondaryItem}
      </Stack>
    );
  };

  const renderContent = (selected) => (
    <Box sx={{ p: 0.5, flexGrow: 1, height: 1, width: 1 }}>
      <Box
        sx={{
          width: 1,
          height: 1,
          opacity: 0.08,
          borderRadius: "4px",
          bgcolor: "text.disabled",
          ...(selected && {
            opacity: 0.24,
            background: `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
          }),
        }}
      />
    </Box>
  );

  return (
    <Stack direction="row" spacing={2}>
      {options.map((option) => {
        const selected = value === option;

        return (
          <ButtonBase
            key={option}
            onClick={() => onChange(option)}
            sx={{
              p: 0,
              width: 1,
              height: 56,
              borderRadius: 1,
              border: `solid 1px ${alpha(theme.palette.text.disabled, 0.08)}`,
              ...(selected && {
                bgcolor: "background.paper",
                boxShadow: `-24px 8px 24px -4px ${alpha(
                  theme.palette.mode === "light"
                    ? theme.palette.text.disabled
                    : theme.palette.text.primary,
                  0.08,
                )}`,
              }),
              ...(option === "horizontal" && {
                flexDirection: "column",
              }),
            }}
          >
            {renderNav(option, selected)}
            {renderContent(selected)}
          </ButtonBase>
        );
      })}
    </Stack>
  );
}

LayoutOptions.propTypes = {
  onChange: PropTypes.func,
  options: PropTypes.array,
  value: PropTypes.string,
};
