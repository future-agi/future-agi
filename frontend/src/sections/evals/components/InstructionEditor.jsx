/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputBase,
  MenuItem,
  Popover,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import PromptEditor from "src/components/PromptCards/PromptEditor";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import ModelSelector from "./ModelSelector";

const TEMPLATE_FORMATS = [
  {
    value: "mustache",
    label: "Mustache",
    icon: "{{x}}",
    description: "{{variable}}, {{obj.key}}",
  },
  {
    value: "jinja",
    label: "Jinja",
    icon: "{% %}",
    description: "{{ variable }}, {% if %}",
  },
];

/**
 * Build dropdown options from dataset column config + JSON schemas.
 * Expands JSON columns to include nested dot-notation paths (e.g. assistant.id).
 * Format: [{id, value, dataType, isJsonPath}]
 */
function buildDropdownOptions(datasetColumns, jsonSchemas = {}) {
  if (!datasetColumns?.length) return [];
  const options = [];

  datasetColumns.forEach((col) => {
    if (!col?.name || ["id", "orgId"].includes(col.name)) return;

    const colId = col.id || col.name;

    // Base column
    options.push({
      id: colId,
      value: col.name,
      dataType: col.data_type || "text",
      isJsonPath: false,
    });

    // Expand columns with nested paths from schema (json + text with JSON values)
    if (jsonSchemas?.[colId]?.keys?.length) {
      const schema = jsonSchemas[colId];
      schema.keys.forEach((path) => {
        options.push({
          id: `${colId}.${path}`,
          value: `${col.name}.${path}`,
          dataType: "json_path",
          isJsonPath: true,
          parentColumn: col.name,
        });
      });
    }

    // Expand images columns with indexed access
    const imagesSchema = jsonSchemas?.[colId];
    if (col.data_type === "images" && imagesSchema?.max_images_count) {
      for (let idx = 0; idx < imagesSchema.max_images_count; idx++) {
        options.push({
          id: `${colId}[${idx}]`,
          value: `${col.name}[${idx}]`,
          dataType: "images_index",
          isJsonPath: true,
          parentColumn: col.name,
        });
      }
    }

    // Expand json/text columns with top-level array data
    const colSchema = jsonSchemas?.[colId];
    if (col.data_type !== "images" && colSchema?.max_array_count) {
      const count = Math.min(colSchema.max_array_count, 2);
      for (let idx = 0; idx < count; idx++) {
        options.push({
          id: `${colId}[${idx}]`,
          value: `${col.name}[${idx}]`,
          dataType: "array_index",
          isJsonPath: true,
          parentColumn: col.name,
        });
      }
    }
  });

  return options;
}

/**
 * Build a Set of valid variable names (lowercase) from dataset columns + JSON schemas.
 * Includes base column names and all expanded JSON paths.
 */
function buildValidVariableSet(datasetColumns, jsonSchemas = {}) {
  const validSet = new Set();
  if (!datasetColumns?.length) return validSet;

  datasetColumns.forEach((col) => {
    if (!col?.name || ["id", "orgId"].includes(col.name)) return;
    const colId = col.id || col.name;
    validSet.add(col.name.toLowerCase());

    // Add nested paths from schema (json + text with JSON values)
    if (jsonSchemas?.[colId]?.keys?.length) {
      jsonSchemas[colId].keys.forEach((path) => {
        validSet.add(`${col.name}.${path}`.toLowerCase());
      });
    }

    // Add images indexed access
    const imagesSchema = jsonSchemas?.[colId];
    if (col.data_type === "images" && imagesSchema?.max_images_count) {
      for (let idx = 0; idx < imagesSchema.max_images_count; idx++) {
        validSet.add(`${col.name}[${idx}]`.toLowerCase());
      }
    }

    // Add array indexed access for json/text columns with top-level arrays
    const colSchema = jsonSchemas?.[colId];
    if (col.data_type !== "images" && colSchema?.max_array_count) {
      const count = Math.min(colSchema.max_array_count, 2);
      for (let idx = 0; idx < count; idx++) {
        validSet.add(`${col.name}[${idx}]`.toLowerCase());
      }
    }
  });

  return validSet;
}

