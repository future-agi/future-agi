/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputBase,
  MenuItem,
  Popover,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import axios, { endpoints } from "src/utils/axios";
import MessageEditor from "./MessageEditor";

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

/**
 * LLM-As-A-Judge prompt editor with Falcon AI.
 *
 * Wraps MessageEditor with an AI bar that generates or improves the full
 * message chain (system + user + assistant). The header (label + template
 * format) and the single bordered editor box mirror the agent
 * InstructionEditor so both eval editors look visually identical.
 */
const LLMPromptEditor = ({
  messages,
  onMessagesChange,
  templateFormat,
  onTemplateFormatChange,
  datasetColumns = [],
  datasetJsonSchemas = {},
  disabled = false,
  modelSelectorDisabled,
  model,
  onModelChange,
}) => {
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalMessages, setOriginalMessages] = useState(null);
  const [formatAnchor, setFormatAnchor] = useState(null);
  const followUpRef = useRef(null);

  const callAI = useCallback(
    async (instruction) => {
      const hasExisting = messages.some((m) => m.content.trim().length > 10);
      const templateHint =
        templateFormat === "jinja"
          ? "Use Jinja2 {{ variable }} syntax for variables."
          : "Use Mustache {{variable}} syntax for variables.";

      const description = hasExisting
        ? `${templateHint}\n\nExisting messages (current draft):\n${messages
            .map((m) => `[${m.role}]: ${m.content}`)
            .join("\n")}\n\nUser wants to: ${instruction}`
        : `${templateHint}\n\n${instruction}`;

      try {
        const { data } = await axios.post(endpoints.develop.eval.aiEvalWriter, {
          description,
          output_format: "messages",
        });
        // Backend parses + validates the messages array for us now.
        const msgs = data?.result?.messages;
        if (!Array.isArray(msgs) || msgs.length === 0) return null;

        // Drop assistant messages — those come from the actual eval at run
        // time, not the template.
        return msgs
          .filter((m) => m.role !== "assistant")
          .map((m) => ({
            role: m.role || "system",
            content: m.content || "",
          }));
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("LLM-as-a-Judge AI: request failed", err?.message);
        return null;
      }
    },
    [messages, templateFormat],
  );

  const handleSubmit = useCallback(
    async (instruction) => {
      if (!instruction?.trim()) return;
      setAiLoading(true);

      if (originalMessages === null) {
        setOriginalMessages([...messages]);
      }

      const result = await callAI(instruction.trim());
      if (result) {
        onMessagesChange(result);
        setHasResult(true);
        setAiPrompt(instruction.trim());
        setTimeout(() => followUpRef.current?.focus(), 100);
      }

      setAiLoading(false);
    },
    [messages, originalMessages, callAI, onMessagesChange],
  );

  const handleAccept = useCallback(() => {
    setAiOpen(false);
    setHasResult(false);
    setOriginalMessages(null);
    setAiPrompt("");
  }, []);

  const handleReject = useCallback(() => {
    if (originalMessages) onMessagesChange(originalMessages);
    setHasResult(false);
    setOriginalMessages(null);
    setAiPrompt("");
  }, [originalMessages, onMessagesChange]);

  const handleClose = useCallback(() => {
    if (hasResult && originalMessages) onMessagesChange(originalMessages);
    setAiOpen(false);
    setHasResult(false);
    setOriginalMessages(null);
    setAiPrompt("");
  }, [hasResult, originalMessages, onMessagesChange]);

  const currentFormat =
    TEMPLATE_FORMATS.find((f) => f.value === templateFormat) ||
    TEMPLATE_FORMATS[0];

  // Falcon AI bar — rendered at the top of MessageEditor's bordered box
  // (when open), matching the agent InstructionEditor's inline AI bar.
  const aiBar = (
    <Box
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
        borderRadius: "8px 8px 0 0",
        backgroundColor: "background.aiSurface",
      }}
    >
      {/* Row 1: Prompt + Reject/Accept */}
      <Box sx={{ display: "flex", alignItems: "center", px: 1.5, py: 1 }}>
        {aiLoading ? (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, flex: 1 }}>
            <CircularProgress size={14} />
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ fontSize: "13px" }}
            >
              Generating messages...
            </Typography>
          </Box>
        ) : !hasResult ? (
          <>
            <SvgColor
              src="/assets/icons/navbar/ic_falcon_ai.svg"
              sx={{
                width: 16,
                height: 16,
                color: "primary.main",
                mr: 1,
                flexShrink: 0,
              }}
            />
            <InputBase
              autoFocus
              fullWidth
              placeholder="Describe your eval — e.g. 'judge if chatbot responses are helpful and accurate'"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit(aiPrompt);
                }
                if (e.key === "Escape") handleClose();
              }}
              sx={{ fontSize: "13px" }}
            />
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
                  color: aiPrompt.trim() ? "primary.main" : "text.disabled",
                }}
              />
            </IconButton>
          </>
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
          <IconButton size="small" onClick={handleClose} sx={{ p: 0.25 }}>
            <Iconify
              icon="mdi:close"
              width={16}
              sx={{ color: "text.disabled" }}
            />
          </IconButton>
        </Box>
      </Box>

      {/* Row 2: Follow-up */}
      {hasResult && (
        <Box sx={{ px: 1.5, pb: 1, pt: 0.5 }}>
          <InputBase
            inputRef={followUpRef}
            fullWidth
            placeholder="Add a follow-up — e.g. 'add a user message with variable mapping'"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && e.target.value.trim()) {
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
  );

  return (
    <Box>
      {/* ── Header row: label (left) + template format (right) — matches
          the agent InstructionEditor header. ── */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 0.5,
        }}
      >
        <Typography typography="s1" fontWeight={600}>
          Prompt Messages
          <Box component="span" sx={{ color: "error.main" }}>
            *
          </Box>
        </Typography>

        <ShowComponent condition={Boolean(onTemplateFormatChange)}>
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
              typography="s2"
              sx={{
                fontWeight: 600,
                fontFamily: "monospace",
                color: "text.secondary",
              }}
            >
              {currentFormat.icon}
            </Typography>
            <Typography typography="s2">{currentFormat.label}</Typography>
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
              paper: { sx: { borderRadius: "8px", p: 0.5, minWidth: 220 } },
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
        </ShowComponent>
      </Box>

      <MessageEditor
        messages={messages}
        onChange={onMessagesChange}
        templateFormat={templateFormat}
        model={model}
        onModelChange={onModelChange}
        datasetColumns={datasetColumns}
        datasetJsonSchemas={datasetJsonSchemas}
        disabled={disabled}
        modelSelectorDisabled={modelSelectorDisabled}
        aiBar={aiOpen ? aiBar : null}
        onFalconClick={() => setAiOpen(true)}
        aiOpen={aiOpen}
      />
    </Box>
  );
};

LLMPromptEditor.propTypes = {
  messages: PropTypes.array.isRequired,
  onMessagesChange: PropTypes.func.isRequired,
  templateFormat: PropTypes.string,
  onTemplateFormatChange: PropTypes.func,
  model: PropTypes.string,
  onModelChange: PropTypes.func,
  disabled: PropTypes.bool,
  modelSelectorDisabled: PropTypes.bool,
};

export default LLMPromptEditor;
