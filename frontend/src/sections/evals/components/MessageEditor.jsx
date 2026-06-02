import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useCallback, useMemo } from "react";
import Iconify from "src/components/iconify";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import MessageEditorBlock from "./MessageEditorBlock";
import ModelSelector from "./ModelSelector";

const ROLES = [
  { value: "system", label: "System" },
  { value: "user", label: "User" },
  { value: "assistant", label: "Assistant" },
];

const TEMPLATE_FORMATS = [
  {
    value: "mustache",
    label: "Mustache",
    icon: "{{x}}",
    description: "{{variable}}",
  },
  {
    value: "jinja",
    label: "Jinja",
    icon: "{% %}",
    description: "{{ variable }}, {% if %}",
  },
];

const JINJA_KEYWORDS = [
  { id: "if", value: "if %}", display: "{% if %}" },
  { id: "endif", value: "endif %}", display: "{% endif %}" },
  { id: "for", value: "for item in list %}", display: "{% for %}" },
  { id: "endfor", value: "endfor %}", display: "{% endfor %}" },
  { id: "else", value: "else %}", display: "{% else %}" },
  { id: "elif", value: "elif %}", display: "{% elif %}" },
  { id: "set", value: "set var = value %}", display: "{% set %}" },
];

/**
 * Multi-message prompt editor for LLM-As-A-Judge.
 * Uses the same PromptEditor (Quill) as the agent InstructionEditor
 * for consistent variable autocomplete and styling.
 */
