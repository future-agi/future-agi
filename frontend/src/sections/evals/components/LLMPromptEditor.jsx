/* eslint-disable react/prop-types */
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputBase,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import MessageEditor from "./MessageEditor";

/**
 * LLM-As-A-Judge prompt editor with Falcon AI.
 *
 * Wraps MessageEditor with an AI bar that generates
 * or improves the full message chain (system + user + assistant).
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
  // Optional model selector — forwarded to MessageEditor's top bar so
  // the model picker and template format picker share the same row.
  model,
  onModelChange,
}) => {
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalMessages, setOriginalMessages] = useState(null);
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
        const prompt = data?.result?.prompt;
        if (!prompt) return null;

        // Backend returns a JSON string for messages format — parse it.
        try {
          let parsed = prompt;
          if (typeof parsed === "string") {
            let text = parsed.trim();
            if (text.startsWith("```")) {
              text = text.split("\n").slice(1).join("\n");
              if (text.endsWith("```")) text = text.slice(0, -3);
              text = text.trim();
            }
            parsed = JSON.parse(text);
          }
          if (Array.isArray(parsed) && parsed.length > 0 && parsed[0].role) {
            // Filter out assistant messages — those come from the actual eval, not the template
            return parsed
              .filter((m) => m.role !== "assistant")
              .map((m) => ({
                role: m.role || "system",
                content: m.content || "",
              }));
          }
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn("LLM-as-a-Judge AI: failed to parse JSON", err?.message);
        }

        // Fallback: keep the raw text as a system message so the user sees SOMETHING
        return [{ role: "system", content: prompt }];
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

  return (
    <Box>
      {/* ── Falcon AI bar ── */}
      {aiOpen && (
        <Box
          sx={{
            mb: 1,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark" ? "#1a1a2e" : "#fafafe",
          }}
        >
          {/* Row 1: Prompt + Reject/Accept */}
          <Box sx={{ display: "flex", alignItems: "center", px: 1.5, py: 1 }}>
            {aiLoading ? (
              <Box
                sx={{ display: "flex", alignItems: "center", gap: 1, flex: 1 }}
              >
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

      {/* ── Label + Falcon icon ── */}
      {!aiOpen && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 0.5,
          }}
        >
          <Typography variant="body2" fontWeight={600}>
            Prompt Messages<span style={{ color: "#d32f2f" }}>*</span>
          </Typography>
          <Tooltip title="Generate with Falcon AI" arrow placement="top">
            <IconButton
              size="small"
              onClick={() => setAiOpen(true)}
              disabled={disabled}
              sx={{
                width: 28,
                height: 28,
                "&:hover": { backgroundColor: "rgba(124,77,255,0.08)" },
              }}
            >
              <SvgColor
                src="/assets/icons/navbar/ic_falcon_ai.svg"
                sx={{ width: 18, height: 18, color: "primary.main" }}
              />
            </IconButton>
          </Tooltip>
        </Box>
      )}

      <MessageEditor
        messages={messages}
        onChange={onMessagesChange}
        templateFormat={templateFormat}
        onTemplateFormatChange={onTemplateFormatChange}
        model={model}
        onModelChange={onModelChange}
        datasetColumns={datasetColumns}
        datasetJsonSchemas={datasetJsonSchemas}
        disabled={disabled}
        modelSelectorDisabled={modelSelectorDisabled}
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
