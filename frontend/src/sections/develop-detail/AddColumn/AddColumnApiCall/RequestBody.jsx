import {
  Box,
  FormHelperText,
  MenuItem,
  Paper,
  Typography,
  useTheme,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useController } from "react-hook-form";

const startRegex = /.*{{[^}\s]*(?!}})$/;
const variablePattern = /({{[^}]*}}|{{[^}]*$)/g;

const RequestBody = ({
  control,
  contentFieldName,
  allColumns,
  placeholder,
  showHelper = true,
  multiline = true,
  label,
  sx = {},
}) => {
  const theme = useTheme();
  const [showDropdown, setShowDropdown] = useState(false);
  const [dropdownPosition, setDropdownPosition] = useState({ x: 0, y: 0 });
  const inputRef = useRef(null);
  const backdropRef = useRef(null);
  const dropdownRef = useRef(null);
  const [selectedIndex, setSelectedIndex] = useState(0);

  const { field, formState } = useController({
    control,
    name: contentFieldName,
  });

  const value =
    typeof field.value === "string"
      ? field.value
      : typeof field.value === "object"
        ? JSON.stringify(field.value, null, 2)
        : "";

  const { errors } = formState;
  const errorMessage = _.get(errors, `${contentFieldName}.message`) || "";
  const isError = !!errorMessage;

  // Valid column names for highlighting
  const columnNameSet = useMemo(
    () => new Set(allColumns.map((c) => c.headerName)),
    [allColumns],
  );

  // Highlighted text: green for valid variables, red for invalid/incomplete
  const highlightedContent = useMemo(() => {
    if (!value) return multiline ? "\n" : "\u00a0";
    const parts = value.split(variablePattern);
    return parts.map((part, i) => {
      if (!part) return null;
      // Complete {{...}}
      const completeMatch = part.match(/^{{(.*)}}$/);
      if (completeMatch) {
        const isValid = columnNameSet.has(completeMatch[1].trim());
        return (
          <span key={i} style={{ color: isValid ? "#4caf50" : "#f44336" }}>
            {part}
          </span>
        );
      }
      // Incomplete {{... (still typing)
      if (/^{{[^}]*$/.test(part)) {
        return (
          <span key={i} style={{ color: "#f44336" }}>
            {part}
          </span>
        );
      }
      return <span key={i}>{part}</span>;
    });
  }, [value, columnNameSet, multiline]);

  // Search text for autocomplete filtering
  const searchText = useMemo(() => {
    const el = inputRef.current;
    if (!el) return "";
    const { selectionStart } = el;
    const textBeforeCursor = value.substring(0, selectionStart);
    if (!startRegex.test(textBeforeCursor)) return "";
    const idx = textBeforeCursor.lastIndexOf("{{");
    return textBeforeCursor.substring(idx + 2, selectionStart);
  }, [value]);

  // Filtered column options for dropdown
  const columnOptions = useMemo(() => {
    return allColumns.reduce((filtered, column) => {
      if (
        column?.headerName
          ?.toLowerCase()
          .startsWith(searchText.toLowerCase())
      ) {
        filtered.push({
          label: column.headerName,
          value: `{{${column.headerName}}}`,
        });
      }
      return filtered;
    }, []);
  }, [allColumns, searchText]);

  const onCloseDropdown = useCallback(() => {
    setShowDropdown(false);
    setSelectedIndex(0);
  }, []);

  // Calculate dropdown position
  const setDropDownPos = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();

    if (!multiline) {
      // Single-line: just below the input
      setDropdownPosition({ x: rect.left, y: rect.bottom + 2 });
      return;
    }

    // Multiline: mirror div technique for caret position
    const { selectionStart } = el;
    const style = getComputedStyle(el);

    const div = document.createElement("div");
    div.style.position = "absolute";
    div.style.visibility = "hidden";
    div.style.whiteSpace = "pre-wrap";
    div.style.wordWrap = "break-word";
    div.style.boxSizing = style.boxSizing;
    div.style.width = style.width;
    div.style.fontSize = style.fontSize;
    div.style.fontFamily = style.fontFamily;
    div.style.lineHeight = style.lineHeight;
    div.style.letterSpacing = style.letterSpacing;
    div.style.padding = style.padding;
    div.style.borderWidth = style.borderWidth;
    div.style.borderStyle = style.borderStyle;
    div.style.borderColor = "transparent";

    div.appendChild(
      document.createTextNode(el.value.substring(0, selectionStart)),
    );
    const span = document.createElement("span");
    span.textContent = "\u200b";
    div.appendChild(span);
    document.body.appendChild(div);
    const sLeft = span.offsetLeft;
    const sTop = span.offsetTop;
    const sHeight = span.offsetHeight;
    document.body.removeChild(div);

    setDropdownPosition({
      x: rect.left + sLeft - el.scrollLeft,
      y: rect.top + sTop + sHeight - el.scrollTop,
    });
  }, [multiline]);

  const handleChange = (content) => {
    field.onChange(content);
    const el = inputRef.current;
    if (!el) return;
    const { selectionStart } = el;
    const textBeforeCursor = content.substring(0, selectionStart);

    if (startRegex.test(textBeforeCursor) && allColumns.length > 0) {
      setDropDownPos();
      setShowDropdown(true);
    } else {
      onCloseDropdown();
    }
  };

  useEffect(() => {
    const onResize = () => {
      if (showDropdown) setDropDownPos();
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [setDropDownPos, showDropdown]);

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!showDropdown) return;
    const handler = (e) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target) &&
        inputRef.current &&
        !inputRef.current.contains(e.target)
      ) {
        onCloseDropdown();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showDropdown, onCloseDropdown]);

  // Sync backdrop scroll with textarea
  const handleScroll = () => {
    if (backdropRef.current && inputRef.current) {
      backdropRef.current.scrollTop = inputRef.current.scrollTop;
      backdropRef.current.scrollLeft = inputRef.current.scrollLeft;
    }
  };

  const handleVariableSelect = (variable) => {
    const el = inputRef.current;
    if (!el) return;
    const { selectionStart } = el;
    const content = el.value;
    const textBeforeCursor = content.substring(0, selectionStart);
    const lastIdx = textBeforeCursor.lastIndexOf("{{");

    const newContent =
      content.substring(0, lastIdx) +
      variable.value +
      content.substring(selectionStart);
    field.onChange(newContent);
    onCloseDropdown();

    setTimeout(() => {
      const pos = lastIdx + variable.value.length;
      el.selectionStart = pos;
      el.selectionEnd = pos;
      el.focus();
    }, 0);
  };

  const onKeyDown = (e) => {
    if (showDropdown && columnOptions.length > 0) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseDropdown();
      } else if (e.key === "ArrowDown") {
        e.stopPropagation();
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % columnOptions.length);
      } else if (e.key === "ArrowUp") {
        e.stopPropagation();
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev > 0 ? prev - 1 : columnOptions.length - 1,
        );
      } else if (e.key === "Enter") {
        e.stopPropagation();
        e.preventDefault();
        handleVariableSelect(columnOptions[selectedIndex]);
      }
    } else if (multiline) {
      e.stopPropagation();
    }
  };

  const sharedTextStyle = {
    fontFamily: "inherit",
    fontSize: "14px",
    lineHeight: "1.5",
    letterSpacing: "normal",
    boxSizing: "border-box",
    ...(multiline
      ? { whiteSpace: "pre-wrap", wordWrap: "break-word" }
      : { whiteSpace: "nowrap", overflow: "hidden" }),
  };

  const inputPadding = multiline ? "8px" : "8.5px 12px";

  return (
    <Box
      sx={{
        paddingX: multiline ? 2 : 0,
        paddingBottom: showHelper ? 1 : 0,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      {label && (
        <Typography
          variant="body2"
          sx={{ color: "text.secondary", fontSize: "0.75rem" }}
        >
          {label}
        </Typography>
      )}
      {/* Container with highlight backdrop + input */}
      <div
        style={{
          position: "relative",
          backgroundColor: theme.palette.background.default,
          borderRadius: "8px",
        }}
      >
        {/* Highlight backdrop — same text with colored variables */}
        <div
          ref={backdropRef}
          style={{
            ...sharedTextStyle,
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            padding: inputPadding,
            border: "1px solid transparent",
            borderRadius: "8px",
            pointerEvents: "none",
            color: theme.palette.text.primary,
            overflow: "hidden",
          }}
        >
          {highlightedContent}
        </div>
        {/* Actual textarea — text is transparent, caret is visible */}
        <textarea
          rows={multiline ? undefined : 1}
          onBlur={field.onBlur}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onScroll={handleScroll}
          ref={(r) => {
            inputRef.current = r;
            field.ref(r);
          }}
          placeholder={
            placeholder === ""
              ? ""
              : placeholder || (multiline ? "Write JSON here..." : "")
          }
          style={{
            ...sharedTextStyle,
            width: "100%",
            padding: inputPadding,
            ...(multiline
              ? { minHeight: "200px", resize: "none" }
              : { resize: "none", overflow: "hidden" }),
            border: `1px solid ${isError ? theme.palette.error.main : theme.palette.divider}`,
            borderRadius: "8px",
            outline: "none",
            color: "transparent",
            caretColor: theme.palette.text.primary,
            backgroundColor: "transparent",
            position: "relative",
            verticalAlign: "top",
            ...sx,
          }}
          onKeyDown={onKeyDown}
        />
      </div>

      {/* Dropdown via createPortal — guaranteed to escape overflow:hidden */}
      {showDropdown &&
        columnOptions.length > 0 &&
        createPortal(
          <Paper
            ref={dropdownRef}
            elevation={3}
            sx={{
              position: "fixed",
              top: dropdownPosition.y,
              left: dropdownPosition.x,
              zIndex: 9999,
              p: 0.5,
              maxHeight: 150,
              overflow: "auto",
            }}
            role="listbox"
          >
            {columnOptions.map((variable, index) => (
              <MenuItem
                key={variable.value}
                onClick={() => handleVariableSelect(variable)}
                selected={index === selectedIndex}
                sx={{
                  minWidth: 150,
                  backgroundColor:
                    index === selectedIndex
                      ? "background.neutral"
                      : "inherit",
                  "&:hover": { backgroundColor: "action.hover" },
                  "&:focus": { outline: "none" },
                }}
              >
                {variable.label}
              </MenuItem>
            ))}
          </Paper>,
          document.body,
        )}

      {allColumns.length > 0 && showHelper && (
        <Typography color="text.secondary" variant="subtitle2" fontWeight={400}>
          use
          <Typography component="span" color="primary">
            {" {{ "}
          </Typography>
          to access variables
        </Typography>
      )}
      {!!isError && (
        <FormHelperText sx={{ paddingLeft: 1, marginTop: 0 }} error={!!isError}>
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

RequestBody.propTypes = {
  control: PropTypes.object,
  contentFieldName: PropTypes.string,
  allColumns: PropTypes.array,
  placeholder: PropTypes.string,
  showHelper: PropTypes.bool,
  multiline: PropTypes.bool,
  label: PropTypes.string,
  sx: PropTypes.object,
};

export default RequestBody;
