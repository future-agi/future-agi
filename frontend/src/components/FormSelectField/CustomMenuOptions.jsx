import {
  Box,
  IconButton,
  Input,
  MenuItem,
  Popover,
  Skeleton,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { LabelButton, SearchFieldBox } from "./FormSelectFieldStyle";
import Iconify from "../iconify";

const CustomMenuOptions = ({
  isOpen,
  setIsOpen,
  handleClose,
  anchorEl,
  options,
  onChange,
  onCreateLabel,
  isSearchable,
  value,
  allowClear,
  createLabel,
  loadingMoreOptions,
  contentWidth,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const theme = useTheme();
  const itemRef = useRef(null);
  const handleSearchChange = (event) => {
    setSearchQuery(event.target.value.toLowerCase());
  };

  const handleCreateLabel = () => {
    onCreateLabel();
  };

  const filteredItems = options.filter(
    (item) =>
      item?.value?.toLowerCase()?.includes(searchQuery?.toLowerCase()) ||
      item?.label?.toLowerCase()?.includes(searchQuery?.toLowerCase()),
  );

  const handleModelSelect = (option) => {
    onChange?.(option.value);
    setIsOpen(false);
  };

  return (
    <Popover
      open={isOpen}
      onClose={handleClose}
      anchorEl={anchorEl?.current}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
      PaperProps={{
        sx: {
          width:
            filteredItems.length > 0 && contentWidth
              ? "max-content"
              : anchorEl?.current?.clientWidth,
          maxHeight: filteredItems.length > 5 ? 300 : "max-content",
          display: "flex",
          flexDirection: "column",
          // gap: 1,
        },
      }}
    >
      {isSearchable && isOpen && (
        <SearchFieldBox sx={{ mb: "12px" }}>
          <IconButton size="small" sx={{ color: theme.palette.divider }}>
            <Iconify icon="solar:magnifier-linear" />
          </IconButton>
          <Input
            placeholder="Search"
            disableUnderline
            value={searchQuery}
            onChange={handleSearchChange}
            sx={{ flex: 1 }}
          />
        </SearchFieldBox>
      )}

      {allowClear && value?.length > 0 && (
        <MenuItem onClick={() => onChange("")} value="clear">
          Clear Selection
        </MenuItem>
      )}
      <Box ref={itemRef}>
        {filteredItems.map((option) => {
          const { value, label, ...rest } = option;
          return (
            <MenuItem
              {...rest}
              key={value}
              onClick={() => handleModelSelect(option)}
              sx={{
                paddingY: filteredItems.length > 5 ? 0.5 : 0.5,
                pointerEvents: "all",
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "flex",
                alignItems: "center",
                minWidth: "max-content",
                "&.Mui-disabled": {
                  pointerEvents: "auto",
                  cursor: "pointer",
                  opacity: 1,
                  // eslint-disable-next-line no-restricted-syntax -- ds: error state of unavailable option
                  color: "status.danger.fg",
                },
              }}
            >
              <Box>{label}</Box>
            </MenuItem>
          );
        })}
      </Box>

      {createLabel && (
        <LabelButton sx={{ minWidth: "170px" }} onClick={handleCreateLabel}>
          <Iconify icon="solar:add-linear" /> {createLabel}
        </LabelButton>
      )}

      {loadingMoreOptions && (
        <Box>
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
        </Box>
      )}
    </Popover>
  );
};

export default CustomMenuOptions;

CustomMenuOptions.propTypes = {
  anchorEl: PropTypes.any,
  isOpen: PropTypes.bool,
  createLabel: PropTypes.bool,
  isSearchable: PropTypes.bool,
  allowClear: PropTypes.bool,
  loadingMoreOptions: PropTypes.bool,
  options: PropTypes.array,
  value: PropTypes.any,
  setIsOpen: PropTypes.func,
  handleClose: PropTypes.func,
  onChange: PropTypes.func,
  onCreateLabel: PropTypes.func,
  valueSelector: PropTypes.func,
  multiSelect: PropTypes.bool,
  multiple: PropTypes.bool,
  contentWidth: PropTypes.bool,
};
