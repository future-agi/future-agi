import {
  Box,
  CircularProgress,
  InputAdornment,
  MenuItem,
  Popover,
  Skeleton,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { Controller } from "react-hook-form";
import Iconify from "src/components/iconify";

const SCROLL_END_THRESHOLD_PX = 5;

const SearchableSelectContent = ({
  value,
  onChange,
  options,
  isPending,
  isFetchingNextPage,
  fetchNextPage,
  hasNextPage,
  searchQuery,
  onSearchChange,
  getOptionLabel,
  getOptionValue,
  placeholder,
  searchPlaceholder,
  emptyMessage,
  buttonSx,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const selectedLabelRef = useRef(null);

  const selectedOption = options?.find((o) => getOptionValue(o) === value);
  if (selectedOption) {
    selectedLabelRef.current = getOptionLabel(selectedOption);
  }

  const handleListScroll = (e) => {
    const { scrollTop, clientHeight, scrollHeight } = e.currentTarget;
    if (scrollTop + clientHeight < scrollHeight - SCROLL_END_THRESHOLD_PX)
      return;
    if (isPending || isFetchingNextPage || !hasNextPage) return;
    fetchNextPage?.();
  };

  const handleOpen = () => setOpen(true);
  const handleClose = () => setOpen(false);

  return (
    <>
      {/* Trigger button */}
      <Box
        ref={anchorRef}
        onClick={handleOpen}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 22,
          px: 1,
          minWidth: 50,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          cursor: "pointer",
          bgcolor: "background.paper",
          "&:hover": {
            borderColor: "text.disabled",
            bgcolor: "background.default",
          },
          ...buttonSx,
        }}
      >
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 500,
            color: "text.primary",
            maxWidth: 120,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            lineHeight: 1,
          }}
        >
          {selectedLabelRef.current || placeholder}
        </Typography>
        {isPending ? (
          <CircularProgress size={10} sx={{ color: "text.disabled" }} />
        ) : (
          <Iconify
            icon="eva:chevron-down-fill"
            width={14}
            height={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
      </Box>

      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        transformOrigin={{ vertical: "top", horizontal: "center" }}
        disableAutoFocus
        PaperProps={{
          elevation: 4,
          sx: {
            mt: 0.5,
            borderRadius: 1,
            overflow: "hidden",
            maxWidth: 160,
          },
        }}
      >
        {/* Search field */}
        <Box sx={{ p: 1, pb: 0.5 }}>
          <TextField
            fullWidth
            autoFocus
            size="small"
            placeholder={searchPlaceholder}
            value={searchQuery ?? ""}
            onChange={(e) => onSearchChange?.(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="eva:search-fill"
                    width={16}
                    height={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{
              "& .MuiOutlinedInput-root": {
                fontSize: 13,
                height: 26,
                "& fieldset": { borderColor: "divider" },
                "&:hover fieldset": { borderColor: "text.disabled" },
              },
              "& .MuiInputBase-input::placeholder": {
                color: "text.disabled",
                opacity: 1,
              },
            }}
          />
        </Box>

        {/* Options list */}
        <Box
          sx={{ maxHeight: 220, overflowY: "auto", py: 0.5 }}
          onScroll={handleListScroll}
        >
          {isPending && (!options || options.length === 0) ? (
            Array.from({ length: 5 }).map((_, i) => (
              <Box key={i} sx={{ px: 1.5, py: 0.75 }}>
                <Skeleton
                  variant="rectangular"
                  sx={{ borderRadius: 0.5 }}
                  width={`${60 + (i % 3) * 15}%`}
                  height={20}
                />
              </Box>
            ))
          ) : !isPending && (!options || options.length === 0) ? (
            <Box sx={{ p: 0.5, textAlign: "center" }}>
              <Typography
                variant="s2"
                color="text.secondary"
                fontWeight={"fontWeightRegular"}
              >
                {emptyMessage ||
                  (searchQuery ? "No results found" : "No options available")}
              </Typography>
            </Box>
          ) : (
            <>
              {options.map((option) => {
                const optionValue = getOptionValue(option);
                const isSelected = optionValue === value;
                return (
                  <MenuItem
                    key={optionValue}
                    onClick={() => {
                      onChange(optionValue);
                      handleClose();
                    }}
                    dense
                    sx={{
                      px: 1.5,
                      py: 0.625,
                      minHeight: "unset",
                      bgcolor: isSelected ? "action.selected" : "transparent",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Typography
                      sx={{
                        fontSize: 13,
                        fontWeight: isSelected ? 500 : 400,
                        color: "text.primary",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {getOptionLabel(option)}
                    </Typography>
                  </MenuItem>
                );
              })}
              {isFetchingNextPage &&
                Array.from({ length: 2 }).map((_, i) => (
                  <Box key={`skel-more-${i}`} sx={{ px: 1.5, py: 0.75 }}>
                    <Skeleton
                      variant="rectangular"
                      sx={{ borderRadius: 0.5 }}
                      width={`${60 + (i % 3) * 15}%`}
                      height={20}
                    />
                  </Box>
                ))}
            </>
          )}
        </Box>
      </Popover>
    </>
  );
};

const SearchableSelectControl = ({
  control,
  fieldName,
  defaultValue = "",
  ...rest
}) => (
  <Controller
    control={control}
    name={fieldName}
    defaultValue={defaultValue}
    render={({ field: { value, onChange } }) => (
      <SearchableSelectContent value={value} onChange={onChange} {...rest} />
    )}
  />
);

export default SearchableSelectControl;

SearchableSelectContent.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array,
  isPending: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  hasNextPage: PropTypes.bool,
  searchQuery: PropTypes.string,
  onSearchChange: PropTypes.func,
  getOptionLabel: PropTypes.func,
  getOptionValue: PropTypes.func,
  placeholder: PropTypes.string,
  searchPlaceholder: PropTypes.string,
  emptyMessage: PropTypes.string,
  buttonSx: PropTypes.object,
};

SearchableSelectControl.propTypes = {
  control: PropTypes.object.isRequired,
  fieldName: PropTypes.string.isRequired,
  defaultValue: PropTypes.any,
};
