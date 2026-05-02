import { InputAdornment, TextField, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useMemo,
  useRef,
  useState,
  useCallback,
  useLayoutEffect,
} from "react";
import Iconify from "../iconify";
import FormSelectMenus from "./FormSelectMenus";
import { mergeRefs } from "src/utils/utils";

const FormSearchSelectFieldState = React.forwardRef(
  (
    {
      label,
      value,
      onChange,
      options = [],
      showClear = true,
      createLabel,
      multiple = false,
      placeholder = "Select Option",
      sx,
      // Filter ``shrink`` out of ``rest`` — see rhf-text-field.jsx note.
      // eslint-disable-next-line no-unused-vars
      shrink: _shrink,
      ...rest
    },
    ref,
  ) => {
    const [focus, setFocus] = useState(false);
    const [openDropdown, setOpenDropdown] = useState(false);
    const [searchedValue, setSearchedValue] = useState("");
    const inputRef = useRef(null);
    const [textFieldWidth, setTextFieldWidth] = useState(0);
    const containerRef = useRef(null);
    const [position, setPosition] = useState("bottom");
    const theme = useTheme();

    useLayoutEffect(() => {
      if (containerRef.current) {
        const updateWidth = () => {
          const width = containerRef.current.getBoundingClientRect().width;
          setTextFieldWidth(width);
        };

        updateWidth();

        // Update on window resize
        window.addEventListener("resize", updateWidth);
        return () => window.removeEventListener("resize", updateWidth);
      }
    }, []);

    const onClose = () => {
      setOpenDropdown(false);
      setSearchedValue("");
    };

    const handleOpen = useCallback(() => {
      if (rest?.disabled) return;
      setOpenDropdown(true);
      setSearchedValue("");

      const boxRect = containerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - boxRect.bottom;
      const spaceAbove = boxRect.top;

      if (spaceBelow < 200 && spaceAbove > spaceBelow) {
        setPosition("top");
      } else {
        setPosition("bottom");
      }
    }, [rest?.disabled]);

    const handleOnFocus = useCallback(() => {
      setFocus(true);
      handleOpen();
      rest?.onFocus?.();
    }, [handleOpen, rest]);

    const handleOnBlur = () => {
      rest.onBlur?.();
      setFocus(false);
    };

    const handleOnClear = useCallback(
      (e) => {
        e.stopPropagation();
        setSearchedValue("");
        handleOpen();
        inputRef.current?.focus();
      },
      [handleOpen],
    );

    const handleOnChange = useCallback((e) => {
      setSearchedValue(e.target.value);
    }, []);

    const handleDropdownIconClick = useCallback(
      (e) => {
        e.stopPropagation();
        if (rest?.disabled) return;
        if (openDropdown) {
          onClose();
        } else {
          handleOpen();
          inputRef.current?.focus();
        }
      },
      [handleOpen, openDropdown, rest?.disabled],
    );

    const getValue = useMemo(() => {
      if (multiple) {
        const selectedOptions = options?.filter((item) =>
          value?.includes(item.value),
        );
        return selectedOptions?.length > 0
          ? selectedOptions.map((item) => item.label).join(", ")
          : "";
      } else {
        const option = options?.find((item) => item.value === value);
        return option?.label || value || "";
      }
    }, [value, options, multiple]);

    const id = useMemo(
      () => (openDropdown ? `${label}-popper` : undefined),
      [openDropdown, label],
    );

    const displayValue = useMemo(() => {
      if (openDropdown && (focus || searchedValue !== "")) {
        return searchedValue;
      }
      return getValue;
    }, [openDropdown, focus, searchedValue, getValue]);

    return (
      <>
        <TextField
          {...rest}
          ref={containerRef}
          autoComplete="off"
          inputRef={mergeRefs(inputRef, ref)}
          type="text"
          label={label}
          placeholder={placeholder}
          onChange={handleOnChange}
          onFocus={handleOnFocus}
          onClick={handleOpen}
          onBlur={handleOnBlur}
          value={displayValue}
          aria-describedby={id}
          sx={{
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
                borderBottomLeftRadius:
                  position === "bottom" ? 0 : theme.spacing(0.5),
                borderBottomRightRadius:
                  position === "bottom" ? 0 : theme.spacing(0.5),
                borderTopLeftRadius:
                  position === "top" ? 0 : theme.spacing(0.5),
                borderTopRightRadius:
                  position === "top" ? 0 : theme.spacing(0.5),
              },
            },
            "& .MuiOutlinedInput-notchedOutline legend": {
              width:
                (label?.length || 0) > 7
                  ? `${(label?.length || 0) - 1}ch`
                  : `${label?.length || 0}ch`,
            },
            input: { color: "text.secondary" },
            textarea: { color: "text.secondary" },
            "& .MuiInputBase-input::placeholder": {
              color: "text.disabled",
              opacity: 0.7,
            },
            "& .MuiFormLabel-asterisk": {
              color: (theme) => theme.palette.error.main,
            },
            // ✅ Add these for hover and focus border color
            "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline": {
              borderColor: rest?.error ? "red.500" : "action.hover", // Or any color you want
            },
            "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
              {
                borderColor: rest?.error ? "red.500" : "action.hover", // Or any color you want
              },
            ...sx,
          }}
          InputLabelProps={{
            ...rest.InputLabelProps,
            shrink: true,
            style: {
              paddingLeft: 1,
              paddingRight: 2,
              background: "var(--bg-paper)",
            },
          }}
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                {openDropdown && searchedValue && showClear ? (
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
                ) : !multiple &&
                  !openDropdown &&
                  getValue &&
                  !rest.disabled &&
                  showClear ? (
                  <Iconify
                    icon="mdi:close"
                    sx={{
                      cursor: "pointer",
                      color: "text.primary",
                      width: 20,
                      height: 20,
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      onChange({ target: { value: multiple ? [] : "" } });
                    }}
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
            ...rest.InputProps,
          }}
        />
        <FormSelectMenus
          id={id}
          inputRef={containerRef}
          open={openDropdown}
          menuPosition={position}
          anchorWidth={textFieldWidth}
          onClose={onClose}
          value={value}
          options={options}
          onChange={onChange}
          searchedValue={searchedValue}
          setSearchedValue={setSearchedValue}
          multiple={multiple}
          createLabel={createLabel}
          {...rest}
        />
      </>
    );
  },
);

FormSearchSelectFieldState.displayName = "FormSearchSelectFieldState";

export default FormSearchSelectFieldState;

FormSearchSelectFieldState.propTypes = {
  value: PropTypes.any,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.any.isRequired,
      disabled: PropTypes.bool,
    }),
  ),
  showClear: PropTypes.bool,
  label: PropTypes.string,
  onChange: PropTypes.func,
  sx: PropTypes.object,
  createLabel: PropTypes.string,
  multiple: PropTypes.bool,
  placeholder: PropTypes.string,
};
