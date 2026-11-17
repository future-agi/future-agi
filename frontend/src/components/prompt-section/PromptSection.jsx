import {
  Box,
  FormHelperText,
  IconButton,
  MenuItem,
  Paper,
  Tooltip,
  useTheme,
} from "@mui/material";
import React, { useState, useRef, useEffect } from "react";
import "react-quill/dist/quill.snow.css";
import Iconify from "src/components/iconify";
import "./PromptSection.css"; // Create this CSS file
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import _ from "lodash";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import logger from "src/utils/logger";

export const PromptSection = ({
  control,
  onRemove,
  allColumns,
  jsonSchemas = {},
  roleSelectDisabled,
  prefixControlString,
  contentSuffix = "content",
  roleSuffix = "role",
  hideSelectRole = false,
}) => {
  const theme = useTheme();
  const [showDropdown, setShowDropdown] = useState(false);
  const [, setCursorPosition] = useState(0);
  const quillRef = useRef(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [, setIsEnterPressed] = useState(false); // State to track Enter key press
  const [dropdownPosition, setDropdownPosition] = useState({
    top: 0,
    left: 0,
  });
  const [searchText, setSearchText] = useState("");
  const [filteredVariables, setFilteredVariables] = useState([]);
  const dropdownRef = useRef(null);

  const contentFieldName = `${prefixControlString}${contentSuffix.length > 0 ? "." : ""}${contentSuffix}`;

  const roleFieldName = `${prefixControlString}${roleSuffix.length > 0 ? "." : ""}${roleSuffix}`;

  const { field, formState } = useController({
    control,
    name: contentFieldName,
  });

  const { errors } = formState;

  const errorMessage = _.get(errors, `${contentFieldName}.message`) || "";
  const isError = !!errorMessage;

  // Update filtered variables when search text changes
  useEffect(() => {
    // Build expanded options including JSON paths
    const expandedOptions = [];

    (allColumns || []).forEach((col) => {
      // Add base column
      expandedOptions.push({
        ...col,
        headerName: col.headerName,
        isJsonPath: false,
      });

      // Add JSON paths for JSON-type columns
      if (col?.dataType === "json" && jsonSchemas?.[col?.field]) {
        const schema = jsonSchemas[col?.field];
        schema?.keys?.forEach((path) => {
          expandedOptions.push({
            ...col,
            headerName: `${col.headerName}.${path}`,
            isJsonPath: true,
            parentColumn: col.headerName,
          });
        });
      }

      // Add indexed options for images-type columns (maxImagesCount comes from jsonSchemas)
      const imagesSchema = jsonSchemas?.[col?.field];
      if (col?.dataType === "images" && imagesSchema?.maxImagesCount) {
        for (let idx = 0; idx < imagesSchema.maxImagesCount; idx++) {
          expandedOptions.push({
            ...col,
            headerName: `${col.headerName}[${idx}]`,
            isImagesIndex: true,
            parentColumn: col.headerName,
          });
        }
      }
    });

    if (!searchText) {
      setFilteredVariables(expandedOptions);
      return;
    }

    const filtered = expandedOptions.filter(
      (column) =>
        column.headerName.toLowerCase().includes(searchText.toLowerCase()) ||
        column.value?.toLowerCase().includes(searchText?.toLowerCase()),
    );
    logger.debug({ expandedOptions });
    setFilteredVariables(filtered);
  }, [searchText, allColumns, jsonSchemas]);

  const getCaretCoordinates = (element, position) => {
    // Create a div to mirror the textarea
    const div = document.createElement("div");
    const style = getComputedStyle(element);

    // Copy all relevant styles
    const properties = [
      "fontFamily",
      "fontSize",
      "fontWeight",
      "fontStyle",
      "letterSpacing",
      "lineHeight",
      "textTransform",
      "wordSpacing",
      "padding",
      "borderWidth",
      "boxSizing",
    ];

    properties.forEach((prop) => {
      div.style[prop] = style[prop];
    });

    // Set essential styles
    div.style.position = "absolute";
    div.style.top = "0";
    div.style.left = "0";
    div.style.visibility = "hidden";
    div.style.whiteSpace = "pre-wrap";
    div.style.wordWrap = "break-word";
    div.style.overflow = "hidden";
    div.style.width = `${element.offsetWidth}px`;

    // Create content up to cursor position
    const content = element.value.substring(0, position);
    div.textContent = content;

    // Create a span for cursor position
    const span = document.createElement("span");
    span.textContent = element.value.substring(position) || ".";
    div.appendChild(span);

    document.body.appendChild(div);

    // Calculate coordinates
    const { offsetLeft: spanLeft, offsetTop: spanTop } = span;
    // Get scroll positions
    const scrollLeft = element.scrollLeft || 0;
    const scrollTop = element.scrollTop || 0;

    // Calculate final coordinates
    const coordinates = {
      left: spanLeft - scrollLeft,
      top: spanTop - scrollTop,
      height: parseInt(style.lineHeight, 10),
    };

    // Clean up
    document.body.removeChild(div);

    return coordinates;
  };

  const adjustTextareaHeight = (element) => {
    if (!element) return;

    // Reset height temporarily to get the correct scrollHeight
    element.style.height = "auto";

    // Set new height
    const newHeight = Math.min(
      Math.max(element.scrollHeight, 100), // minimum 100px, use scrollHeight if larger
      400, // maximum height
    );

    element.style.height = `${newHeight}px`;

    // If content exceeds max height, enable scrolling
    if (element.scrollHeight > 400) {
      element.style.overflowY = "auto";
    } else {
      element.style.overflowY = "hidden";
    }
  };

  // Add resize observer
  useEffect(() => {
    const textarea = quillRef.current;
    if (!textarea) return;

    const resizeObserver = new ResizeObserver(() => {
      adjustTextareaHeight(textarea);
    });

    resizeObserver.observe(textarea);
    return () => resizeObserver.disconnect();
  }, []);

  // Adjust height when content changes
  useEffect(() => {
    adjustTextareaHeight(quillRef.current);
  }, [field.value]);

  const handleChange = (content) => {
    field.onChange(content);
    const textarea = quillRef.current;
    const cursorPosition = textarea.selectionStart;

    // Check for {{ anywhere in the text
    const textBeforeCursor = content.substring(0, cursorPosition);
    const lastOpenBraceIndex = textBeforeCursor.lastIndexOf("{{");

    if (lastOpenBraceIndex !== -1) {
      // Get caret coordinates at the cursor position
      const caretCoords = getCaretCoordinates(textarea, cursorPosition);

      // // Get textarea's absolute position on screen
      // const textareaRect = textarea.getBoundingClientRect();
      // const viewportHeight = window.innerHeight;
      // const dropdownHeight = 200;

      // // Calculate absolute position for dropdown
      // let left = textareaRect.left + caretCoords.left;
      // let top = textareaRect.top + caretCoords.top + caretCoords.height;

      // // Check if there's enough space below
      // const spaceBelow = viewportHeight - (textareaRect.top + caretCoords.top + caretCoords.height);
      // const spaceAbove = textareaRect.top + caretCoords.top;

      // // If not enough space below, allow partial overlap
      // if (spaceBelow < dropdownHeight) {
      //   // Calculate how much we can show below
      //   const visibleBelow = Math.min(spaceBelow, dropdownHeight);
      //   const visibleAbove = dropdownHeight - visibleBelow;

      //   // Position dropdown to show as much as possible below
      //   top = textareaRect.top + caretCoords.top + caretCoords.height - (dropdownHeight - visibleBelow);
      // }

      // // Adjust if dropdown would go off right side of screen
      // if (left + 200 > window.innerWidth) {
      //   left = window.innerWidth - 200;
      // }

      // // Ensure dropdown stays within viewport
      // top = Math.max(top, 0);
      // left = Math.max(left, 0);
      // console.log(caretCoords);
      setDropdownPosition({
        top: caretCoords.top, // Convert back to relative coordinates
        left: caretCoords.left,
      });

      // Get text between {{ and cursor
      const query = textBeforeCursor.substring(
        lastOpenBraceIndex + 2,
        cursorPosition,
      );
      setSearchText(query);
      setShowDropdown(true);
      setSelectedIndex(0);
    } else {
      // setShowDropdown(false);
      setSearchText("");
    }

    adjustTextareaHeight(textarea);
  };

  // Track cursor movement
  const handleCursorMove = (e) => {
    if (showDropdown) {
      setCursorPosition(e.target.selectionStart);
    }
  };

  const handleVariableSelect = (variable) => {
    const textarea = quillRef.current;
    const cursorPosition = textarea.selectionStart;
    const content = textarea.value;
    const textBeforeCursor = content.substring(0, cursorPosition);

    // Find the last occurrence of '{{' before cursor
    const lastOpenBraceIndex = textBeforeCursor.lastIndexOf("{{");

    if (lastOpenBraceIndex !== -1) {
      // Create new content by replacing text between {{ and cursor with variable
      const newContent =
        content.substring(0, lastOpenBraceIndex) +
        `{{${variable.headerName}}}` +
        content.substring(cursorPosition);

      field.onChange(newContent);

      // Move cursor after the inserted variable
      setTimeout(() => {
        const newPosition = lastOpenBraceIndex + variable.headerName.length + 4; // +4 for {{ and }}
        textarea.selectionStart = newPosition;
        textarea.selectionEnd = newPosition;
        // textarea.focus();
      }, 0);
    }

    setShowDropdown(false);
    setSearchText("");
  };

  // Function to scroll selected item into view
  const scrollSelectedIntoView = (index) => {
    if (!dropdownRef.current) return;

    const menuItem =
      dropdownRef.current.children[0].children[0].children[index];
    if (!menuItem) return;

    const dropdownRect = dropdownRef.current.getBoundingClientRect();
    const itemRect = menuItem.getBoundingClientRect();

    if (itemRect.bottom > dropdownRect.bottom) {
      // Scroll down if item is below viewport
      menuItem.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else if (itemRect.top < dropdownRect.top) {
      // Scroll up if item is above viewport
      menuItem.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  };

  const handleKeyDown = (e) => {
    // Prevent scroll propagation when using arrow keys in textarea
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown"].includes(e.key)) {
      e.stopPropagation();
    }
    if (showDropdown && filteredVariables.length > 0) {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelectedIndex((prev) => {
            const newIndex = prev < filteredVariables.length - 1 ? prev + 1 : 0;
            // Scroll into view after state update
            setTimeout(() => scrollSelectedIntoView(newIndex), 0);
            return newIndex;
          });
          break;

        case "ArrowUp":
          e.preventDefault();
          setSelectedIndex((prev) => {
            const newIndex = prev > 0 ? prev - 1 : filteredVariables.length - 1;
            // Scroll into view after state update
            setTimeout(() => scrollSelectedIntoView(newIndex), 0);
            return newIndex;
          });
          break;

        case "Enter":
          e.preventDefault(); // Prevent form submission and new line
          if (filteredVariables[selectedIndex]) {
            handleVariableSelect(filteredVariables[selectedIndex]);
          }
          return false; // Prevent any default behavior

        case "Escape":
          e.preventDefault();
          // setShowDropdown(false);
          setSearchText("");
          break;

        case "Tab":
          if (showDropdown) {
            e.preventDefault();
            if (filteredVariables[selectedIndex]) {
              handleVariableSelect(filteredVariables[selectedIndex]);
            }
          }
          break;

        default:
          break;
      }
    } else if (e.key === "Enter") {
      // Handle normal Enter key behavior when dropdown is not shown
      const textarea = quillRef.current;
      const cursorPosition = textarea.selectionStart;
      const content = textarea.value;
      const textBeforeCursor = content.substring(0, cursorPosition);

      // Check if we just completed a variable insertion
      if (textBeforeCursor.endsWith("}}")) {
        e.preventDefault();
        const newContent =
          content.substring(0, cursorPosition) +
          "\n" +
          content.substring(cursorPosition);

        field.onChange(newContent);

        // Move cursor to new line
        setTimeout(() => {
          textarea.selectionStart = cursorPosition + 1;
          textarea.selectionEnd = cursorPosition + 1;
        }, 0);
      }
    }
  };

  const onKeyUpInTextarea = (e) => {
    if (e.key === "Enter") {
      setIsEnterPressed(false); // Reset the flag when Enter is released
    }
  };

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        // setShowDropdown(false);
        setSearchText("");
      }
    };

    // Add event listener when dropdown is shown
    if (showDropdown) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    // Cleanup event listener
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [showDropdown]);

  const copyPromptSection = () => {
    copyToClipboard(field?.value);
    enqueueSnackbar({
      variant: "success",
      message: "Response copied to clipboard",
    });
  };

  return (
    <Box
      sx={{
        width: "100%",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "8px",
        position: "relative",
      }}
    >
      <Box
        sx={{
          padding: "10px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Box>
          {!hideSelectRole && (
            <FormSelectField
              disabled={roleSelectDisabled}
              control={control}
              fieldName={roleFieldName}
              size="small"
              options={[
                { value: "user", label: "User" },
                { value: "assistant", label: "Assistant" },
                { value: "system", label: "System" },
              ]}
            />
          )}
        </Box>
        <Box>
          {onRemove ? (
            <IconButton onClick={onRemove}>
              <Iconify
                icon="solar:trash-bin-trash-bold"
                sx={{ color: "text.disabled" }}
              />
            </IconButton>
          ) : (
            <></>
          )}
          <Tooltip title="Copy" arrow>
            <IconButton onClick={copyPromptSection} disabled={!field?.value}>
              <Iconify
                icon="solar:copy-linear"
                sx={{ color: "text.disabled" }}
              />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      <div style={{ position: "relative" }}>
        <textarea
          ref={quillRef}
          value={field.value}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onKeyUp={onKeyUpInTextarea}
          onClick={handleCursorMove}
          placeholder="Write your prompt here..."
          style={{
            width: "100%",
            minHeight: "220px",
            padding: "8px",
            border: "none",
            resize: "none",
            fontFamily: "inherit",
            outline: "none",
            backgroundColor: theme.palette.background.default,
            color: theme.palette.text.primary,
            verticalAlign: "top",
            overflowY: "auto", // Ensure only vertical scroll
            lineHeight: "1.5",
            fontSize: "13px",
            transition: "height 0.1s ease-out",
            "&::placeholder": {
              color: theme.palette.text.secondary,
              opacity: 0.7,
            },
          }}
          onWheel={(e) => {
            // Prevent scroll propagation when using mouse wheel
            e.stopPropagation();
            const textarea = e.currentTarget;
            const isAtTop = textarea.scrollTop === 0;
            const isAtBottom =
              textarea.scrollHeight - textarea.scrollTop ===
              textarea.clientHeight;

            if ((isAtTop && e.deltaY < 0) || (isAtBottom && e.deltaY > 0)) {
              e.preventDefault();
            }
          }}
        />
      </div>
      {!!isError && (
        <FormHelperText sx={{ paddingLeft: 1, marginTop: 0 }} error={!!isError}>
          {errorMessage}
        </FormHelperText>
      )}
      {showDropdown && (
        <div
          ref={dropdownRef}
          style={{
            position: "absolute",
            top: `${dropdownPosition.top + 80}px`,
            left: `${dropdownPosition.left}px`,
            backgroundColor: "var(--bg-paper)",
            borderRadius: "4px",
            zIndex: 1,
            maxHeight: "120px",
            overflowY: "auto",
            scrollBehavior: "smooth",
          }}
        >
          <Paper elevation={0}>
            <Box sx={{ p: 0.5 }}>
              {filteredVariables.map((variable, index) => (
                <MenuItem
                  key={variable.headerName}
                  onClick={() => handleVariableSelect(variable)}
                  selected={index === selectedIndex}
                  sx={{
                    minWidth: 100,
                    backgroundColor:
                      index === selectedIndex
                        ? "background.neutral"
                        : "inherit",
                    "&:hover": {
                      backgroundColor: "background.default",
                    },
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 2,
                    padding: "8px 16px",
                    transition: "background-color 0.2s",
                  }}
                >
                  <span>{variable.headerName}</span>
                </MenuItem>
              ))}
            </Box>
          </Paper>
        </div>
      )}
    </Box>
  );
};

PromptSection.propTypes = {
  control: PropTypes.object,
  onRemove: PropTypes.func,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  roleSelectDisabled: PropTypes.bool,
  prefixControlString: PropTypes.string,
  contentSuffix: PropTypes.string,
  roleSuffix: PropTypes.string,
  hideSelectRole: PropTypes.bool,
};
