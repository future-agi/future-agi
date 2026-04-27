import { Box, Menu, MenuItem, Typography } from "@mui/material";
import React, { useRef, useState } from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { NumberFilterOperators } from "src/utils/constants";

const FilterOperator = ({ operator, setOperator, dataType }) => {
  const dropDownRef = useRef();
  const [isDropdownOpen, setDropDownOpen] = useState(false);

  const selectedOperator = NumberFilterOperators.find(
    (o) => o.value === operator,
  );

  const currentLabel = dataType === "string" ? "is" : selectedOperator.label;

  const disabled = dataType === "string";

  return (
    <>
      <Box
        ref={dropDownRef}
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: disabled ? "auto" : "pointer",
        }}
        onClick={() => {
          if (disabled) return;
          setDropDownOpen(true);
        }}
      >
        <Typography color="text.disabled" typography="caption">
          {currentLabel}
        </Typography>
        {!disabled && (
          <Iconify
            icon="ion:chevron-down"
            color="text.disabled"
            sx={{ transform: isDropdownOpen ? "rotate(180deg)" : "" }}
          />
        )}
      </Box>

      <Menu
        open={isDropdownOpen}
        onClose={() => setDropDownOpen(false)}
        anchorEl={dropDownRef?.current}
        PaperProps={{
          style: {
            width: `${dropDownRef?.current?.clientWidth}px`,
            maxHeight: 150,
          },
        }}
      >
        {NumberFilterOperators.map(({ label, value }) => (
          <MenuItem
            onClick={() => {
              setOperator(value);
              setDropDownOpen(false);
            }}
            key={value}
          >
            {label}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

FilterOperator.propTypes = {
  operator: PropTypes.string,
  setOperator: PropTypes.func,
  dataType: PropTypes.string,
};

export default FilterOperator;
