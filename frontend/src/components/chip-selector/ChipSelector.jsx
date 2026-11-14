import { Box, Chip, styled } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const StyledChip = styled(Chip, {
  shouldForwardProp: (prop) => prop !== "isSelected",
})(({ theme, isSelected }) => {
  const discovery = theme.palette.status.discovery;
  return {
    cursor: "pointer",
    transition: "background-color 120ms ease, border-color 120ms ease",
    border: `1px solid ${
      isSelected ? discovery.border : theme.palette.border.default
    }`,
    backgroundColor: isSelected ? discovery.bg : "transparent",
    color: "text.primary",
    "&:hover": {
      backgroundColor: isSelected ? discovery.bg : theme.palette.action.hover,
      borderColor: theme.palette.border.hover,
    },
    "& .MuiChip-label": {
      color: theme.palette.text.primary,
    },
  };
});

const ChipSelector = ({
  options = [],
  value,
  onChange,
  sx = {},
  chipSx = {},
  ...rest
}) => {
  const handleChipClick = (optionValue) => {
    if (onChange) {
      onChange(optionValue);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        gap: 1,
        flexWrap: "wrap",
        ...sx,
      }}
      {...rest}
    >
      {options.map((option) => (
        <StyledChip
          key={option.value}
          label={option.label}
          isSelected={value === option.value}
          onClick={() => handleChipClick(option.value)}
          sx={chipSx}
        />
      ))}
    </Box>
  );
};

ChipSelector.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ).isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func,
  sx: PropTypes.object,
  chipSx: PropTypes.object,
};

export default ChipSelector;
