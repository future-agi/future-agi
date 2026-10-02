//@ts-nocheck
import React, { useRef, useState } from "react";
import { Controller } from "react-hook-form";
import {
  Avatar,
  Box,
  Checkbox,
  FormControl,
  FormHelperText,
  IconButton,
  Input,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import _ from "lodash";
import { SearchFieldBox } from "./FormSelectFieldStyle";
import Iconify from "src/components/iconify";
import { stringAvatar } from "src/utils/utils";
import DropdownWithSearch from "src/sections/common/DropdownWithSearch";

function getStyles(label, personName, theme) {
  return {
    fontWeight: personName.includes(label)
      ? theme.typography.fontWeightMedium
      : theme.typography.fontWeightRegular,
  };
}

const FormMultiSelectField = (
  {
    control,
    options,
    fieldName,
    isSearchable,
    valueSelector,
    helperText,
    fullWidth,
    createLabel,
    dropDownMaxHeight,
    onScrollEnd,
    loadingMoreOptions,
    ...rest
  },
  forwardedRef,
) => {
  const [searchQuery, setSearchQuery] = useState("");
  const theme = useTheme();
  const anchorRef = useRef(null);

  const handleScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    if (scrollTop + clientHeight >= scrollHeight - 5) {
      onScrollEnd?.();
    }
  };

  const handleSearchChange = (event) => {
    setSearchQuery(event.target.value.toLowerCase());
  };

  const filteredItems = options
    ?.filter((item) =>
      item?.label?.toLowerCase().includes(searchQuery.toLowerCase()),
    )
    .filter(Boolean); // Ensure no null or undefined items

  const handleChange = (selectedIds, onChange) => {
    // Map selected labels to their corresponding values
    const selectedValues = selectedIds?.map((id) => {
      const matchedOption = options.find((opt) => opt.id === id);
      return matchedOption ? matchedOption.id : null;
    });

    // Pass values back to Controller
    onChange(selectedValues.filter((val) => val !== null)); // Remove any null values
    rest?.onChange?.(selectedValues.filter((val) => val !== null));
  };

  return (
    <Controller
      render={({ field: { onChange, value }, formState: { errors } }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;
        const data = Array.isArray(value) ? value : [];

        return (
          <FormControl
            ref={forwardedRef}
            error={isError}
            fullWidth={fullWidth}
            size={rest?.size}
          >
            <DropdownWithSearch
              label={rest.label}
              options={options?.map((opt) => ({
                value: opt.id,
                label: opt.label,
              }))}
              value={data}
              multiple={true}
              anchorRef={anchorRef}
              sx={{ width: "100%" }}
              onSelect={(selectedIds) => handleChange(selectedIds, onChange)}
              renderValue={(selected) => (
                <Box
                  sx={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 1,
                    overflow: "hidden",
                  }}
                >
                  {selected?.map((val, idx) => {
                    const selectedOption = options?.find(
                      (opt) => opt.id === val,
                    );
                    if (!selectedOption) return null; // Skip if no matching option is found

                    return (
                      <Stack
                        direction="row"
                        alignItems="center"
                        gap="8px"
                        key={idx}
                      >
                        <Avatar
                          {...stringAvatar(selectedOption.label)}
                          sx={style.avatarStyle}
                        />
                        <Typography
                          sx={{
                            fontSize: "12px",
                            fontWeight: theme.typography.fontWeightRegular,
                            color:
                              theme.palette.mode === "light"
                                ? theme.palette.text.primary
                                : theme.palette.common.white,
                          }}
                        >
                          {selectedOption.label}
                        </Typography>
                      </Stack>
                    );
                  })}
                </Box>
              )}
              popoverComponent={({ anchorElement }) => (
                <Box
                  sx={{
                    width: anchorElement ? anchorElement.clientWidth : 250,
                    maxHeight: dropDownMaxHeight || 400,
                    overflow: "auto",
                  }}
                  onScroll={handleScroll}
                >
                  <SearchFieldBox sx={{}}>
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
                    />
                  </SearchFieldBox>
                  {loadingMoreOptions && (
                    <Box sx={{ p: 1, textAlign: "center" }}>
                      <Typography variant="body2">Loading more...</Typography>
                    </Box>
                  )}
                  {filteredItems?.map((option) => {
                    const { id, label, img } = option || {}; // Safely destructure
                    if (!id || !label) return null; // Skip invalid options

                    return (
                      <MenuItem
                        key={id}
                        value={id}
                        onClick={() => {
                          const newSelected = data.includes(id)
                            ? data?.filter((item) => item !== id)
                            : [...data, id];
                          handleChange(newSelected, onChange);
                        }}
                        style={getStyles(label, data, theme)}
                      >
                        <Checkbox checked={data.includes(id)} />
                        <Avatar
                          alt={label}
                          src={img}
                          {...(!img && stringAvatar(label))}
                          sx={{
                            width: 24,
                            height: 24,
                            mr: "8px",
                            backgroundColor: img
                              ? "background.neutral"
                              : undefined,
                          }}
                        />
                        {label}
                      </MenuItem>
                    );
                  })}
                </Box>
              )}
            />
            {(isError || helperText) && (
              <FormHelperText>{errorMessage || helperText}</FormHelperText>
            )}
          </FormControl>
        );
      }}
      control={control}
      name={fieldName}
    />
  );
};

const style = {
  avatarStyle: {
    width: "20px",
    height: "20px",
    objectFit: "cover",
    fontSize: "12px",
    backgroundColor: "background.neutral",
    color: "pink.500",
  },
};

const formMultiSelectFieldPropTypes = {
  control: PropTypes.any,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.any.isRequired,
      label: PropTypes.string.isRequired,
      img: PropTypes.string,
    }),
  ),
  createLabel: PropTypes.string,
  fieldName: PropTypes.string.isRequired,
  valueSelector: PropTypes.func,
  isSearchable: PropTypes.bool,
  formControlProps: PropTypes.oneOfType([PropTypes.object, PropTypes.any]),
  helperText: PropTypes.string,
  fullWidth: PropTypes.bool,
  dropDownMaxHeight: PropTypes.number,
  onScrollEnd: PropTypes.func,
  loadingMoreOptions: PropTypes.bool,
  onChange: PropTypes.func,
};

FormMultiSelectField.propTypes = formMultiSelectFieldPropTypes;

// @ts-ignore
FormMultiSelectField.propTypes = formMultiSelectFieldPropTypes;
export const EnhancedFormMultiSelectField =
  React.forwardRef(FormMultiSelectField);
EnhancedFormMultiSelectField.propTypes = formMultiSelectFieldPropTypes;