const MessageEditor = ({
  messages = [{ role: "system", content: "" }],
  onChange,
  templateFormat = "mustache",
  onTemplateFormatChange,
  datasetColumns = [],
  datasetJsonSchemas = {},
  disabled = false,
  modelSelectorDisabled,
  // Optional model selector — when provided, renders inline in the top
  // bar alongside the template format picker so LLM-as-a-judge has the
  // same top-bar layout as the agent InstructionEditor.
  model,
  onModelChange,
}) => {
  // Build dropdown options for variable autocomplete (same logic as InstructionEditor)
  const dropdownOptions = useMemo(() => {
    if (!templateFormat) return [];
    const options = [];

    datasetColumns.forEach((col) => {
      const name =
        typeof col === "string" ? col : col.name || col.label || String(col);
      options.push({ id: name, value: name, display: name });

      // JSON dot-notation paths
      const schema = datasetJsonSchemas?.[name];
      if (schema?.properties) {
        const addPaths = (obj, prefix) => {
          Object.entries(obj).forEach(([key, val]) => {
            const path = `${prefix}.${key}`;
            options.push({ id: path, value: path, display: path });
            if (val?.properties) addPaths(val.properties, path);
          });
        };
        addPaths(schema.properties, name);
      }
    });

    if (templateFormat === "jinja") {
      options.push(...JINJA_KEYWORDS);
    }

    return options;
  }, [datasetColumns, datasetJsonSchemas, templateFormat]);

  const mentionEnabled = true;
  const denotationChars = templateFormat === "jinja" ? ["{{", "{%"] : ["{{"];

  // Jinja-aware input variable set for highlighting — extract from
  // each message separately and union the results, since each message
  // is rendered independently by Jinja (a {% for %} in one message
  // doesn't scope into another).
  const jinjaInputVarSet = useMemo(() => {
    if (templateFormat !== "jinja") return null;
    const allVars = new Set();
    messages.forEach((m) => {
      if (m.content?.trim()) {
        extractJinjaVariables(m.content).forEach((v) =>
          allVars.add(v.toLowerCase()),
        );
      }
    });
    return allVars.size > 0 ? allVars : null;
  }, [templateFormat, messages]);

  // Variable validator: returns null for loop-scoped (no highlight), true for input vars
  const variableValidator = useCallback(
    (varName) => {
      const trimmed = varName.trim();
      if (JINJA_KEYWORDS.some((k) => trimmed.startsWith(k.id))) return true;
      if (templateFormat === "jinja" && jinjaInputVarSet) {
        const root = trimmed.split(/[.(\s|]/)[0].toLowerCase();
        if (!jinjaInputVarSet.has(root)) return null;
      }
      return true;
    },
    [templateFormat, jinjaInputVarSet],
  );

  const handleMentionSelect = useCallback(
    (item, insertItem, denotationChar) => {
      if (denotationChar === "{%" && item) {
        insertItem({ ...item, value: item.value || item.id });
        return;
      }
      insertItem(item);
    },
    [],
  );

  const handleAddMessage = useCallback(() => {
    const lastRole = messages[messages.length - 1]?.role || "system";
    const nextRole =
      lastRole === "system"
        ? "user"
        : lastRole === "user"
          ? "assistant"
          : "user";
    onChange([...messages, { role: nextRole, content: "" }]);
  }, [messages, onChange]);

  const handleUpdateContent = useCallback(
    (index, content) => {
      onChange(messages.map((m, i) => (i === index ? { ...m, content } : m)));
    },
    [messages, onChange],
  );

  const handleUpdateRole = useCallback(
    (index, role) => {
      onChange(messages.map((m, i) => (i === index ? { ...m, role } : m)));
    },
    [messages, onChange],
  );

  const handleRemoveMessage = useCallback(
    (index) => {
      if (messages.length <= 1) return;
      onChange(messages.filter((_, i) => i !== index));
    },
    [messages, onChange],
  );

  const placeholder =
    templateFormat === "mustache"
      ? "Evaluate {{output}} against {{expected}}..."
      : templateFormat === "jinja"
        ? "Evaluate {{ output }} against {{ expected }}..."
        : "Enter message content...";

  return (
    <Box>
      {/* Top bar — model selector on the left, template format selector
          on the right. Matches the top bar of InstructionEditor (agent
          flow) so LLM-as-a-judge and agent editors look consistent. */}
      {(onModelChange || onTemplateFormatChange) && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 1,
            mb: 0.75,
          }}
        >
          {onModelChange ? (
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <ModelSelector
                model={model}
                onModelChange={onModelChange}
                showMode={false}
                showPlus={false}
                disabled={modelSelectorDisabled ?? disabled}
              />
            </Box>
          ) : (
            <Box sx={{ flex: 1 }} />
          )}
          {onTemplateFormatChange && (
            <Select
              size="small"
              value={templateFormat}
              onChange={(e) => onTemplateFormatChange?.(e.target.value)}
              disabled={disabled}
              variant="outlined"
              sx={{
                fontSize: "13px",
                height: 30,
                borderColor: "divider",
                "& .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                },
              }}
              renderValue={(val) => {
                const fmt = TEMPLATE_FORMATS.find((f) => f.value === val);
                return (
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <Typography
                      sx={{
                        fontSize: "12px",
                        fontFamily: "monospace",
                        fontWeight: 600,
                      }}
                    >
                      {fmt?.icon}
                    </Typography>
                    <Typography sx={{ fontSize: "12px" }}>
                      {fmt?.label}
                    </Typography>
                  </Box>
                );
              }}
            >
              {TEMPLATE_FORMATS.map((fmt) => (
                <MenuItem
                  key={fmt.value}
                  value={fmt.value}
                  sx={{ fontSize: "13px", gap: 1 }}
                >
                  <Typography
                    sx={{
                      fontSize: "12px",
                      fontFamily: "monospace",
                      fontWeight: 600,
                      minWidth: 32,
                    }}
                  >
                    {fmt.icon}
                  </Typography>
                  <Box>
                    <Typography variant="body2" sx={{ fontSize: "13px" }}>
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
            </Select>
          )}
        </Box>
      )}

      {/* Message cards */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {messages.map((msg, i) => (
          <Box
            key={i}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius:
                i === 0
                  ? "8px 8px 0 0"
                  : i === messages.length - 1
                    ? "0 0 8px 8px"
                    : 0,
              borderTop: i > 0 ? "none" : undefined,
              "&:focus-within": {
                borderColor: "primary.main",
                zIndex: 1,
                position: "relative",
              },
            }}
          >
            {/* Role header */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                px: 1.5,
                pt: 1,
                pb: 0.5,
              }}
            >
              <Select
                size="small"
                value={msg.role}
                onChange={(e) => handleUpdateRole(i, e.target.value)}
                disabled={disabled}
                variant="standard"
                disableUnderline
                sx={{
                  fontSize: "13px",
                  fontWeight: 600,
                  "& .MuiSelect-select": { py: 0, pr: 2 },
                }}
              >
                {ROLES.map((r) => (
                  <MenuItem
                    key={r.value}
                    value={r.value}
                    sx={{ fontSize: "13px" }}
                  >
                    {r.label}
                  </MenuItem>
                ))}
              </Select>

              <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
                {messages.length > 1 && (
                  <IconButton
                    size="small"
                    onClick={() => handleRemoveMessage(i)}
                    disabled={disabled}
                    sx={{ p: 0.25, opacity: 0.5, "&:hover": { opacity: 1 } }}
                  >
                    <Iconify icon="mdi:close" width={14} />
                  </IconButton>
                )}
              </Box>
            </Box>

            {/* Content — same PromptEditor as agent InstructionEditor */}
            <MessageEditorBlock
              content={msg.content}
              onContentChange={(text) => handleUpdateContent(i, text)}
              placeholder={i === 0 ? placeholder : "Enter message content..."}
              minHeight={i === 0 ? 80 : 50}
              dropdownOptions={dropdownOptions}
              mentionEnabled={mentionEnabled}
              mentionDenotationChars={denotationChars}
              onMentionSelect={
                templateFormat === "jinja" ? handleMentionSelect : undefined
              }
              disabled={disabled}
              templateFormat={templateFormat}
              allVariablesValid={templateFormat !== "jinja"}
              variableValidator={
                templateFormat === "jinja" ? variableValidator : undefined
              }
              jinjaMode={templateFormat === "jinja"}
            />
          </Box>
        ))}
      </Box>

      {/* Bottom bar: + Message. Template format moved to the top bar
          above for parity with the agent InstructionEditor. */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 1 }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:plus" width={14} />}
          onClick={handleAddMessage}
          disabled={disabled}
          sx={{
            textTransform: "none",
            fontSize: "13px",
            borderColor: "divider",
            color: "text.secondary",
            "&:hover": { borderColor: "text.secondary" },
          }}
        >
          Message
        </Button>
      </Box>
    </Box>
  );
};

MessageEditor.propTypes = {
  messages: PropTypes.arrayOf(
    PropTypes.shape({
      role: PropTypes.oneOf(["system", "user", "assistant"]),
      content: PropTypes.string,
    }),
  ),
  onChange: PropTypes.func.isRequired,
  templateFormat: PropTypes.oneOf(["mustache", "jinja"]),
  onTemplateFormatChange: PropTypes.func,
  model: PropTypes.string,
  onModelChange: PropTypes.func,
  datasetColumns: PropTypes.array,
  datasetJsonSchemas: PropTypes.object,
  disabled: PropTypes.bool,
  modelSelectorDisabled: PropTypes.bool,
};

export default MessageEditor;
