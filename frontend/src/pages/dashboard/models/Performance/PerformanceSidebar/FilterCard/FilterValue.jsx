import { Box, Checkbox, InputAdornment, Menu, TextField } from "@mui/material";
import React, { useRef, useState } from "react";
import PropType from "prop-types";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const FilterValue = ({ value, updateFilter, dataType, options, disabled }) => {
  const inputValue = dataType === "string" ? value?.join(", ") : value[0] || "";

  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  const dropDownRef = useRef();

  return (
    <>
      <CustomTooltip
        show={disabled}
        title="Select a property first"
        placement="bottom"
        arrow
      >
        <TextField
          disabled={disabled}
          ref={dropDownRef}
          value={inputValue}
          variant="filled"
          size="small"
          placeholder={dataType === "string" ? "Select Value" : "Enter Value"}
          hiddenLabel
          inputProps={{
            readOnly: dataType === "string",
            style: { cursor: dataType === "string" ? "pointer" : "auto" },
          }}
          onClick={() => {
            if (dataType !== "string") return;
            setIsDropdownOpen(true);
          }}
          onChange={(e) => {
            updateFilter((o) => ({ ...o, values: [e.target.value] }));
          }}
          type={dataType === "string" ? "text" : "number"}
        />
      </CustomTooltip>

      <Menu
        open={isDropdownOpen}
        onClose={() => setIsDropdownOpen(false)}
        anchorEl={dropDownRef?.current}
        PaperProps={{
          style: {
            width: `${dropDownRef?.current?.clientWidth}px`,
            maxHeight: 150,
          },
        }}
      >
        <Box sx={{ paddingY: 0.5 }}>
          <TextField
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="eva:search-fill" color="divider" />
                </InputAdornment>
              ),
            }}
            placeholder="value"
            variant="outlined"
            label="Search"
            size="small"
            fullWidth
          />
        </Box>
        <Box
          sx={{
            maxHeight: "100px",
            overflowY: "auto",
          }}
        >
          <ValueOption
            selected={value?.length === options?.length}
            label="Select all"
            onChange={(e) => {
              if (e.target.checked) {
                updateFilter((o) => ({
                  ...o,
                  values: [...options],
                }));
              } else {
                updateFilter((o) => ({ ...o, values: [] }));
              }
            }}
          />
          {options.map((opt) => {
            const selected = Boolean(value?.find((v) => v === opt));
            return (
              <ValueOption
                selected={selected}
                onChange={(e) => {
                  if (e.target.checked) {
                    updateFilter((o) => {
                      const new_filter = {
                        ...o,
                        values: [...(o.values || []), opt],
                      };
                      return new_filter;
                    });
                  } else {
                    updateFilter((o) => {
                      return {
                        ...o,
                        values: (o.values || []).filter((v) => v !== opt),
                      };
                    });
                  }
                }}
                key={opt}
                label={opt}
              />
            );
          })}
        </Box>
      </Menu>
    </>
  );
};

const ValueOption = ({ label, selected, onChange }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        cursor: "pointer",
      }}
      component="label"
    >
      <Checkbox checked={selected} onChange={onChange} />
      {label}
    </Box>
  );
};

ValueOption.propTypes = {
  label: PropType.string,
  selected: PropType.bool,
  onChange: PropType.func,
};

FilterValue.propTypes = {
  value: PropType.string,
  updateFilter: PropType.func,
  dataType: PropType.string,
  options: PropType.array,
  disabled: PropType.bool,
};

export default FilterValue;
