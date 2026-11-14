import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import {
  Box,
  Checkbox,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Skeleton,
  Stack,
  Button,
  TextField,
  InputAdornment,
  Typography,
  ListItemIcon,
  Radio,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { LabelButton } from "../FormSelectField/FormSelectFieldStyle";
import { ShowComponent } from "../show";
import SvgColor from "../svg-color";

const SkeletonItem = () => (
  <Box sx={{ display: "flex", gap: 1, padding: 1, alignItems: "center" }}>
    <Skeleton variant="rounded" width={20} height={15} />
    <Skeleton variant="rounded" width="100%" height={15} />
  </Box>
);

const CustomSelectField = ({
  control,
  name,
  data,
  isPending,
  fetchNextPage,
  hasNextPage,
  isFetchingNextPage,
  searchQuery = "",
  onSearchChange,
  width = 265,
  height = 238,
  getOptionLabel = (option) => option?.name,
  getOptionValue = (option) => option?.id,
  multiple = false,
  onClose = null,
  label,
  error,
  placeholder,
  onRefresh,
  zeroOptionsMessage,
  zeroOptionsActionLabel = "New Prompt",
  zeroOptionsActionUrl = "/dashboard/workbench/all",
}) => {
  return (
    <Controller
      name={name}
      control={control}
      defaultValue={multiple ? [] : null}
      render={({ field: { value, onChange } }) => (
        <SelectContent
          options={data}
          loading={isPending}
          multiple={multiple}
          value={value}
          onChange={onChange}
          hasMore={hasNextPage}
          onLoadMore={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          searchQuery={searchQuery}
          onSearchChange={onSearchChange}
          getOptionLabel={getOptionLabel}
          getOptionValue={getOptionValue}
          width={width}
          height={height}
          error={error}
          label={label}
          placeholder={placeholder}
          onClose={onClose}
          onRefresh={onRefresh}
          zeroOptionsMessage={zeroOptionsMessage}
          zeroOptionsActionLabel={zeroOptionsActionLabel}
          zeroOptionsActionUrl={zeroOptionsActionUrl}
        />
      )}
    />
  );
};

const SelectContent = ({
  options,
  loading,
  multiple,
  value,
  onChange,
  hasMore,
  onLoadMore,
  isFetchingNextPage,
  searchQuery,
  onSearchChange,
  getOptionLabel,
  zeroOptionsActionLabel,
  zeroOptionsActionUrl,
  getOptionValue,
  height,
  width,
  onClose,
  onRefresh,
  zeroOptionsMessage = null,
  error,
}) => {
  const [localSelection, setLocalSelection] = useState(() => {
    if (multiple) {
      return Array.isArray(value) ? [...value] : [];
    }
    return value;
  });

  // Sync local selection with form value
  useEffect(() => {
    if (multiple) {
      setLocalSelection(Array.isArray(value) ? [...value] : []);
    } else {
      setLocalSelection(value);
    }
  }, [value, multiple]);

  const handleSearchChange = (e) => {
    if (onSearchChange) {
      onSearchChange(e.target.value);
    }
  };

  const handleSelect = useCallback(
    (option) => {
      if (multiple) {
        const currentValues = Array.isArray(localSelection)
          ? localSelection
          : [];
        const optionValue = getOptionValue(option);
        const isSelected = currentValues.some(
          (v) => getOptionValue(v) === optionValue,
        );

        const newValue = isSelected
          ? currentValues.filter((v) => getOptionValue(v) !== optionValue)
          : [...currentValues, option];

        setLocalSelection(newValue);
      } else {
        setLocalSelection(option);
        onChange(option);
      }
    },
    [multiple, localSelection, getOptionValue, onChange],
  );

  const isSelected = useCallback(
    (option) => {
      const optionValue = getOptionValue(option);
      if (multiple) {
        return Array.isArray(localSelection)
          ? localSelection.some((v) => getOptionValue(v) === optionValue)
          : false;
      }
      return localSelection
        ? getOptionValue(localSelection) === optionValue
        : false;
    },
    [multiple, localSelection, getOptionValue],
  );

  const handleClear = useCallback(() => {
    if (multiple) {
      setLocalSelection([]);
    } else {
      setLocalSelection(null);
    }
  }, [multiple]);

  const handleAdd = useCallback(() => {
    onChange(localSelection);
    handleClose();
  }, [onChange, localSelection]);

  const handleLoadMore = useCallback(() => {
    if (!loading && !isFetchingNextPage && hasMore) {
      onLoadMore();
    }
  }, [loading, isFetchingNextPage, hasMore, onLoadMore]);

  const scrollContainer = useScrollEnd(handleLoadMore, [handleLoadMore]);

  const handleClose = () => {
    if (onClose) {
      onClose?.();
    }
  };

  // No items at all (no active search) — show empty state with action buttons
  if (zeroOptionsMessage && !searchQuery && !loading) {
    return (
      <Box
        sx={{
          height: height,
          width: width,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <Stack spacing={0.5} alignItems="center">
          <Typography
            typography={"s2"}
            fontWeight={"fontWeightRegular"}
            align="center"
          >
            {zeroOptionsMessage}
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<SvgColor width={16} src="/assets/icons/ic_add.svg" />}
            onClick={() => window.open(zeroOptionsActionUrl, "_blank")}
          >
            {zeroOptionsActionLabel}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor width={16} src="/assets/icons/ic_reload.svg" />
            }
            onClick={onRefresh}
          >
            Refresh
          </Button>
        </Stack>
      </Box>
    );
  }

  // API error — show error state with refresh
  if (error) {
    return (
      <Box
        sx={{
          height: height,
          width: width,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <Stack spacing={0.5} alignItems="center">
          <SvgColor
            sx={{ width: 24, height: 24 }}
            src="/assets/icons/ic_not_found.svg"
          />
          <Typography typography={"s2"} fontWeight={"fontWeightRegular"}>
            Cannot connect to Prompt Workbench.
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor width={16} src="/assets/icons/ic_reload.svg" />
            }
            onClick={onRefresh}
          >
            Refresh
          </Button>
        </Stack>
      </Box>
    );
  }
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: height,
        width: width,
        gap: 0.25,
      }}
    >
      {/* Search Field */}
      {onSearchChange && (
        <Box>
          <TextField
            placeholder="Search..."
            size="small"
            fullWidth
            autoFocus
            value={searchQuery}
            onChange={handleSearchChange}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={16}
                    color={"text.disabled"}
                  />
                </InputAdornment>
              ),
            }}
            sx={{
              "& .MuiInputBase-root": {
                height: "26px",
                // border: "1px solid",
                // borderRadius: "2px",
              },
              "& .MuiInputBase-input::placeholder": {
                color: "text.disabled",
              },
            }}
          />
        </Box>
      )}

      {/* Options List */}
      <List
        sx={{
          width: "100%",

          overflowY: "scroll",
        }}
        dense
        ref={scrollContainer}
      >
        {!loading && options?.length > 0 && (
          <LabelButton
            onClick={() => {
              window.open(zeroOptionsActionUrl, "_blank");
            }}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              border: 0,
              marginTop: 0,
            }}
          >
            <SvgColor
              sx={{
                color: "primary.main",
                height: 20,
                width: 20,
              }}
              src={"/assets/icons/ic_add.svg"}
            />
            <Typography
              component="span"
              typography={"s1"}
              fontWeight={"fontWeightMedium"}
            >
              {zeroOptionsActionLabel}
            </Typography>
          </LabelButton>
        )}

        {loading ? (
          <>
            <SkeletonItem />
            <SkeletonItem />
            <SkeletonItem />
          </>
        ) : options?.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              gap: 1,
              py: 8,
              height: "100%",
            }}
          >
            <Typography
              typography={"s1"}
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
            >
              No results found
            </Typography>
            <Button
              variant="outlined"
              size="small"
              startIcon={<SvgColor width={16} src="/assets/icons/ic_add.svg" />}
              onClick={() => window.open(zeroOptionsActionUrl, "_blank")}
            >
              {zeroOptionsActionLabel}
            </Button>
          </Box>
        ) : (
          <>
            {options?.map((option) => {
              const labelId = `select-list-label-${getOptionValue(option)}`;
              const selected = isSelected(option);

              return (
                <ListItem key={getOptionValue(option)} disablePadding>
                  <ListItemButton
                    role={undefined}
                    onClick={() => handleSelect(option)}
                    dense
                    sx={{
                      borderRadius: 1,
                      display: "flex",
                      flexDirection: "row",
                      alignItems: "center",
                      marginLeft: 0.5,
                      "&:hover": {
                        backgroundColor: "action.hover",
                      },
                    }}
                  >
                    <ListItemIcon>
                      <ShowComponent condition={multiple}>
                        <Checkbox
                          edge="start"
                          checked={selected}
                          tabIndex={-1}
                          disableRipple
                          inputProps={{ "aria-labelledby": labelId }}
                          sx={{
                            padding: 0,
                            color: "divider",
                            "&.Mui-checked": {
                              color: "primary.main",
                            },
                          }}
                        />
                      </ShowComponent>
                      <ShowComponent condition={!multiple}>
                        <Radio
                          edge="start"
                          tabIndex={-1}
                          disableRipple
                          inputProps={{ "aria-labelledby": labelId }}
                          sx={{ padding: 0 }}
                          checked={selected}
                        />
                      </ShowComponent>
                    </ListItemIcon>

                    <ListItemText
                      id={labelId}
                      primary={getOptionLabel(option)}
                      primaryTypographyProps={{
                        typography: "s1",
                        textOverflow: "ellipsis",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                        fontWeight: "fontWeightRegular",
                      }}
                      sx={{
                        marginLeft: -1,
                      }}
                    />
                  </ListItemButton>
                </ListItem>
              );
            })}

            {/* Loading more indicator */}
            {isFetchingNextPage && (
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  p: 2,
                  paddingLeft: 0,
                }}
              >
                <SkeletonItem />
                <SkeletonItem />
              </Box>
            )}
          </>
        )}
      </List>
      {/* Action Buttons - Only for multiple selection */}
      {multiple && (
        <Stack
          direction="row"
          spacing={1}
          sx={{
            position: "sticky",
            paddingTop: 0.5,
            borderTop: 1,
            marginTop: "auto",
            bottom: 0,
            borderColor: "divider",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 0.5,
          }}
        >
          <ShowComponent condition={Boolean(onRefresh)}>
            <Button
              sx={{ color: "primary.main" }}
              startIcon={
                <SvgColor width={16} src="/assets/icons/ic_reload.svg" />
              }
              onClick={onRefresh}
            >
              Refresh
            </Button>
          </ShowComponent>

          <Button
            size="small"
            onClick={handleClear}
            variant="outlined"
            sx={{
              borderColor: "divider",
              color: "text.primary",
            }}
          >
            Clear
          </Button>
          <Button
            variant="contained"
            size="small"
            onClick={handleAdd}
            sx={{
              backgroundColor: "primary.main",
              color: "primary.contrastText",
              "&:hover": {
                backgroundColor: "primary.dark",
              },
            }}
          >
            Add
          </Button>
        </Stack>
      )}
    </Box>
  );
};

