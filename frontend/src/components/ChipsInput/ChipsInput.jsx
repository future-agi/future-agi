import React, { useState, useRef, forwardRef, useEffect } from "react";
import PropTypes from "prop-types";
import { alpha, Box, Chip, FormHelperText } from "@mui/material";
import { styled } from "@mui/material/styles";
import SvgColor from "../svg-color";

// Styled components
const InputContainer = styled(Box)(() => ({
  position: "relative",
  width: "100%",
}));

const FloatingLabel = styled("label")(({ theme, error }) => ({
  position: "absolute",
  left: theme.spacing(1.75),
  top: -8,
  fontSize: "12px",
  fontWeight: theme.typography.fontWeightMedium,
  color: error ? theme.palette.error.main : theme.palette.text.disabled,
  padding: `0 ${theme.spacing(1)}`,
  pointerEvents: "none",
  zIndex: 1,
  backgroundColor: theme.palette.background.paper,
}));

const ChipsContainer = styled(Box)(({ theme, error }) => ({
  minHeight: "100px",
  padding: `${theme.spacing(2)} ${theme.spacing(1.5)} ${theme.spacing(1)}`,
  border: `1px solid ${error ? theme.palette.error.main : theme.palette.text.disabled}`,
  borderRadius: theme.spacing(0.5),
  backgroundColor: theme.palette.background.paper,
  flexWrap: "wrap",
  gap: theme.spacing(1),
  cursor: "text",
  display: "flex",
  alignItems: "flex-start",
  transition: theme.transitions.create(["border-color"], {
    duration: theme.transitions.duration.short,
  }),
  "&:hover": {
    borderColor: error ? theme.palette.error.main : theme.palette.text.disabled,
  },
}));

export const StyledChip = styled(Chip)(({ theme, invalid }) => ({
  backgroundColor: invalid
    ? alpha(theme.palette.error.main, 0.15)
    : alpha(theme.palette.primary.main, 0.1),
  color: invalid ? theme.palette.error.main : theme.palette.primary.main,
  fontWeight: theme.typography.fontWeightMedium,
  "& .MuiChip-deleteIcon": {
    color: invalid ? theme.palette.error.main : theme.palette.primary.main,
    bgcolor: "transparent !important",
  },
  "&:hover": {
    backgroundColor: invalid
      ? alpha(theme.palette.error.main, 0.25)
      : alpha(theme.palette.primary.main, 0.18),
  },
}));

const StyledInput = styled("input")(({ theme }) => ({
  flex: 1,
  minWidth: "120px",
  border: "none",
  outline: "none",
  backgroundColor: "transparent",
  typography: "s1",
  color: theme.palette.text.primary,
  paddingBlock: theme.spacing(0.5),
  "&::placeholder": {
    color: theme.palette.text.disabled,
    typography: "s1",
    fontWeight: "fontWeightRegular",
  },
}));

