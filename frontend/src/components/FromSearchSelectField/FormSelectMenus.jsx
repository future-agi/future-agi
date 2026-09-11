import React, { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Box,
  Checkbox,
  MenuItem,
  MenuList,
  Popover,
  Portal,
  Skeleton,
  Typography,
} from "@mui/material";
import { LabelButton } from "src/components/FormSelectField/FormSelectFieldStyle";
import Iconify from "src/components/iconify";
import { getPopperDimensions } from "src/sections/develop-detail/DataTab/DoubleClickEditCell/editHelper";
import PropTypes from "prop-types";
import ScrollingWrapper from "../custom-model-dropdown/ScrollingWrapper";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import CustomTooltip from "../tooltip";
const emptyValue = "n_o_t_d_e_f_i_e_n_d";

const FormSelectMenus = ({
  id,
  inputRef,
  open,
  onClose,
  value,
  options,
  onChange,
  searchedValue,
  setSearchedValue,
  multiple,
  createLabel,
  anchorWidth,
  menuPosition,
  onScrollEnd,
  isFetchingNextPage,
  selectAll,
  ...rest
}) => {
  const popperRef = useRef(null);

  const handleOnClick = useCallback(
    (e, option) => {
      if (multiple) {
        // setIsFocus(true);
        const selectedValues = value || [];
        const isSelected = selectedValues.includes(option.value);
        if (isSelected) {
          const newValues = selectedValues.filter(
            (selectedValue) => selectedValue !== option.value,
          );
          onChange({ ...e, target: { ...e.target, value: newValues } });
        } else {
          onChange({
            ...e,
            target: { ...e.target, value: [...selectedValues, option.value] },
          });
        }
      } else {
        const event = {
          ...e,
          target: { ...e.target, value: option.value, option },
        };
        // setIsFocus(false);
        setSearchedValue(""); // Clear search after selection
        onChange(event);
        onClose();
      }
    },
    [multiple, value, onChange, setSearchedValue, onClose],
  );

  const position = getPopperDimensions();

  const fieldWidth = useMemo(() => {
    return inputRef?.current?.offsetWidth || position?.width;
  }, [inputRef, position]);

  const filteredItems = useMemo(() => {
    const filtered =
      options?.filter((item) =>
        item.alwaysVisible // special key check
          ? true
          : item.label?.toLowerCase().includes(searchedValue?.toLowerCase()),
      ) || [];
    return filtered.length > 0
      ? filtered
      : searchedValue
        ? [
            {
              label: rest?.emptyMessage ?? "No option found",
              value: emptyValue,
              disabled: true,
            },
          ]
        : [
            {
              label: rest?.noOptions ?? "No option provided",
              value: emptyValue,
              disabled: true,
            },
          ];
  }, [options, rest?.emptyMessage, rest?.noOptions, searchedValue]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [popperRef, open, onClose]);

  return (
    <Portal>
      <Popover
        id={id}
        anchorEl={inputRef.current}
        ref={popperRef}
        open={open}
        onClose={onClose}
        anchorOrigin={{
          vertical: rest?.error ? 40 : menuPosition,
          horizontal: "left",
        }}
        transformOrigin={{
          vertical: menuPosition === "bottom" ? "top" : "bottom",
          horizontal: "left",
        }}
        disableRestoreFocus
        disableEnforceFocus
        disableAutoFocus
        sx={{
          // width: anchorWidth,
          "& .MuiPaper-root": {
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "action.hover",
            p: "12px",
            boxShadow: "0px 4px 8px rgba(0, 0, 0, 0.1)",
            maxHeight: `280px`,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            ...(createLabel && { pb: "0px" }),
            ...(menuPosition === "top"
              ? { borderRadius: "4px 4px 0px 0px !important", mb: -0.2 }
              : { borderRadius: "0px 0px 4px 4px !important", mt: -0.2 }),
          },
        }}
      >
        <ScrollingWrapper
          position={position}
          width={fieldWidth - 26}
          scrollFunction={onScrollEnd}
          dependancies={[isFetchingNextPage]}
        >
          {/* <Box
            sx={{
              overflowY: "auto",
              overflowX: "hidden",
              borderRadius: "4px",
              position: "relative",
            }}
          > */}
          <MenuList sx={{ padding: "0", overflow: "hidden" }} {...rest}>
            {(() => {
              // Select all header: only for multi-select dropdowns that opt in
              // via selectAll. Operates on the currently filtered (visible)
              // selectable options — excludes disabled, groups, and the
              // "No option" placeholder so searching narrows the scope.
              if (!multiple || !selectAll) return null;
              const selectableItems = filteredItems.filter(
                (o) => !o.disabled && !o.isGroup && o.value !== emptyValue,
              );
              if (selectableItems.length === 0) return null;
              const currentValues = Array.isArray(value) ? value : [];
              const selectableValues = selectableItems.map((o) => o.value);
              const allSelected = selectableValues.every((v) =>
                currentValues.includes(v),
              );
              const handleSelectAll = (e) => {
                const newValues = allSelected
                  ? currentValues.filter((v) => !selectableValues.includes(v))
                  : Array.from(
                      new Set([...currentValues, ...selectableValues]),
                    );
                onChange({ ...e, target: { ...e.target, value: newValues } });
              };
              return (
                <MenuItem
                  key="__select_all__"
                  onClick={handleSelectAll}
                  sx={{
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    mb: 0.5,
                  }}
                >
                  {rest.checkbox && (
                    <Checkbox
                      checked={allSelected}
                      indeterminate={
                        !allSelected &&
                        selectableValues.some((v) => currentValues.includes(v))
                      }
                    />
                  )}
                  <Typography
                    sx={{
                      typography: "s1",
                      fontWeight: "fontWeightMedium",
                      color: "text.primary",
                    }}
                  >
                    Select all
                  </Typography>
                </MenuItem>
              );
            })()}
            {filteredItems?.map((option, index) => {
              const {
                value: optionValue,
                label,
                component,
                disabled,
                ...restOption
              } = option;
              const selectedValue = multiple
                ? value.includes(optionValue)
                : optionValue === value;
              return (
                <MenuItem
                  sx={{
                    ...(component && { padding: 0 }),
                    ...(option?.isGroup && { pointerEvents: "none" }),
                  }}
                  key={optionValue + index}
                  value={optionValue}
                  selected={selectedValue}
                  disabled={disabled}
                  onClick={(e) => {
                    if (!disabled || !option?.isGroup) {
                      handleOnClick(e, option);
                    }
                  }}
                  {...restOption}
                >
                  {rest.checkbox && optionValue != emptyValue && (
                    <Checkbox checked={selectedValue} />
                  )}
                  {component ? (
                    component
                  ) : (
                    <CustomTooltip
                      show={option?.tooltip}
                      title={option.tooltip}
                      placement="left"
                      arrow
                      size="small"
                      type="black"
                      PopperProps={{
                        disablePortal: false,
                      }}
                      slotProps={{
                        tooltip: {
                          sx: {
                            maxWidth: "200px !important",
                          },
                        },
                        popper: {
                          sx: {
                            pointerEvents: "auto",
                          },
                          modifiers: [
                            {
                              name: "preventOverflow",
                              options: {
                                boundary: "viewport",
                                padding: 12,
                              },
                            },
                          ],
                        },
                      }}
                    >
                      <Typography
                        fontWeight={
                          optionValue === value
                            ? "fontWeightMedium"
                            : "fontWeightRegular"
                        }
                        color={disabled ? "text.disabled" : "text.primary"}
                        noWrap
                        sx={{
                          typography: "s1",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          ...(option?.isGroup && {
                            typography: "s3",
                            fontWeight: "fontWeightMedium",
                            color: "text.primary",
                            textTransform: "uppercase",
                          }),
                        }}
                      >
                        {label}
                      </Typography>
                    </CustomTooltip>
                  )}
                </MenuItem>
              );
            })}
          </MenuList>
          {isFetchingNextPage && (
            <Box>
              <Skeleton variant="text" height={34} />
              <Skeleton variant="text" height={34} />
              <Skeleton variant="text" height={34} />
            </Box>
          )}
        </ScrollingWrapper>

        {createLabel && (
          <Box
            sx={{
              paddingBottom: "12px",
              position: "sticky",
              bottom: 0,
              backgroundColor: "inherit",
            }}
          >
            <LabelButton
              onClick={() => {
                onClose();
                trackEvent(Events.annNewLabelClick, {
                  [PropertyName.click]: true,
                });
                rest?.handleCreateLabel?.();
                rest?.onCreateLabel?.();
              }}
              sx={{
                typography: "s1",
                borderTop: "0",
                marginTop: "5px",
                borderRadius: "6px",
                height: "32px",
                backgroundColor: "background.paper",
                paddingLeft: "8px",
                "&:hover": {
                  backgroundColor: "background.neutral",
                },
              }}
            >
              <Iconify icon="eva:plus-fill" /> &nbsp; {createLabel}
            </LabelButton>
          </Box>
        )}
        {/* </Box>/ */}
      </Popover>
    </Portal>
  );
};

export default FormSelectMenus;

FormSelectMenus.propTypes = {
  id: PropTypes.string,
  inputRef: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  value: PropTypes.any,
  anchorWidth: PropTypes.number,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.any.isRequired,
      disabled: PropTypes.bool,
    }),
  ),
  onChange: PropTypes.func,
  searchedValue: PropTypes.string,
  setSearchedValue: PropTypes.func,
  menuPosition: PropTypes.string,
  multiple: PropTypes.bool,
  createLabel: PropTypes.string,
  onScrollEnd: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
  selectAll: PropTypes.bool,
};
