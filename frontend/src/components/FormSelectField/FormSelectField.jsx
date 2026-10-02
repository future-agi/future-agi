//@ts-nocheck
import React, { useState } from "react";
import { Controller } from "react-hook-form";
import {
  Box,
  FormControl,
  FormHelperText,
  IconButton,
  Input,
  InputLabel,
  MenuItem,
  Select,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import _ from "lodash";
import { LabelButton, SearchFieldBox } from "./FormSelectFieldStyle";
import Iconify from "src/components/iconify";
import { red } from "src/theme/palette";
import { mergeRefs } from "src/utils/utils";

/**
 * A wrapper component for displaying MUI Select with react-hook-form
 */
const FormSelectField = (
  {
    control,
    options,
    fieldName,
    isSearchable,
    valueSelector,
    helperText,
    fullWidth,
    createLabel,
    onCreateLabel,
    dropDownMaxHeight,
    onScrollEnd,
    loadingMoreOptions,
    allowClear,
    multiple,
    contentWidth,
    defaultValue,
    sx,
    onBlur,
    required,
    ...rest
  },
  forwardedRef,
) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const theme = useTheme();
  const [isFocus, setIsFocus] = useState(false);

  const handleOpen = () => {
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const handleScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    if (scrollTop + clientHeight >= scrollHeight - 5) {
      onScrollEnd?.();
    }
  };

  const setRef = (innerRef) => {
    const container = innerRef;
    if (container) {
      container.addEventListener("scroll", handleScroll);
    }
  };

  const handleSearchChange = (event) => {
    setSearchQuery(event.target.value.toLowerCase());
  };

  const handleCreateLabel = () => {
    setIsFocus(false);
    onCreateLabel();
  };

  const filteredItems =
    options?.filter((item) =>
      // item.value?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.label?.toLowerCase().includes(searchQuery.toLowerCase()),
    ) || [];

  // Custom required label styles
  const customLabelStyles = required
    ? {
        "& .MuiFormLabel-asterisk": {
          color: red[500],
        },
      }
    : {};

  return (
    <Controller
      render={({
        field: { onChange, value, ref, onBlur: defaultBlur },
        formState: { errors },
      }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;
        return (
          <FormControl
            sx={{ ...sx, ...customLabelStyles }}
            error={isError}
            fullWidth={fullWidth}
            size={rest?.size}
            required={required}
          >
            <InputLabel
              shrink
              sx={{
                background: "background.paper",
                paddingLeft: 1,
                paddingRight: 1,
              }}
            >
              <Typography
                variant="m3"
                fontWeight={"fontWeightMedium"}
                color="text.disabled"
              >
                {rest.label?.endsWith("*") ? (
                  <>
                    {rest.label.slice(0, -1)}
                    <Typography component="span" color="error.main">
                      *
                    </Typography>
                  </>
                ) : (
                  rest.label
                )}
              </Typography>
            </InputLabel>
            <Select
              {...rest}
              onChange={(e) => {
                setIsFocus(false);
                if (e.target.value === "clear") {
                  onChange?.("");
                  rest?.onChange?.("");
                  return;
                }
                onChange?.(e);
                rest?.onChange?.(e);
              }}
              value={value}
              error={isError}
              onOpen={handleOpen}
              open={isOpen}
              onClose={() => (isSearchable && isFocus ? {} : handleClose())}
              inputRef={mergeRefs(forwardedRef, ref)}
              onBlur={() => {
                defaultBlur();
                onBlur?.();
              }}
              sx={{ ...rest?.InputSx }}
              MenuProps={{
                PaperProps: {
                  ref: setRef,
                  sx: {
                    maxHeight: dropDownMaxHeight || 150,
                    display: "flex",
                    flexDirection: "column",
                    paddingTop: theme.spacing(0),
                  },
                },
              }}
              displayEmpty={!rest?.label}
            >
              {isSearchable && isOpen && (
                <Box
                  sx={{
                    backgroundColor: "background.paper",
                    paddingY: theme.spacing(1),
                    position: "sticky",
                    top: 0,
                    zIndex: 2,
                  }}
                >
                  <SearchFieldBox
                    sx={{
                      height: 36,
                      backgroundColor: "background.paper",
                    }}
                  >
                    <IconButton
                      size="small"
                      sx={{ color: theme.palette.divider }}
                    >
                      <Iconify icon="eva:search-fill" />
                    </IconButton>
                    <Input
                      placeholder="Search"
                      disableUnderline
                      value={searchQuery}
                      onChange={handleSearchChange}
                      sx={{ flex: 1 }}
                      onFocus={() => setIsFocus(true)}
                      onBlur={() => setIsFocus(false)}
                      onKeyDown={(e) => e.stopPropagation()}
                    />
                  </SearchFieldBox>
                </Box>
              )}
              {rest?.placeholder && !value && (
                <MenuItem disabled value={value}>
                  {rest?.placeholder}
                </MenuItem>
              )}
              {allowClear && value?.length > 0 && (
                <MenuItem value="clear">Clear Selection</MenuItem>
              )}
              {filteredItems.map((option) => {
                const { value, label, ...rest } = option;
                return (
                  <MenuItem
                    {...rest}
                    key={value}
                    value={valueSelector ? valueSelector(option) : value}
                  >
                    {label}
                  </MenuItem>
                );
              })}

              {createLabel && (
                <LabelButton
                  onClick={handleCreateLabel}
                  sx={{
                    borderTop: `1px solid ${theme.palette.divider}`,
                    position: "sticky",
                    bottom: -4,
                    height: "40px",
                    backgroundColor: "var(--bg-paper) !important",
                    borderRadius: "0 0 0 8px",
                    marginLeft: "-5px",
                    marginRight: "-5px",
                    marginBottom: "-5px",
                    paddingLeft: "12px",
                    "&:hover": {
                      backgroundColor: "var(--bg-neutral) !important",
                    },
                  }}
                >
                  <Iconify icon="eva:plus-fill" /> &nbsp; {createLabel}
                </LabelButton>
              )}

              {loadingMoreOptions && (
                <Box>
                  <Skeleton variant="text" height={34} />
                  <Skeleton variant="text" height={34} />
                  <Skeleton variant="text" height={34} />
                </Box>
              )}
            </Select>
            {(isError || helperText) && (
              <FormHelperText>{errorMessage || helperText}</FormHelperText>
            )}
          </FormControl>
        );
      }}
      control={control}
      name={fieldName}
      defaultValue={defaultValue}
    />
  );
};

const formSelectFieldPropTypes = {
  control: PropTypes.any,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.any.isRequired,
    }),
  ),
  createLabel: PropTypes.string,
  fieldName: PropTypes.string.isRequired,
  valueSelector: PropTypes.func,
  onCreateLabel: PropTypes.func,
  isSearchable: PropTypes.bool,
  formControlProps: PropTypes.oneOfType([PropTypes.object, PropTypes.any]),
  helperText: PropTypes.string,
  fullWidth: PropTypes.bool,
  multiple: PropTypes.bool,
  dropDownMaxHeight: PropTypes.number,
  onScrollEnd: PropTypes.func,
  loadingMoreOptions: PropTypes.bool,
  allowClear: PropTypes.bool,
  contentWidth: PropTypes.bool,
  defaultValue: PropTypes.any,
  onBlur: PropTypes.func,
  sx: PropTypes.object,
  required: PropTypes.bool,
};

FormSelectField.propTypes = formSelectFieldPropTypes;

// @ts-ignore
FormSelectField.propTypes = formSelectFieldPropTypes;
export const EnhancedFormSelectField = React.forwardRef(FormSelectField);
EnhancedFormSelectField.propTypes = formSelectFieldPropTypes;