// Jinja template keywords for {% %} autocomplete
const JINJA_KEYWORDS = [
  {
    id: "if",
    value: "if ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% if condition %}",
  },
  {
    id: "elif",
    value: "elif ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% elif condition %}",
  },
  {
    id: "else",
    value: "else %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% else %}",
  },
  {
    id: "endif",
    value: "endif %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endif %}",
  },
  {
    id: "for",
    value: "for ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% for item in list %}",
  },
  {
    id: "endfor",
    value: "endfor %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endfor %}",
  },
  {
    id: "set",
    value: "set ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% set var = value %}",
  },
  {
    id: "macro",
    value: "macro ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% macro name() %}",
  },
  {
    id: "endmacro",
    value: "endmacro %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endmacro %}",
  },
  {
    id: "block",
    value: "block ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% block name %}",
  },
  {
    id: "endblock",
    value: "endblock %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endblock %}",
  },
  {
    id: "filter",
    value: "filter ",
    dataType: "jinja",
    isJinja: true,
    hint: "{% filter upper %}",
  },
  {
    id: "endfilter",
    value: "endfilter %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endfilter %}",
  },
  {
    id: "raw",
    value: "raw %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% raw %}",
  },
  {
    id: "endraw",
    value: "endraw %}",
    dataType: "jinja",
    isJinja: true,
    hint: "{% endraw %}",
  },
];

