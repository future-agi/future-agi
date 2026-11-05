import { Box } from "@mui/material";
import PropTypes from "prop-types";
import { useCallback, useMemo, useRef } from "react";
import PromptEditor from "src/components/PromptCards/PromptEditor";

/**
 * Single message block using PromptEditor (same Quill editor as agent InstructionEditor).
 * Handles the string↔blocks conversion and prevents re-init on every keystroke.
 */
const MessageEditorBlock = ({
  content,
  onContentChange,
  placeholder,
  minHeight = 50,
  dropdownOptions = [],
  mentionEnabled = true,
  mentionDenotationChars = ["{{"],
  onMentionSelect,
  disabled = false,
  templateFormat = "mustache",
  allVariablesValid = true,
  variableValidator,
  jinjaMode = false,
}) => {
  const quillRef = useRef(null);
  // Track whether we're in a controlled update to skip echo
  const isInternalChange = useRef(false);
  // Store the last known content to avoid re-init loops
  const lastContent = useRef(content);

  // Only re-create prompt blocks when content changes externally
  // (not from our own onPromptChange callback)
  const prompt = useMemo(() => {
    lastContent.current = content;
    return content ? [{ type: "text", text: content }] : [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content]);

  const handlePromptChange = useCallback(
    (blocks) => {
      const text = blocks
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("");
      // Only fire onChange if text actually changed
      if (text !== lastContent.current) {
        lastContent.current = text;
        isInternalChange.current = true;
        onContentChange(text);
        // Reset flag after React processes the update
        requestAnimationFrame(() => {
          isInternalChange.current = false;
        });
      }
    },
    [onContentChange],
  );

  return (
    <Box
      sx={{
        "& .ql-container": { border: "none !important", fontSize: "13px" },
        "& .ql-editor": {
          minHeight,
          padding: "4px 12px 8px",
          fontSize: "13px",
          lineHeight: 1.6,
        },
        "& .ql-editor.ql-blank::before": {
          left: 12,
          fontStyle: "normal",
        },
        ...(disabled && {
          cursor: "not-allowed",
          "& *": { cursor: "not-allowed !important" },
        }),
      }}
    >
      <PromptEditor
        key={templateFormat}
        ref={quillRef}
        placeholder={placeholder}
        prompt={prompt}
        onPromptChange={handlePromptChange}
        dropdownOptions={dropdownOptions}
        mentionEnabled={mentionEnabled}
        mentionDenotationChars={mentionDenotationChars}
        onMentionSelect={onMentionSelect}
        showEditEmbed={false}
        allowVariables={mentionEnabled}
        allVariablesValid={allVariablesValid}
        variableValidator={variableValidator}
        jinjaMode={jinjaMode}
        disabled={disabled}
        expandable
      />
    </Box>
  );
};

MessageEditorBlock.propTypes = {
  content: PropTypes.string,
  onContentChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  minHeight: PropTypes.number,
  dropdownOptions: PropTypes.array,
  mentionEnabled: PropTypes.bool,
  mentionDenotationChars: PropTypes.array,
  onMentionSelect: PropTypes.func,
  disabled: PropTypes.bool,
  templateFormat: PropTypes.string,
  allVariablesValid: PropTypes.bool,
  variableValidator: PropTypes.func,
  jinjaMode: PropTypes.bool,
};

export default MessageEditorBlock;
