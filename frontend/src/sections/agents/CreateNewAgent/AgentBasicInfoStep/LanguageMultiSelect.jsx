import React, { useState, useRef, useMemo } from "react";
import {
  Box,
  MenuItem,
  FormControl,
  Chip,
  IconButton,
  Input,
  Typography,
  Checkbox,
  FormHelperText,
  Popover,
  InputLabel,
  Select,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import Iconify from "src/components/iconify";

const chipStyles = {
  backgroundColor: "background.neutral",
  color: "text.primary",
  borderRadius: "2px",
  fontWeight: "fontWeightMedium",
  fontSize: "12px",
  height: 25,
  "& .MuiChip-label": {
    paddingTop: 0,
    paddingBottom: 0,
    lineHeight: "28px", // vertically center text
  },
  "& .MuiChip-deleteIcon": {
    color: "text.primary",
    opacity: 1, // always visible
  },
  "&:hover": {
    backgroundColor: "background.neutral",
    color: "text.primary",
  },
};

const LanguageMultiSelect = ({
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
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);

  const handleClose = () => {
    setOpen(false);
  };

  const handleScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    if (scrollTop + clientHeight >= scrollHeight - 5) {
      onScrollEnd?.();
    }
  };

  const handleSearchChange = (event) => {
    setSearchQuery(event.target.value.toLowerCase());
  };

  const filteredItems = useMemo(() => {
    return (
      options?.filter((item) =>
        item?.label?.toLowerCase()?.includes(searchQuery?.toLowerCase()),
      ) || []
    );
  }, [options, searchQuery]);

  const handleChange = (selectedIds, onChange) => {
    const selectedValues = selectedIds?.map((id) => {
      const matchedOption = options.find((opt) => opt.id === id);
      return matchedOption ? matchedOption.id : null;
    });

    onChange(selectedValues.filter((val) => val !== null));
    rest?.onChange?.(selectedValues.filter((val) => val !== null));
  };

  return (
    <Controller
      render={({ field: { onChange, value }, formState: { errors } }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;
        const data = Array.isArray(value) ? value : [];

        return (
          <FormControl error={isError} fullWidth={fullWidth} size={rest?.size}>
            <InputLabel
              required={rest?.required}
              shrink
              error={rest?.error}
              sx={{
                background: "background.paper",
                paddingLeft: 1,
                paddingRight: 1,
                fontWeight: 500,
              }}
            >
              {rest.label}
            </InputLabel>
            <Select
              size="small"
              value={data || []}
              open={open}
              onOpen={() => setOpen(true)}
              onClose={handleClose}
              label={rest.label}
              multiple={true}
              onChange={() => {}}
              ref={anchorRef}
              displayEmpty
              sx={{
                "& .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                  transition: "border-radius 0.2s ease",
                  borderBottomLeftRadius: open ? 0 : 4,
                  borderBottomRightRadius: open ? 0 : 4,
                },
                "&:hover .MuiOutlinedInput-notchedOutline": {
                  borderColor: "action.hover",
                },
                "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
                  borderColor: "action.hover",
                },
              }}
              IconComponent={() => (
                <Iconify
                  icon="eva:arrow-ios-upward-fill"
                  sx={{
                    width: 25,
                    height: 25,
                    mr: 1.4,
                    color: "text.primary",
                    cursor: rest?.disabled ? "not-allowed" : "pointer",
                    transform: `rotateX(${open ? 0 : -180}deg)`,
                    transition: "transform 0.5s",
                  }}
                />
              )}
              MenuProps={{
                PaperProps: {
                  style: {
                    display: "none",
                  },
                },
              }}
              renderValue={(selected) => {
                // Show placeholder when nothing is selected
                if (selected.length === 0) {
                  return (
                    <Typography
                      typography={"s1"}
                      sx={{
                        color: "text.disabled",
                        opacity: 0.7,
                      }}
                    >
                      {rest.placeholder || "Select..."}
                    </Typography>
                  );
                }

                // Show chips when items are selected
                return (
                  <Box
                    sx={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 0.5,
                      overflow: "hidden",
                      py: 0.3,
                    }}
                  >
                    {selected?.map((val, idx) => {
                      const selectedOption = options?.find(
                        (opt) => opt.id === val,
                      );
                      if (!selectedOption) return null;

                      return (
                        <Chip
                          key={idx}
                          label={selectedOption.label}
                          size="small"
                          onDelete={() => {
                            const newSelected = data.filter(
                              (item) => item !== val,
                            );
                            handleChange(newSelected, onChange);
                          }}
                          deleteIcon={
                            <IconButton
                              size="small"
                              sx={{
                                p: 0.5,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                color: "text.primary",
                              }}
                              onMouseDown={(e) => e.stopPropagation()}
                              onClick={(e) => {
                                e.stopPropagation();
                                const newSelected = data.filter(
                                  (item) => item !== val,
                                );
                                handleChange(newSelected, onChange);
                              }}
                            >
                              <Iconify
                                icon="mdi:close"
                                height={18}
                                width={18}
                                color="text.primary"
                              />
                            </IconButton>
                          }
                          sx={chipStyles}
                        />
                      );
                    })}
                  </Box>
                );
              }}
              {...rest}
            />
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
              PaperProps={{
                sx: {
                  borderRadius: "0px 0px 0px 0px !important",
                },
              }}
            >
              <Box
                sx={{
                  width: anchorRef?.current
                    ? anchorRef.current.clientWidth - 9
                    : 250,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.5,
                    height: 40,
                    px: 1,
                    m: 1,
                    backgroundColor: "background.paper",
                  }}
                >
                  <IconButton
                    size="small"
                    sx={{ color: "text.disabled", p: 0 }}
                  >
                    <Iconify icon="eva:search-fill" height={20} width={20} />
                  </IconButton>

                  <Input
                    placeholder="Search"
                    disableUnderline
                    value={searchQuery}
                    onChange={handleSearchChange}
                    sx={{
                      flex: 1,
                      ml: 0.5,
                      height: "100%",
                      "& input": {
                        padding: 0,
                        height: "100%",
                        boxSizing: "border-box",
                        "&::placeholder": {
                          color: "text.disabled",
                          opacity: 1,
                        },
                      },
                    }}
                  />
                </Box>

                <Box
                  sx={{
                    maxHeight: dropDownMaxHeight || 400,
                    overflow: "auto",
                    "&::-webkit-scrollbar": { width: "6px" },
                    "&::-webkit-scrollbar-thumb": {
                      backgroundColor: "rgba(0, 0, 0, 0.3)",
                      borderRadius: "3px",
                    },
                    "&::-webkit-scrollbar-track": {
                      backgroundColor: "transparent",
                    },
                  }}
                  onScroll={handleScroll}
                >
                  {loadingMoreOptions && (
                    <Box sx={{ p: 1, textAlign: "center" }}>
                      <Typography variant="body2">Loading more...</Typography>
                    </Box>
                  )}
                  {filteredItems?.map((option) => {
                    const { id, label: optionLabel } = option || {};
                    if (!id || !optionLabel) return null;
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
                        sx={{ paddingY: 0, margin: 0 }}
                      >
                        <Checkbox
                          checked={data.includes(id)}
                          icon={
                            <Iconify
                              icon="system-uicons:checkbox-empty"
                              color="action.default"
                              width={24}
                              height={24}
                              style={{ display: "block" }}
                            />
                          }
                          checkedIcon={
                            <Iconify
                              icon="famicons:checkbox"
                              color="primary.light"
                              width={24}
                              height={24}
                              style={{ display: "block" }}
                            />
                          }
                          sx={{
                            color: "divider",
                          }}
                        />
                        <Typography
                          typography={"s2_1"}
                          fontWeight={"fontWeightMedium"}
                        >
                          {optionLabel}
                        </Typography>
                      </MenuItem>
                    );
                  })}
                </Box>
              </Box>
            </Popover>
            {(isError || helperText) && (
              <FormHelperText sx={{ ml: 0 }}>
                {errorMessage || helperText}
              </FormHelperText>
            )}
          </FormControl>
        );
      }}
      control={control}
      name={fieldName}
    />
  );
};

LanguageMultiSelect.propTypes = {
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

export default LanguageMultiSelect;