const InstructionEditor = ({
  value,
  onChange,
  model,
  onModelChange,
  placeholder = "Write your evaluation instructions here. Use {{variable}} to reference inputs...",
  disabled = false,
  // Separate flag: lets the prompt text be read-only while keeping the
  // ModelSelector (bottom bar: model picker, + menu, mode pill) editable.
  // Defaults to `disabled` for backward compat.
  modelSelectorDisabled,
  label = "Instructions",
  templateFormat = "mustache",
  onTemplateFormatChange,
  datasetColumns = [],
  datasetJsonSchemas = {},
  mappedVariables = {},
  // Pass-through ModelSelector lifted-state props — forwarded as-is so the
  // parent form can collect mode / internet / summary / connectors / KBs.
  mode,
  onModeChange,
  useInternet,
  onUseInternetChange,
  activeSummary,
  onActiveSummaryChange,
  activeConnectorIds,
  onActiveConnectorIdsChange,
  selectedKBs,
  onSelectedKBsChange,
  activeContextOptions,
  onActiveContextOptionsChange,
}) => {
  const modelBarDisabled = modelSelectorDisabled ?? disabled;
  const quillRef = useRef(null);
  const followUpRef = useRef(null);
  const [formatAnchor, setFormatAnchor] = useState(null);

  const hasDataset = datasetColumns.length > 0;

  // Extract existing variables from text as fallback suggestions
  const existingVarOptions = useMemo(() => {
    if (!value) return [];
    const matches = value.match(/\{\{\s*([^{}]+?)\s*\}\}/g) || [];
    const vars = [
      ...new Set(matches.map((m) => m.replace(/\{\{|\}\}/g, "").trim())),
    ];
    return vars.map((v) => ({
      id: v,
      value: v,
      dataType: "text",
      isJsonPath: v.includes("."),
    }));
  }, [value]);

  // Build dropdown options for quill-mention autocomplete
  // Includes dataset columns (with JSON paths), fallback existing vars, and Jinja keywords
  const dropdownOptions = useMemo(() => {
    const columnOpts = hasDataset
      ? buildDropdownOptions(datasetColumns, datasetJsonSchemas)
      : existingVarOptions;

    if (templateFormat === "jinja") {
      return [...columnOpts, ...JINJA_KEYWORDS];
    }
    return columnOpts;
  }, [
    hasDataset,
    datasetColumns,
    datasetJsonSchemas,
    existingVarOptions,
    templateFormat,
  ]);

  // Valid variable set for coloring
  const validVarSet = useMemo(
    () => buildValidVariableSet(datasetColumns, datasetJsonSchemas),
    [datasetColumns, datasetJsonSchemas],
  );

  // Column names that have nested paths (lowercase) — any path starting with these is valid
  const jsonColumnNames = useMemo(() => {
    const s = new Set();
    datasetColumns.forEach((col) => {
      const colId = col?.id || col?.name;
      if (col?.name && datasetJsonSchemas?.[colId]?.keys?.length) {
        s.add(col.name.toLowerCase());
      }
    });
    return s;
  }, [datasetColumns, datasetJsonSchemas]);

  // Jinja-aware input variable set: only top-level inputs (not loop/set vars)
  const jinjaInputVarSet = useMemo(() => {
    if (templateFormat !== "jinja" || !value) return null;
    const vars = extractJinjaVariables(value);
    return new Set(vars.map((v) => v.toLowerCase()));
  }, [templateFormat, value]);

  // Set of variable names that have been mapped to a column
  const mappedVarSet = useMemo(() => {
    const s = new Set();
    Object.entries(mappedVariables || {}).forEach(([k, v]) => {
      if (v) s.add(k.trim().toLowerCase());
    });
    return s;
  }, [mappedVariables]);

  // Variable validator: checks column match, mapped status, JSON path, or Jinja keyword
  const variableValidator = useCallback(
    (varName) => {
      const trimmed = varName.trim();

      // Jinja keywords are always valid
      if (JINJA_KEYWORDS.some((k) => trimmed.startsWith(k.id))) return true;

      // In Jinja mode, skip highlighting for loop-scoped/set variables (return null)
      if (templateFormat === "jinja" && jinjaInputVarSet) {
        const root = trimmed.split(/[.(\s|]/)[0].toLowerCase();
        if (!jinjaInputVarSet.has(root)) return null;
      }

      // Variables mapped in the mapping panel are valid (green)
      if (mappedVarSet.has(trimmed.toLowerCase())) return true;

      if (!hasDataset) return true; // No dataset → remaining vars are valid

      const lower = trimmed.toLowerCase();
      // Direct column match
      if (validVarSet.has(lower)) return true;
      // JSON path: check if base column is a JSON type column
      const dotIdx = lower.indexOf(".");
      if (dotIdx > 0) {
        const base = lower.substring(0, dotIdx);
        if (jsonColumnNames.has(base)) return true;
      }
      // Indexed access: col[0]
      const bracketMatch = lower.match(/^(.+?)\[\d+\]$/);
      if (bracketMatch && validVarSet.has(bracketMatch[1])) return true;
      return false;
    },
    [
      hasDataset,
      validVarSet,
      jsonColumnNames,
      templateFormat,
      jinjaInputVarSet,
      mappedVarSet,
    ],
  );

  // Jinja: use both {{ and {% as denotation chars
  const denotationChars = useMemo(
    () => (templateFormat === "jinja" ? ["{{", "{%"] : ["{{"]),
    [templateFormat],
  );

  // Auto-detect Jinja syntax: switch to jinja when {% is detected
  useEffect(() => {
    if (
      templateFormat === "mustache" &&
      onTemplateFormatChange &&
      value &&
      /\{%/.test(value)
    ) {
      onTemplateFormatChange("jinja");
    }
  }, [value, templateFormat, onTemplateFormatChange]);

  // Custom onSelect for Jinja: handles both {{ variable }} and {% keyword %}
  const handleMentionSelect = useCallback((item, quill, options) => {
    if (!quill) return;

    const cursorPosition = quill.getSelection(true).index;
    const textBefore = quill.getText(0, cursorPosition);

    // Check if triggered by {% or {{
    // eslint-disable-next-line no-useless-escape
    const matchBlock = textBefore.match(/\{%[\w.\s\[\]]*$/);
    // eslint-disable-next-line no-useless-escape
    const matchVar = textBefore.match(/\{\{[\w.\s\[\]]*$/);

    if (matchBlock && item.isJinja) {
      // Jinja block: {% keyword %}
      const startIndex = cursorPosition - matchBlock[0].length;
      const textAfter = quill.getText(cursorPosition, 2);
      const hasClosing = textAfter === "%}";
      const deleteLength = matchBlock[0].length + (hasClosing ? 2 : 0);

      quill.deleteText(startIndex, deleteLength);

      // Some keywords already include closing %} (else, endif, endfor etc.)
      const insertText = item.value.endsWith("%}")
        ? `{% ${item.value}`
        : `{% ${item.value}%}`;

      quill.insertText(
        startIndex,
        insertText,
        { color: "var(--mention-valid-color)", bold: true },
        "user",
      );

      // Place cursor before closing %} for keywords that need arguments
      const closingPos = insertText.indexOf("%}");
      if (closingPos > 0 && !item.value.endsWith("%}")) {
        quill.setSelection(startIndex + closingPos);
      } else {
        quill.setSelection(startIndex + insertText.length);
      }
    } else if (matchVar) {
      // Variable: {{ variable }}
      const startIndex = cursorPosition - matchVar[0].length;
      const textAfter = quill.getText(cursorPosition, 2);
      const hasClosing = textAfter === "}}";
      const deleteLength = matchVar[0].length + (hasClosing ? 2 : 0);

      quill.deleteText(startIndex, deleteLength);

      const isValid = options.some(
        (v) => !v.isJinja && v.value.toLowerCase() === item.value.toLowerCase(),
      );

      // Jinja style: {{ variable }} with spaces
      const insertText = `{{ ${item.value} }}`;
      quill.insertText(
        startIndex,
        insertText,
        {
          color: isValid
            ? "var(--mention-valid-color)"
            : "var(--mention-invalid-color)",
        },
        "user",
      );
      quill.setSelection(startIndex + insertText.length);
    }
  }, []);

  // Convert string value → blocks for PromptEditor
  const prompt = useMemo(
    () => (value ? [{ type: "text", text: value }] : []),
    [value],
  );

  // Convert blocks → string for parent onChange
  const handlePromptChange = useCallback(
    (blocks) => {
      const text = blocks
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("");
      onChange(text);
    },
    [onChange],
  );

  // AI state
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalValue, setOriginalValue] = useState(null);

  const callAI = useCallback(
    async (instruction) => {
      const isImprovement = value && value.trim().length > 20;
      const description = isImprovement
        ? `Existing evaluation prompt:\n\n${value}\n\nUser wants to: ${instruction}\n\nGenerate an improved version. Keep {{variable}} syntax. Return ONLY the prompt.`
        : instruction;

      const { data } = await axios.post(endpoints.develop.eval.aiEvalWriter, {
        description,
      });
      return data?.result?.prompt || null;
    },
    [value],
  );

  const setQuillText = useCallback(
    (text) => {
      onChange(text);
      const quill = quillRef.current;
      if (quill) {
        quill.setContents([{ insert: text }], "api");
      }
    },
    [onChange],
  );

  const handleSubmit = useCallback(
    async (instruction) => {
      if (!instruction?.trim()) return;
      setAiLoading(true);

      if (originalValue === null) {
        setOriginalValue(value || "");
      }

      try {
        const result = await callAI(instruction.trim());
        if (result) {
          setQuillText(result);
          setHasResult(true);
          setAiPrompt(instruction.trim());
          setTimeout(() => followUpRef.current?.focus(), 100);
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("AI failed:", err?.message);
      } finally {
        setAiLoading(false);
      }
    },
    [value, originalValue, callAI, setQuillText],
  );

  const handleAccept = useCallback(() => {
    setAiOpen(false);
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  }, []);

  const handleReject = useCallback(() => {
    if (originalValue !== null) {
      setQuillText(originalValue);
    }
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  }, [originalValue, setQuillText]);

  const handleClose = useCallback(() => {
    if (hasResult && originalValue !== null) {
      setQuillText(originalValue);
    }
    setAiOpen(false);
    setHasResult(false);
    setOriginalValue(null);
    setAiPrompt("");
  }, [hasResult, originalValue, setQuillText]);

  const mentionEnabled = true;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 0.5,
        }}
      >
        <Typography variant="body2" fontWeight={600}>
          {label}
          <span style={{ color: "#d32f2f" }}>*</span>
        </Typography>

        {/* Template format selector */}
        {onTemplateFormatChange && (
          <>
            <Box
              onClick={(e) => !disabled && setFormatAnchor(e.currentTarget)}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                px: 1.25,
                py: 0.35,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                cursor: disabled ? "default" : "pointer",
                "&:hover": disabled ? {} : { borderColor: "text.secondary" },
              }}
            >
              <Typography
                sx={{
                  fontSize: "12px",
                  fontWeight: 600,
                  fontFamily: "monospace",
                  color: "text.secondary",
                }}
              >
                {TEMPLATE_FORMATS.find((f) => f.value === templateFormat)
                  ?.icon || "{{x}}"}
              </Typography>
              <Typography variant="caption" sx={{ fontSize: "12px" }}>
                {TEMPLATE_FORMATS.find((f) => f.value === templateFormat)
                  ?.label || "Mustache"}
              </Typography>
              <Iconify
                icon={formatAnchor ? "mdi:chevron-up" : "mdi:chevron-down"}
                width={14}
                sx={{ color: "text.disabled" }}
              />
            </Box>
            <Popover
              open={Boolean(formatAnchor)}
              anchorEl={formatAnchor}
              onClose={() => setFormatAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{
                paper: {
                  sx: { borderRadius: "8px", p: 0.5, minWidth: 220 },
                },
              }}
            >
              {TEMPLATE_FORMATS.map((fmt) => (
                <MenuItem
                  key={fmt.value}
                  selected={templateFormat === fmt.value}
                  onClick={() => {
                    onTemplateFormatChange(fmt.value);
                    setFormatAnchor(null);
                  }}
                  sx={{ borderRadius: "6px", py: 1, gap: 1.5 }}
                >
                  <Typography
                    sx={{
                      fontSize: "14px",
                      fontWeight: 700,
                      fontFamily: "monospace",
                      width: 40,
                      textAlign: "center",
                      color:
                        templateFormat === fmt.value
                          ? "primary.main"
                          : "text.secondary",
                    }}
                  >
                    {fmt.icon}
                  </Typography>
                  <Box>
                    <Typography
                      variant="body2"
                      sx={{ fontSize: "13px", fontWeight: 600 }}
                    >
                      {fmt.label}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ fontSize: "11px" }}
                    >
                      {fmt.description}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
            </Popover>
          </>
        )}
      </Box>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          "&:focus-within": {
            borderColor: disabled ? undefined : "primary.main",
          },
          ...(disabled && {
            cursor: "not-allowed",
            "& .ql-editor, & .ql-container, & .ql-toolbar": {
              cursor: "not-allowed !important",
            },
          }),
        }}
      >
        {/* ── AI bar ── */}
        {aiOpen && (
          <Box
            sx={{
              borderBottom: "1px solid",
              borderColor: "divider",
              backgroundColor: (theme) =>
                theme.palette.mode === "dark" ? "#1a1a2e" : "#fafafe",
              borderRadius: "8px 8px 0 0",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                px: 1.5,
                pt: 1,
              }}
            >
              {aiLoading ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    flex: 1,
                  }}
                >
                  <CircularProgress size={14} />
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontSize: "13px" }}
                  >
                    Generating...
                  </Typography>
                </Box>
              ) : !hasResult ? (
                <InputBase
                  autoFocus
                  fullWidth
                  placeholder={
                    value?.trim()
                      ? "Describe changes — e.g. 'make scoring stricter'"
                      : "Describe your eval — e.g. 'evaluate SEO blog quality'"
                  }
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSubmit(aiPrompt);
                    }
                    if (e.key === "Escape") handleClose();
                  }}
                  sx={{ fontSize: "13px", flex: 1 }}
                />
              ) : (
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontSize: "13px",
                    color: "text.secondary",
                    fontStyle: "italic",
                  }}
                >
                  {aiPrompt}
                </Typography>
              )}

              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  ml: 1,
                  flexShrink: 0,
                }}
              >
                {hasResult && (
                  <>
                    <Button
                      size="small"
                      onClick={handleReject}
                      sx={{
                        textTransform: "none",
                        fontSize: "12px",
                        color: "text.secondary",
                        minWidth: 0,
                        px: 1,
                      }}
                    >
                      Reject
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={handleAccept}
                      sx={{
                        textTransform: "none",
                        fontSize: "12px",
                        minWidth: 0,
                        px: 1.5,
                        fontWeight: 600,
                      }}
                    >
                      Accept
                    </Button>
                  </>
                )}
                {!hasResult && !aiLoading && (
                  <IconButton
                    size="small"
                    onClick={() => handleSubmit(aiPrompt)}
                    disabled={!aiPrompt.trim()}
                    sx={{ p: 0.5 }}
                  >
                    <Iconify
                      icon="mdi:arrow-up-circle"
                      width={20}
                      sx={{
                        color: aiPrompt.trim()
                          ? "primary.main"
                          : "text.disabled",
                      }}
                    />
                  </IconButton>
                )}
                <IconButton size="small" onClick={handleClose} sx={{ p: 0.25 }}>
                  <Iconify
                    icon="mdi:close"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </IconButton>
              </Box>
            </Box>

            {hasResult && (
              <Box sx={{ px: 1.5, pb: 1, pt: 0.5 }}>
                <InputBase
                  inputRef={followUpRef}
                  fullWidth
                  placeholder="Add a follow-up..."
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      e.target.value.trim()
                    ) {
                      e.preventDefault();
                      handleSubmit(e.target.value);
                      e.target.value = "";
                    }
                    if (e.key === "Escape") handleClose();
                  }}
                  sx={{
                    fontSize: "13px",
                    borderTop: "1px solid",
                    borderColor: "divider",
                    pt: 0.75,
                  }}
                />
              </Box>
            )}
          </Box>
        )}

        {/* ── Quill Editor (same as prompt workbench) ── */}
        {/* Key on templateFormat so Quill re-inits with correct denotation chars */}
        <PromptEditor
          key={`${templateFormat}-${mentionEnabled}`}
          ref={quillRef}
          placeholder={placeholder}
          prompt={prompt}
          onPromptChange={handlePromptChange}
          dropdownOptions={dropdownOptions}
          mentionEnabled={mentionEnabled}
          mentionDenotationChars={denotationChars}
          onMentionSelect={
            templateFormat === "jinja" ? handleMentionSelect : undefined
          }
          showEditEmbed={false}
          allowVariables={mentionEnabled}
          allVariablesValid={!hasDataset && templateFormat !== "jinja"}
          variableValidator={
            hasDataset || templateFormat === "jinja"
              ? variableValidator
              : undefined
          }
          jinjaMode={templateFormat === "jinja"}
          disabled={disabled}
          expandable
          sx={{
            border: "none",
            borderRadius: 0,
            minHeight: 160,
            padding: "12px 16px",
            "& .ql-container": { border: "none !important" },
            "& .ql-editor": {
              border: "none !important",
              outline: "none !important",
              padding: 0,
              minHeight: 140,
              fontSize: "14px",
              lineHeight: 1.6,
              ...(disabled && { cursor: "not-allowed !important" }),
            },
            "& .ql-editor:focus": {
              outline: "none !important",
              boxShadow: "none !important",
            },
            "& .ql-editor.ql-blank::before": {
              fontStyle: "normal",
              color: "var(--text-disabled)",
              left: 0,
            },
            ...(disabled && {
              cursor: "not-allowed",
              "& *": { cursor: "not-allowed !important" },
            }),
          }}
        />

        {/* ── Model bar + Falcon button ── */}
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
          }}
        >
          <Box sx={{ flex: 1 }}>
            <ModelSelector
              model={model}
              onModelChange={onModelChange}
              disabled={modelBarDisabled}
              mode={mode}
              onModeChange={onModeChange}
              useInternet={useInternet}
              onUseInternetChange={onUseInternetChange}
              activeSummary={activeSummary}
              onActiveSummaryChange={onActiveSummaryChange}
              activeConnectorIds={activeConnectorIds}
              onActiveConnectorIdsChange={onActiveConnectorIdsChange}
              selectedKBs={selectedKBs}
              onSelectedKBsChange={onSelectedKBsChange}
              activeContextOptions={activeContextOptions}
              onActiveContextOptionsChange={onActiveContextOptionsChange}
            />
          </Box>
          {!aiOpen && (
            <Tooltip title="Write with Falcon AI" arrow placement="top">
              <IconButton
                size="small"
                onClick={() => setAiOpen(true)}
                disabled={disabled}
                sx={{
                  width: 32,
                  height: 32,
                  "&:hover": {
                    backgroundColor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(124,77,255,0.12)"
                        : "rgba(124,77,255,0.06)",
                  },
                }}
              >
                <SvgColor
                  src="/assets/icons/navbar/ic_falcon_ai.svg"
                  sx={{ width: 20, height: 20, color: "primary.main" }}
                />
              </IconButton>
            </Tooltip>
          )}
        </Box>
      </Box>
    </Box>
  );
};

InstructionEditor.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  model: PropTypes.string.isRequired,
  onModelChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  disabled: PropTypes.bool,
  label: PropTypes.string,
  templateFormat: PropTypes.oneOf(["mustache", "jinja"]),
  onTemplateFormatChange: PropTypes.func,
  datasetColumns: PropTypes.array,
  datasetJsonSchemas: PropTypes.object,
};

export default InstructionEditor;
