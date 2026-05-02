import { Box, InputAdornment, TextField } from "@mui/material";
import PropTypes from "prop-types";
import React, { forwardRef, useCallback, useRef } from "react";
import Iconify from "../iconify";
import { useCombinedRefs } from "src/hooks/use-combined-refs";

const SearchField = forwardRef(
  (
    {
      id,
      label,
      searchedValue,
      setSearchedValue,
      getValue,
      onChange,
      isFocus,
      setIsFocus,
      setOpenDropdown,
      openDropdown,
      sx = {},
      modelRef,
      showIcon,
      logoUrl,
      multiple,
      onClick,
      // Filter ``shrink`` out of ``rest`` — see rhf-text-field.jsx note.
      // eslint-disable-next-line no-unused-vars
      shrink: _shrink,
      ...rest
    },
    ref,
  ) => {
    const blurTimeoutRef = useRef(null);
    const combinedRef = useCombinedRefs(modelRef, ref);

    const handleOnFocus = useCallback(() => {
      if (blurTimeoutRef.current) {
        clearTimeout(blurTimeoutRef.current);
        blurTimeoutRef.current = null;
      }
      setIsFocus(true);
      rest?.onFocus?.();
    }, [rest, setIsFocus]);

    const handleOnBlur = useCallback(() => {
      blurTimeoutRef.current = setTimeout(() => {
        !multiple && setIsFocus(false);
        blurTimeoutRef.current = null;
        !multiple && rest?.onBlur?.();
      }, 300);
    }, [multiple, rest, setIsFocus]);

    const handleOnClear = useCallback(
      (e) => {
        e.stopPropagation();

        if (blurTimeoutRef.current) {
          clearTimeout(blurTimeoutRef.current);
          blurTimeoutRef.current = null;
        }

        setSearchedValue("");

        setIsFocus(true);
        ref.current?.focus();
      },
      [ref, setIsFocus, setSearchedValue],
    );

    const handleOnChange = useCallback(
      (e) => {
        setSearchedValue(e.target.value);
      },
      [setSearchedValue],
    );

    const handleDropdownIconClick = useCallback(
      (e) => {
        e.stopPropagation();
        if (rest?.disabled) return;
        if (isFocus) {
          handleOnBlur();
        } else {
          handleOnFocus();
          ref.current?.focus();
        }
      },
      [rest?.disabled, isFocus, handleOnBlur, handleOnFocus, ref],
    );

    return (
      <TextField
        {...rest}
        focused={isFocus}
        autoComplete="off"
        inputRef={combinedRef}
        type="text"
        label={label}
        // hiddenLabel
        onClick={() => {
          setOpenDropdown(true);
          onClick?.();
        }}
        onChange={handleOnChange}
        onFocus={handleOnFocus}
        onBlur={handleOnBlur}
        value={isFocus ? searchedValue : getValue}
        aria-describedby={id}
        placeholder="Select Option"
        sx={{
          input: { color: "text.secondary" },
          textarea: { color: "text.secondary" },

          "& .MuiOutlinedInput-root": {
            "& fieldset": {
              borderColor: "divider",
              borderBottomLeftRadius: "4px",
              borderBottomRightRadius: "4px",
            },
            "&:hover fieldset": {
              borderColor: "divider",
            },
            "&.Mui-focused fieldset": {
              borderColor: "divider",
              borderBottomLeftRadius: 0,
              borderBottomRightRadius: 0,
            },
          },
          "& .MuiInputBase-input::placeholder": {
            color: "text.disabled",
            opacity: 0.7,
          },
          "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline": {
            borderColor: rest?.error ? "red.500" : "divider", // Or any color you want
          },
          "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
            {
              borderColor: rest?.error ? "red.500" : "divider", // Or any color you want
            },
          ...sx,
        }}
        InputLabelProps={{
          ...rest.InputLabelProps,
          shrink: true,
          sx: {
            color: "text.primary",
          },
        }}
        InputProps={{
          ...rest.InputProps,
          startAdornment:
            showIcon && logoUrl && !isFocus ? (
              <InputAdornment position="start">
                <Box
                  component="img"
                  sx={(theme) => ({
                    width: theme.spacing(2.5),
                    objectFit: "cover",
                    ...(theme.palette.mode === "dark" &&
                      logoUrl?.includes("provider-logos") && {
                        filter: "invert(1) brightness(2)",
                      }),
                  })}
                  src={logoUrl}
                  alt="icon"
                />
              </InputAdornment>
            ) : undefined,
          endAdornment: (
            <InputAdornment position="end">
              {isFocus && searchedValue ? (
                <Iconify
                  icon="mdi:close"
                  sx={{
                    cursor: "pointer",
                    color: "text.primary",
                    width: 20,
                    height: 20,
                  }}
                  onClick={handleOnClear}
                />
              ) : !isFocus && getValue && !rest.disabled ? (
                <Iconify
                  icon="mdi:close"
                  sx={{
                    cursor: "pointer",
                    color: "text.primary",
                    width: 20,
                    height: 20,
                  }}
                  onClick={(e) => onChange(e, true)}
                />
              ) : (
                <Iconify
                  icon="eva:arrow-ios-upward-fill"
                  sx={{
                    cursor: rest?.disabled ? "not-allowed" : "pointer",
                    color: "text.primary",
                    width: 20,
                    height: 20,
                    transform: `rotateX(${openDropdown ? 0 : -180}deg)`,
                    transition: "transform 0.5s",
                  }}
                  onClick={handleDropdownIconClick}
                />
              )}
            </InputAdornment>
          ),
        }}
      />
    );
  },
);

export default SearchField;

SearchField.displayName = "SearchField";

SearchField.propTypes = {
  id: PropTypes.string,
  label: PropTypes.string,
  searchedValue: PropTypes.string,
  setSearchedValue: PropTypes.func,
  getValue: PropTypes.string,
  onChange: PropTypes.func,
  isFocus: PropTypes.bool,
  setIsFocus: PropTypes.func,
  sx: PropTypes.object,
  setOpenDropdown: PropTypes.func,
  modelRef: PropTypes.object,
  showIcon: PropTypes.bool,
  logoUrl: PropTypes.string,
  multiple: PropTypes.bool,
  openDropdown: PropTypes.bool,
  onClick: PropTypes.func,
};
