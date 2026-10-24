import * as React from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import OutlinedInput from "@mui/material/OutlinedInput";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import ListItemText from "@mui/material/ListItemText";
import Select from "@mui/material/Select";
import Checkbox from "@mui/material/Checkbox";
import { Typography } from "@mui/material";

const ITEM_HEIGHT = 48;
const ITEM_PADDING_TOP = 8;
const MenuProps = {
  PaperProps: {
    style: {
      maxHeight: ITEM_HEIGHT * 4.5 + ITEM_PADDING_TOP,
      width: 320,
    },
  },
};

export default function DropdownWithCheckBox({
  name,
  control,
  label,
  options = [],
  sx,
  size,
  placeholder,
}) {
  return (
    <FormControl size={size} sx={{ width: 300, ...sx }}>
      <InputLabel
        sx={{
          "&.MuiFormLabel-root": {
            color: "text.disabled",
            fontWeight: "fontWeightMedium",
          },
        }}
        id={`${name}-label`}
      >
        {label}
      </InputLabel>
      <Controller
        name={name}
        control={control}
        render={({ field }) => (
          <Select
            placeholder={placeholder}
            labelId={`${name}-label`}
            id={`${name}-select`}
            multiple
            value={field.value}
            onChange={(e) => {
              field.onChange(e);
            }}
            input={<OutlinedInput label={label} placeholder={placeholder} />}
            renderValue={(selected) => (
              <Typography
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                {options
                  .filter((opt) => selected.includes(opt.value))
                  .map((opt) => opt.label)
                  .join(", ")}
              </Typography>
            )}
            MenuProps={MenuProps}
          >
            {placeholder && !field?.value && (
              <MenuItem disabled value={field?.value}>
                {placeholder}
              </MenuItem>
            )}
            {options?.map((option) => (
              <MenuItem
                key={option?.id}
                value={option?.value}
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  padding: (theme) => theme.spacing(0.25, 1.5),
                  "&:hover": {
                    backgroundColor: "transparent",
                  },
                  "&.Mui-selected": {
                    backgroundColor: "transparent",
                  },
                }}
              >
                <Checkbox
                  sx={{
                    // Change checked color
                    paddingBottom: 0,
                    "& .MuiSvgIcon-root": {
                      stroke: "action.hover",
                      height: (theme) => theme.spacing(2),
                      width: (theme) => theme.spacing(2),
                    },
                  }}
                  checked={field.value.includes(option?.value)}
                />
                <ListItemText
                  primary={
                    <Typography
                      variant="s3"
                      color="text.primary"
                      fontWeight="fontWeightRegular"
                    >
                      {option?.label}
                    </Typography>
                  }
                />
              </MenuItem>
            ))}
          </Select>
        )}
      />
    </FormControl>
  );
}

DropdownWithCheckBox.propTypes = {
  name: PropTypes.string.isRequired,
  control: PropTypes.object.isRequired,
  label: PropTypes.string,
  options: PropTypes.arrayOf(PropTypes.string).isRequired,
  sx: PropTypes.object,
  size: PropTypes.string,
  placeholder: PropTypes.string,
};

DropdownWithCheckBox.defaultProps = {
  label: "Select",
  sx: {},
};
