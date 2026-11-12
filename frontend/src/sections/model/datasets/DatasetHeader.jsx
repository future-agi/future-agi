import { Badge, Box, Button, Menu, MenuItem, MenuList } from "@mui/material";
import React, { forwardRef, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { styled } from "@mui/material/styles";
import { buttonClasses } from "@mui/material/Button/";
import PropTypes from "prop-types";

const ActionButtons = styled(Button)(({ theme }) => ({
  [`&.${buttonClasses.text}`]: {
    color: theme.palette.text.disabled,
    fontSize: "12px",
    fontWeight: 400,
  },
}));

forwardRef;

const DatasetHeader = forwardRef(
  (
    {
      filterOpen,
      setFilterOpen,
      onEditColumnClick,
      onExportClick,
      rightSection,
      actionOptions,
      onSortClick,
      actionId,
      appliedFilters,
    },
    ref,
  ) => {
    const actionButtonRef = useRef();
    const [actionDropDownOpen, setActionDropDownOpen] = useState(false);
    const sortButtonRef = useRef();

    React.useImperativeHandle(ref, () => ({
      get sortButton() {
        return sortButtonRef.current;
      },
    }));

    return (
      <Box
        sx={{
          padding: "7.7px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        {/* <FormControl>
          <TextField
            sx={{ width: "415px" }}
            placeholder="Search"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="solar:magnifier-linear" width={16} sx={{ color: "divider" }} />
                </InputAdornment>
              ),
            }}
          />
        </FormControl> */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flex: 1,
            justifyContent: "flex-end",
            gap: 2,
          }}
        >
          {onEditColumnClick ? (
            <ActionButtons
              onClick={onEditColumnClick}
              startIcon={
                <Iconify icon="solar:pen-bold" sx={{ color: "primary.main" }} />
              }
              variant="text"
            >
              Edit column
            </ActionButtons>
          ) : null}
          {setFilterOpen ? (
            <ActionButtons
              onClick={() => setFilterOpen((o) => !o)}
              startIcon={
                <Badge
                  color="error"
                  badgeContent={appliedFilters ? 1 : 0}
                  variant="dot"
                  overlap="circular"
                >
                  <Iconify
                    icon="solar:filter-linear"
                    sx={{ color: "primary.main" }}
                  />
                </Badge>
              }
              variant="text"
            >
              {filterOpen ? "Hide" : "Show"} filter
            </ActionButtons>
          ) : null}
          {onSortClick ? (
            <ActionButtons
              ref={sortButtonRef}
              startIcon={
                <Iconify
                  icon="solar:sort-from-top-to-bottom-linear"
                  sx={{ color: "primary.main" }}
                />
              }
              variant="text"
              onClick={onSortClick}
            >
              Sort
            </ActionButtons>
          ) : null}
          {actionOptions ? (
            <ActionButtons
              id={actionId}
              ref={actionButtonRef}
              onClick={() => setActionDropDownOpen(true)}
              startIcon={
                <Iconify
                  icon="solar:check-circle-bold"
                  sx={{ color: "primary.main" }}
                />
              }
              variant="text"
            >
              Action
            </ActionButtons>
          ) : null}

          <Menu
            anchorOrigin={{
              vertical: "bottom",
              horizontal: "right",
            }}
            transformOrigin={{
              vertical: "top",
              horizontal: "right",
            }}
            open={actionDropDownOpen}
            onClose={() => setActionDropDownOpen(false)}
            anchorEl={actionButtonRef?.current}
          >
            <MenuList>
              {actionOptions?.map(({ label, value, onClick }) => (
                <MenuItem
                  onClick={() => {
                    onClick();
                    setActionDropDownOpen(false);
                  }}
                  key={value}
                >
                  {label}
                </MenuItem>
              ))}
            </MenuList>
          </Menu>

          {onExportClick ? (
            <ActionButtons
              onClick={() => onExportClick()}
              startIcon={
                <Iconify
                  icon="solar:export-linear"
                  sx={{ color: "primary.main" }}
                />
              }
              variant="text"
            >
              Export datasets
            </ActionButtons>
          ) : null}
          {rightSection}
        </Box>
      </Box>
    );
  },
);

DatasetHeader.displayName = "DatasetHeader";

DatasetHeader.propTypes = {
  filterOpen: PropTypes.bool,
  setFilterOpen: PropTypes.func,
  onEditColumnClick: PropTypes.func,
  onExportClick: PropTypes.func,
  rightSection: PropTypes.any,
  actionOptions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string,
      value: PropTypes.string,
      onClick: PropTypes.func,
    }),
  ),
  onSortClick: PropTypes.func,
  actionId: PropTypes.string,
  appliedFilters: PropTypes.number,
};

export default DatasetHeader;
