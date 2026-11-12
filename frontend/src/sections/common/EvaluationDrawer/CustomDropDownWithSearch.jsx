import React, { useEffect, useRef, useState } from "react";
import {
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  InputAdornment,
  Box,
  useTheme,
  styled,
  Checkbox,
  ListItemText,
} from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "../../../components/svg-color";
import Iconify from "../../../components/iconify";

const StyledFormControl = styled(FormControl)(() => ({
  "& .MuiInputLabel-outlined": {
    transform: "translate(14px, 5px) scale(1)",
    "&.MuiInputLabel-shrink": {
      transform: "translate(14px, -8px) scale(0.75)",
    },
  },
  "& .MuiInputBase-root": {
    alignItems: "center",
  },
}));

const CustomDropDownWithSearch = ({
  label,
  options,
  value = [],
  onSelect,
  multiple = true,
  ...rest
}) => {
  const [, setOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [, setAnchorEl] = useState(null);
  const theme = useTheme();
  const searchInputRef = useRef(null);
  const selectedValues = multiple
    ? Array.isArray(value)
      ? value.map((val) => {
          const option = options.find(
            (opt) =>
              opt.value === val ||
              opt.label.toLowerCase() === val.toLowerCase(),
          );
          return option ? option.value : val;
        })
      : []
    : value;

  const handleOpen = (event) => {
    setOpen(true);
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleSearchChange = (event) => {
    setSearchTerm(event.target.value);
  };

  const handleSelectChange = (event) => {
    if (onSelect) {
      onSelect(event.target.value);
    }
  };

  const filteredOptions = options.filter((option) =>
    option.label.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  useEffect(() => {
    if (searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [filteredOptions]);

  const getSelectedItemLabels = (selected) => {
    const labels = selected
      .map((val) => {
        const option = options.find((opt) => opt.value === val);
        return option ? option.label : val;
      })
      .join(", ");

    return (
      <Box
        sx={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          display: "block",
        }}
        title={labels}
      >
        {labels}
      </Box>
    );
  };

  return (
    <StyledFormControl fullWidth>
      <InputLabel
        shrink
        sx={{ background: "background.paper", paddingLeft: 1, paddingRight: 1 }}
      >
        {label}
      </InputLabel>
      <Select
        value={selectedValues}
        // label={label}
        placeholder="Select evals type, eg: LLM,RAG,..."
        multiple={multiple}
        onOpen={handleOpen}
        onClose={handleClose}
        onChange={handleSelectChange}
        size="small"
        notched
        renderValue={getSelectedItemLabels}
        MenuProps={{
          PaperProps: {
            style: {
              padding: "12px",
            },
            sx: {
              borderTopLeftRadius: "0px !important",
              borderTopRightRadius: "0px !important",
            },
            onMouseDown: (e) => {
              e.stopPropagation();
            },
          },
        }}
        sx={{
          "& .MuiSelect-select": {
            paddingTop: theme.spacing(0.5),
            paddingBottom: theme.spacing(0.5),
            paddingX: theme.spacing(2),
            minHeight: "auto",
            display: "flex",
            alignItems: "center",
          },
          "& .MuiOutlinedInput-root": {
            padding: 0,
          },
          "& .MuiOutlinedInput-notchedOutline legend": {
            width: "auto",
          },
        }}
        {...rest}
      >
        {/* Interactive Search Field */}
        <Box sx={{ pointerEvents: "auto" }}>
          <TextField
            inputRef={searchInputRef}
            autoFocus
            fullWidth
            variant="outlined"
            size="small"
            value={searchTerm}
            onChange={handleSearchChange}
            placeholder="Search"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            onKeyUp={(e) => e.stopPropagation()}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SvgColor
                    src={`/assets/icons/custom/search.svg`}
                    sx={{ width: 16, height: 16, color: "divider" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{
              "& .MuiInputBase-root": {
                pl: 1,
              },
              pb: 1,
              "& .MuiInputBase-input": {
                paddingY: `${theme.spacing(0.5)}`,
                paddingRight: `${theme.spacing(0.5)}`,
                "&::placeholder": {
                  color: "divider",
                },
              },
            }}
          />
        </Box>

        {/* Filtered Results with Checkboxes */}
        {filteredOptions.map((option) => (
          <MenuItem
            key={option.value}
            value={option.value}
            sx={{
              py: 0.5,
            }}
          >
            <Checkbox
              checked={selectedValues && selectedValues.includes(option.value)}
              sx={{ padding: "4px" }}
            />
            <ListItemText
              primary={option.label}
              primaryTypographyProps={{
                fontSize: "13px",
                fontWeight: 400,
                color: "text.primary",
                noWrap: true, // this is important for ellipsis
                sx: {
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                },
              }}
              sx={{
                margin: 0, // remove default margin
              }}
            />
          </MenuItem>
        ))}
        {filteredOptions.length === 0 && (
          <MenuItem disabled sx={{ opacity: 0.7, fontStyle: "italic" }}>
            No options found
          </MenuItem>
        )}
      </Select>
    </StyledFormControl>
  );
};

CustomDropDownWithSearch.propTypes = {
  label: PropTypes.string,
  options: PropTypes.array,
  value: PropTypes.any,
  onSelect: PropTypes.func,
  size: PropTypes.string,
  multiple: PropTypes.bool,
};

export default CustomDropDownWithSearch;
