import {
  Box,
  FormHelperText,
  MenuItem,
  Paper,
  Popper,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { camelCaseToTitleCase } from "src/utils/utils";
import HelperText from "../HelperText";
import "./RulePrompt.css";
import { useController, useFormState, useWatch } from "react-hook-form";
import _ from "lodash";

const startRegex = /.*{{[^}\s]*(?!}})$/;

const RulePromptInput = ({
  setIsRulePrompt,
  setRulePromptData,
  rulePromptData,
  isRulePrompt,
  control,
  config,
  configKey,
  ruleStringKey,
  allColumns,
  jsonSchemas = {},
  isEvaluatation = false,
  allInputColumns,
  isAnnotation = false,
}) => {
  const quillRef = useRef(null);
  const theme = useTheme();
  const [showDropdown, setShowDropdown] = useState(false);
  const [dropdownPosition, setDropdownPosition] = useState({ x: 0, y: 0 });
  const [selectedIndex, setSelectedIndex] = useState(0);
  const popperRef = useRef(null);
  const fieldName = `config.config.${configKey}`;
  const ruleStringFieldName = `config.config.${ruleStringKey}`;

  let ruleStringValue = useWatch({ control, name: ruleStringFieldName });

  const { field } = useController({ control, name: fieldName });

  const searchText = useMemo(() => {
    const textarea = quillRef.current;
    if (!textarea) return "";
    const { selectionStart } = textarea;

    const textBeforeCursor = field.value.substring(0, selectionStart);
    if (!startRegex.test(textBeforeCursor)) return "";
    const lastOpenBracketIndex = textBeforeCursor.lastIndexOf("{{");

    return textBeforeCursor.substring(lastOpenBracketIndex + 2, selectionStart);
  }, [field.value]);

  const options = useMemo(() => {
    let allColumnsMap = allColumns?.reduce((acc, curr) => {
      acc[curr.field] = curr.headerName;
      return acc;
    }, {});

    // Helper to add JSON paths to options
    const addJsonPaths = (baseOptions) => {
      const expandedOptions = [...baseOptions];
      allColumns?.forEach((col) => {
        if (col?.dataType === "json" && jsonSchemas?.[col?.field]) {
          const schema = jsonSchemas[col?.field];
          schema?.keys?.forEach((path) => {
            const fullPath = `${col.headerName}.${path}`;
            expandedOptions.push({
              label: fullPath,
              value: `{{${fullPath}}}`,
              isJsonPath: true,
            });
          });
        }
      });
      return expandedOptions;
    };

    if (isEvaluatation) {
      allColumnsMap = allColumns?.reduce((acc, curr) => {
        acc[curr.name] = curr.value;
        return acc;
      }, {});

      ruleStringValue = allColumns?.map((acc, index) => {
        acc["id"] = acc.name;
        acc["value"] = allInputColumns[index]?.value;
        return acc;
      }, {});

      const baseOptions =
        ruleStringValue
          ?.filter((item) => item?.value?.length > 0)
          ?.map((item) => ({
            label: item?.name,
            value: `{{${item?.name}}}`,
          })) || [];

      return addJsonPaths(baseOptions)?.filter(({ label }) => {
        return label?.toLowerCase().startsWith(searchText.toLowerCase());
      });
    }
    if (!ruleStringValue?.length) {
      ruleStringValue = allColumns?.map((i, index) => {
        return {
          id: index + 1,
          name: `variable_${index + 1}`,
          value: i?.headerName || "",
        };
      });
    }

    const baseOptions =
      ruleStringValue
        ?.filter((item) => item?.value?.length > 0)
        ?.map((item) => ({
          label: allColumnsMap[item?.value],
          value: `{{${allColumnsMap[item?.value]}}}`,
        })) || [];

    return addJsonPaths(baseOptions)?.filter(({ label }) => {
      return label?.toLowerCase().startsWith(searchText.toLowerCase());
    });
  }, [allColumns, ruleStringValue, searchText, jsonSchemas]);

  const { errors } = useFormState({ control });

  const errorMessage = _.get(errors, fieldName)?.message || "";
  const isError = !!errorMessage;

  const onCloseDropdown = () => {
    setShowDropdown(false);
    setSelectedIndex(0);
  };
  const getCaretCoordinates = (element, position) => {
    const { offsetLeft, offsetTop } = element;
    const div = document.createElement("div");
    const style = getComputedStyle(element);

    div.style.fontSize = style.fontSize;
    div.style.fontFamily = style.fontFamily;
    div.style.padding = style.padding;
    div.style.position = "absolute";
    div.style.whiteSpace = "pre-wrap";
    div.textContent = element.value.substring(0, position);

    const span = document.createElement("span");
    span.textContent = element.value.substring(position) || ".";
    div.appendChild(span);

    document.body.appendChild(div);
    const coordinates = {
      left: offsetLeft + span.offsetLeft,
      top: offsetTop + span.offsetTop,
      height: span.offsetHeight,
    };
    document.body.removeChild(div);

    return coordinates;
  };

  const setDropDownPos = useCallback(() => {
    const textarea = quillRef.current;
    const { selectionStart } = textarea;

    const cursorCoords = getCaretCoordinates(textarea, selectionStart);

    const rect = textarea.getBoundingClientRect();

    const dropDownPos = {
      x: rect.left + cursorCoords.left - 20,
      y: rect.top + cursorCoords.height + 10,
    };
    setDropdownPosition(dropDownPos);
  }, []);

  const handleChange = (content) => {
    setIsRulePrompt(false);
    field.onChange(content);
    const textarea = quillRef.current;
    const { selectionStart } = textarea;

    // Get text up to cursor position only
    const textBeforeCursor = content.substring(0, selectionStart);

    if (startRegex.test(textBeforeCursor)) {
      setDropDownPos();
      setShowDropdown(true);
    } else {
      onCloseDropdown();
    }
  };

  const handleVariableSelect = (variable) => {
    const textarea = quillRef.current;
    const { selectionStart } = textarea;
    const content = textarea.value;
    const textBeforeCursor = content.substring(0, selectionStart);

    // Find the last occurrence of {{ before cursor
    const lastOpenBracketIndex = textBeforeCursor.lastIndexOf("{{");

    // Create new content by replacing text between {{ and cursor with variable
    const newContent =
      content.substring(0, lastOpenBracketIndex) +
      `${variable.value}` +
      content.substring(selectionStart);
    field.onChange(newContent);
    onCloseDropdown();

    // Set cursor position after the inserted variable
    setTimeout(() => {
      const newCursorPosition = lastOpenBracketIndex + variable.value.length;
      textarea.selectionEnd = newCursorPosition;
      textarea.selectionStart = newCursorPosition;
    }, 0);
  };

  const onKeyDownInTextarea = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") {
      onCloseDropdown();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : options.length - 1));
    } else if (e.key === "Enter" && showDropdown) {
      e.preventDefault();
      handleVariableSelect(options[selectedIndex]);
    }
  };

  const helperText =
    "Enter a rule prompt that will be evaluated using the available {{column_name}} placeholders";
  const configHelperText =
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  useEffect(() => {
    window.addEventListener("resize", setDropDownPos);
    return () => window.removeEventListener("resize", setDropDownPos);
  }, [setDropDownPos]);

  useEffect(() => {
    if (setRulePromptData) setRulePromptData(field.value);
  }, [field.value, setRulePromptData]);

  return (
    <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
      <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
        <Typography
          variant="body2"
          sx={{
            fontWeight: isAnnotation
              ? theme.typography.fontWeightMedium
              : theme.typography.fontWeightRegular,
          }}
        >
          {isAnnotation
            ? `${camelCaseToTitleCase(configKey)}*`
            : camelCaseToTitleCase(configKey)}
        </Typography>
        <HelperText text={configHelperText} />
      </Box>
      <Box>
        <textarea
          ref={quillRef}
          value={field.value}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={isAnnotation ? helperText : "Write a prompt here..."}
          style={{
            width: "100%",
            minHeight: "100px",
            padding: "8px",
            border: "1px solid",
            borderColor: theme.palette.divider,
            borderRadius: theme.shape.borderRadius,
            resize: "none",
            fontFamily: "inherit",
            outline: "none",
            backgroundColor: theme.palette.background.default,
            verticalAlign: "top",
            color: theme.palette.text.primary,
          }}
          onKeyDown={onKeyDownInTextarea}
        />
        <Popper
          ref={popperRef}
          open={showDropdown}
          sx={{
            zIndex: 9999,
            "&:focus": { outline: "none" },
          }}
          placement="bottom-start"
          disablePortal={false}
          role="listbox"
          style={{
            position: "absolute",
            transform: `translate(${dropdownPosition.x}px, ${dropdownPosition.y}px)`,
          }}
          tabIndex={0}
        >
          <Paper
            elevation={3}
            sx={{ p: 0.5, maxHeight: 150, overflow: "auto" }}
          >
            {options.map((variable, index) => (
              <MenuItem
                key={variable.value}
                onClick={() => handleVariableSelect(variable)}
                selected={index === selectedIndex}
                sx={{
                  minWidth: 150,
                  backgroundColor:
                    index === selectedIndex ? "background.neutral" : "inherit",
                  "&:hover": {
                    backgroundColor: "action.hover",
                  },
                  "&:focus": {
                    outline: "none",
                  },
                }}
              >
                {variable.label}
              </MenuItem>
            ))}
          </Paper>
        </Popper>
        {(!!isError || helperText) && !isAnnotation && (
          <FormHelperText
            sx={{ paddingLeft: 1, marginTop: 0 }}
            error={!!isError}
          >
            {errorMessage || helperText}
          </FormHelperText>
        )}
        {(!!isError || helperText) && isAnnotation && (
          <FormHelperText
            sx={{ paddingLeft: 1, marginTop: 0 }}
            error={!!isError}
          >
            {errorMessage}
          </FormHelperText>
        )}
        {isRulePrompt && (
          <Typography fontSize={12} color="status.danger.fg" marginLeft="7px">
            {!rulePromptData
              ? "This field is required"
              : "Variables added should be used in the rule prompt"}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

RulePromptInput.propTypes = {
  setIsRulePrompt: PropTypes.func,
  rulePromptData: PropTypes.string,
  setRulePromptData: PropTypes.func,
  isRulePrompt: PropTypes.bool,
  control: PropTypes.object.isRequired,
  fieldConfig: PropTypes.object.isRequired,
  config: PropTypes.object.isRequired,
  configKey: PropTypes.string.isRequired,
  ruleStringKey: PropTypes.string,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  isEvaluatation: PropTypes.bool,
  allInputColumns: PropTypes.array,
  isAnnotation: PropTypes.bool,
};

export default RulePromptInput;
