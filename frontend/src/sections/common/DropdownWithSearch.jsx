import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Popover,
  styled,
  InputAdornment,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";

const DevelopSelect = styled(Select)(() => ({
  "& .MuiSelect-select": {
    paddingTop: 4,
    paddingBottom: 4,
    color: "text.primary",
    typography: "s1",
    fontWeight: "fontWeightRegular",
  },
  "& .MuiSelect-icon": {
    color: "text.disabled",
  },
}));

const DropdownWithSearch = ({
  size,
  label,
  popoverComponent,
  sx,
  multiple,
  value,
  options,
  onSelect,
  anchorRef,
  onClose,
  anchorElement,
  useCustomStyle,
  iconUrl,
  labelSx,
  // Some legacy call sites pass react-hook-form's setValue through. The
  // search dropdown does not use it, and MUI would otherwise forward it to DOM.
  // eslint-disable-next-line no-unused-vars
  setValue: _setValue,
  ...rest
}) => {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim());
  const isSearching = searchQuery !== debouncedSearchQuery;

  const handleClose = () => {
    setOpen(false);
    if (onClose) {
      onClose();
    }
  };

  const displayOptions = useMemo(() => {
    if (searchQuery === "" || !searchQuery) {
      return options;
    }
    return options.filter(
      (option) =>
        option.label
          ?.toLowerCase()
          .indexOf(debouncedSearchQuery?.toLowerCase()) > -1,
    );
  }, [searchQuery, options, debouncedSearchQuery]);

  const handleChange = (event) => {
    if (onSelect) {
      onSelect(event.target.value);
    }
  };

  const SelectComponent = useCustomStyle ? DevelopSelect : Select;

  return (
    <FormControl size={size} sx={{ minWidth: "245px", ...sx }}>
      <InputLabel
        required={rest.required}
        shrink
        error={rest?.error}
        sx={{
          background: "background.paper",
          paddingLeft: 1,
          paddingRight: 1,
          ...labelSx,
        }}
      >
        {label}
      </InputLabel>
      <SelectComponent
        size="small"
        value={value}
        open={open}
        onOpen={() => setOpen(true)}
        onClose={handleClose}
        label={label}
        multiple={multiple}
        onChange={handleChange}
        ref={anchorRef}
        MenuProps={{
          autoFocus: false,
          PaperProps: {
            style: {
              display: popoverComponent ? "none" : undefined,
            },
          },
        }}
        startAdornment={
          iconUrl ? (
            <InputAdornment position="start">
              <Box
                component="img"
                sx={{
                  width: (theme) => theme.spacing(2.5),
                  objectFit: "cover",
                }}
                src={iconUrl}
                alt="Dropdown icon"
                onError={(e) => {
                  e.target.style.display = "none";
                }}
                onLoad={(e) => {
                  e.target.style.display = "block";
                }}
              />
            </InputAdornment>
          ) : undefined
        }
        {...rest}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          fullWidth
          autoFocus
          searchQuery={searchQuery}
          onKeyDown={(e) => {
            if (e.key !== "Escape") {
              e.stopPropagation();
            }
          }}
          onChange={(e) => setSearchQuery(e.target.value)}
          onClick={(e) => {
            e.stopPropagation();
          }}
          onClickCapture={(e) => {
            e.stopPropagation();
          }}
          // onChange={(e) => setSearchTerm(e.target.value)}
          sx={{
            my: 1,
            height: 34,
            "& .MuiInputBase-root": {
              height: 34,
              minHeight: 34,
            },
            "& .MuiInputBase-input": {
              height: 34,
            },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                {isSearching ? (
                  <CircularProgress size={16} />
                ) : (
                  <Iconify
                    icon="eva:search-fill"
                    sx={{ color: "text.disabled" }}
                  />
                )}
              </InputAdornment>
            ),
          }}
        />
        {displayOptions.map((option, idx) => (
          <MenuItem
            key={`${option.value ?? "option"}-${idx}`}
            value={option.value}
          >
            {option.label}
          </MenuItem>
        ))}
      </SelectComponent>
      {popoverComponent && (
        <Popover
          open={open}
          anchorEl={anchorRef?.current}
          onClose={handleClose}
          anchorOrigin={{
            vertical: "bottom",
            horizontal: "left",
          }}
          transformOrigin={{
            vertical: "top",
            horizontal: "left",
          }}
        >
          {popoverComponent({
            open,
            onClose: handleClose,
            anchorElement: anchorRef?.current,
          })}
        </Popover>
      )}
    </FormControl>
  );
};

DropdownWithSearch.propTypes = {
  size: PropTypes.string,
  label: PropTypes.string,
  popoverComponent: PropTypes.func,
  sx: PropTypes.object,
  multiple: PropTypes.bool,
  value: PropTypes.string,
  options: PropTypes.array,
  renderValue: PropTypes.func,
  onSelect: PropTypes.func,
  anchorRef: PropTypes.object,
  onClose: PropTypes.func,
  anchorElement: PropTypes.object,
  useCustomStyle: PropTypes.bool,
  iconUrl: PropTypes.string,
  labelSx: PropTypes.object,
  setValue: PropTypes.func,
};

DropdownWithSearch.defaultProps = {
  size: "small",
  label: "",
  sx: {},
  multiple: false,
  options: [],
  onSelect: () => {},
  anchorRef: null,
  onClose: () => {},
  anchorElement: null,
  useCustomStyle: false,
};

export default DropdownWithSearch;