const ChipsInput = forwardRef(
  (
    {
      value = [],
      onChange,
      onInputChange,
      onBlur,
      onFocus,
      name,
      placeholder = "",
      error,
      helperText,
      label = "Items",
      sx = {},
      chipContainerStyle = {},
      chipStyle = {},
      limit = Infinity,
      setError,
      validateItem = () => true,
      formatItem = (item) => item.trim(),
      fixedVales = [],
      getErrorMessage = (type) =>
        type === "limit"
          ? "You’ve reached the maximum allowed items."
          : "Invalid input",
    },
    ref,
  ) => {
    const [inputValue, setInputValue] = useState("");
    const inputRef = useRef(null);

    const isValid = (item) => validateItem(item);
    const format = (item) => formatItem(item);

    const addItem = (item) => {
      const formatted = format(item);
      if (formatted && isValid(formatted) && !value.includes(formatted)) {
        if (value.length >= limit) {
          setError?.(name, { message: getErrorMessage("limit") });
          return;
        }
        onChange?.([...value, formatted]);
        setInputValue("");
        onInputChange?.("");
      } else if (!isValid(formatted)) {
        setError?.(name, { message: getErrorMessage("invalid") });
      }
    };

    const removeItem = (toRemove) => {
      if (fixedVales.includes(toRemove)) return;
      const newItems = value.filter((v) => v !== toRemove);
      onChange?.(newItems);
      if (newItems.length < limit) {
        setError?.(name, null);
      }
    };

    useEffect(() => {
      if (inputValue.trim() === "") {
        setError?.(name, null);
      }
    }, [inputValue, setError, name]);

    const handleKeyDown = (e) => {
      if (["Enter", ",", ";"].includes(e.key)) {
        e.preventDefault();
        if (inputValue.trim()) {
          addItem(inputValue);
        }
      } else if (e.key === "Backspace" && !inputValue && value.length > 0) {
        removeItem(value[value.length - 1]);
      }
    };

    const handleBlur = (e) => {
      if (inputValue.trim()) {
        addItem(inputValue);
      }
      onBlur?.(e);
    };

    const handleChange = (e) => {
      const val = e.target.value;
      setInputValue(val);
      onInputChange?.(val);
    };

    const handlePaste = (e) => {
      e.preventDefault();
      const pastedText = e.clipboardData.getData("text");
      const items = pastedText
        .split(/[,;\s]+/)
        .map((item) => format(item))
        .filter((item) => item && isValid(item) && !value.includes(item));

      const allowed = items.slice(0, Math.max(0, limit - value.length));
      if (allowed.length < items.length) {
        setError?.(name, { message: getErrorMessage("limit") });
      }

      if (allowed.length > 0) {
        onChange?.([...value, ...allowed]);
        setInputValue("");
        onInputChange?.("");
      }
    };

    const handleContainerClick = () => {
      inputRef.current?.focus();
    };

    return (
      <Box sx={{ width: "100%", ...sx }}>
        <InputContainer>
          <FloatingLabel error={error} htmlFor={name}>
            {label}
          </FloatingLabel>

          <ChipsContainer
            error={error}
            onClick={handleContainerClick}
            sx={chipContainerStyle}
          >
            {value.map((item) => (
              <StyledChip
                key={item}
                label={item}
                onDelete={
                  fixedVales.includes(item) ? undefined : () => removeItem(item)
                }
                size="small"
                invalid={!isValid(item)}
                sx={chipStyle}
                deleteIcon={
                  fixedVales.includes(item) ? null : (
                    <SvgColor
                      sx={{ height: 12, width: 12, bgcolor: "primary.main" }}
                      src="/assets/icons/ic_close.svg"
                    />
                  )
                }
              />
            ))}

            <StyledInput
              ref={(el) => {
                inputRef.current = el;
                if (ref) {
                  if (typeof ref === "function") ref(el);
                  else ref.current = el;
                }
              }}
              type="text"
              value={inputValue}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onBlur={handleBlur}
              onFocus={onFocus}
              onPaste={handlePaste}
              placeholder={!value.length ? placeholder : ""}
              name={name}
              id={name}
              size={"small"}
            />
          </ChipsContainer>
        </InputContainer>

        {(error || helperText) && (
          <FormHelperText error={!!error} sx={{ mt: 1 }}>
            {error || helperText}
          </FormHelperText>
        )}
      </Box>
    );
  },
);

ChipsInput.displayName = "ChipsInput";

ChipsInput.propTypes = {
  value: PropTypes.arrayOf(PropTypes.string),
  onChange: PropTypes.func,
  onInputChange: PropTypes.func,
  onBlur: PropTypes.func,
  onFocus: PropTypes.func,
  name: PropTypes.string,
  placeholder: PropTypes.string,
  error: PropTypes.bool,
  helperText: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
  chipContainerStyle: PropTypes.object,
  chipStyle: PropTypes.object,
  limit: PropTypes.number,
  setError: PropTypes.func,
  validateItem: PropTypes.func,
  formatItem: PropTypes.func,
  getErrorMessage: PropTypes.func,
  fixedVales: PropTypes.array,
};

export default ChipsInput;