CustomSelectField.propTypes = {
  control: PropTypes.object.isRequired,
  name: PropTypes.string.isRequired,
  data: PropTypes.object,
  isPending: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  hasNextPage: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  searchQuery: PropTypes.string,
  onSearchChange: PropTypes.func,
  getOptionLabel: PropTypes.func,
  getOptionValue: PropTypes.func,
  multiple: PropTypes.bool,
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  label: PropTypes.string,
  placeholder: PropTypes.string,
  onClose: PropTypes.func,
  error: PropTypes.string,
  onRefresh: PropTypes.func,
  zeroOptionsMessage: PropTypes.string,
  zeroOptionsActionLabel: PropTypes.string,
  zeroOptionsActionUrl: PropTypes.string,
};

SelectContent.propTypes = {
  options: PropTypes.array.isRequired,
  loading: PropTypes.bool.isRequired,
  multiple: PropTypes.bool.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  hasMore: PropTypes.bool,
  onLoadMore: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
  searchQuery: PropTypes.string,
  onSearchChange: PropTypes.func,
  getOptionLabel: PropTypes.func.isRequired,
  getOptionValue: PropTypes.func.isRequired,
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  label: PropTypes.string,
  placeholder: PropTypes.string,
  onClose: PropTypes.func,
  error: PropTypes.string,
  onRefresh: PropTypes.func,
  zeroOptionsMessage: PropTypes.string,
  zeroOptionsActionLabel: PropTypes.string,
  zeroOptionsActionUrl: PropTypes.string,
};

export default CustomSelectField;
