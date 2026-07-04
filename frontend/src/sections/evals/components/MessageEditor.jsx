import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Tooltip,
} from "@mui/material";
import PropTypes from "prop-types";
import { useCallback, useMemo } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import MessageEditorBlock from "./MessageEditorBlock";
import ModelSelector from "./ModelSelector";

const ROLES = [
  { value: "system", label: "System" },
  { value: "user", label: "User" },
  { value: "assistant", label: "Assistant" },
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
 *
 * Renders a single bordered box (Falcon AI bar at the top when open, the
 * message cards joined by dividers, and a bottom bar with the model
 * selector + Add message + Falcon AI trigger) so it looks visually
 * identical to the agent InstructionEditor. The template-format picker
 * and the "Prompt Messages" label live in the parent LLMPromptEditor's
 * header row, mirroring the agent layout.
 *
 * Uses the same PromptEditor (Quill) as the agent InstructionEditor for
 * consistent variable autocomplete and styling.
 */
const MessageEditor = ({
  messages = [{ role: "system", content: "" }],
  onChange,
  templateFormat = "mustache",
  datasetColumns = [],
  datasetJsonSchemas = {},
  disabled = false,
  modelSelectorDisabled,
  model,
  onModelChange,
  // Falcon AI: the parent renders the AI bar node (shown at the top of
  // the box when open) and passes the trigger handler for the bottom-bar
  // icon. aiOpen hides the trigger while the bar is open, matching agent.
  aiBar,
  onFalconClick,
  aiOpen = false,
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
    <>
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
        {/* ── Falcon AI bar (rendered at the top of the box when open) ── */}
        {aiBar}

        {/* ── Message cards — joined inside the single bordered box and
          separated by dividers, mirroring the agent InstructionEditor's
          single editor box. ── */}
        <Box sx={{ display: "flex", flexDirection: "column" }}>
          {messages.map((msg, i) => (
            <Box
              key={i}
              sx={{
                borderBottom: i < messages.length - 1 ? "1px solid" : "none",
                borderColor: "divider",
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
                    "& .MuiSelect-select": { py: 0, pr: "28px !important" },
                    "& .MuiSelect-icon": { right: 0 },
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

        {/* ── Bottom bar inside the box: model selector (left) + Add message
          + Falcon AI trigger (right), separated by a top divider —
          identical in spirit to the agent InstructionEditor's model bar. ── */}
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            gap: 1,
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

          <ShowComponent condition={onFalconClick && !aiOpen}>
            <Tooltip title="Generate with Falcon AI" arrow placement="top">
              <IconButton
                size="small"
                onClick={onFalconClick}
                disabled={disabled}
                sx={{
                  width: 32,
                  height: 32,
                  flexShrink: 0,
                  "&:hover": { backgroundColor: "background.aiHover" },
                }}
              >
                <SvgColor
                  src="/assets/icons/navbar/ic_falcon_ai.svg"
                  sx={{ width: 20, height: 20, color: "primary.main" }}
                />
              </IconButton>
            </Tooltip>
          </ShowComponent>
        </Box>
      </Box>

      {/* ── Add message — sits outside the box, bottom-left. ── */}
      <Box sx={{ mt: 1 }}>
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
    </>
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
  model: PropTypes.string,
  onModelChange: PropTypes.func,
  datasetColumns: PropTypes.array,
  datasetJsonSchemas: PropTypes.object,
  disabled: PropTypes.bool,
  modelSelectorDisabled: PropTypes.bool,
  aiBar: PropTypes.node,
  onFalconClick: PropTypes.func,
  aiOpen: PropTypes.bool,
};

export default MessageEditor;
