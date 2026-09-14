import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { Controller } from "react-hook-form";
import PromptCard from "src/components/PromptCards/PromptCard";
import {
  extractVariableFromAllCols,
  getDropdownOptionsFromCols,
} from "../RunPrompt/common";

const PromptTemplateMessageList = ({
  control,
  promptField,
  messages,
  remove,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  onGeneratePrompt,
  onImprovePrompt,
  jinjaMode = false,
}) => {
  const [expandPrompt, setExpandPrompt] = useState({});
  const existingCols = useMemo(() => {
    return extractVariableFromAllCols(
      allColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [allColumns, jsonSchemas, derivedVariables]);

  const memoizedMentionValues = useMemo(() => {
    const result = getDropdownOptionsFromCols(
      allColumns,
      jsonSchemas,
      derivedVariables,
    );
    return result;
  }, [allColumns, jsonSchemas, derivedVariables]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {messages?.map((m, _idx) => (
        <Controller
          key={m.id}
          control={control}
          name={`${promptField}.messages.${_idx}`} // This controls the whole message object at index _idx
          render={({ field, fieldState: { error } }) => {
            return (
              <Box key={_idx}>
                <PromptCard
                  inputRef={field?.ref}
                  index={_idx}
                  role={field.value.role} // pass the role from form state
                  prompt={field.value.content} // pass the text from content[0]
                  onRemove={
                    _idx === 0
                      ? undefined
                      : () => {
                          remove(_idx);
                        }
                  }
                  appliedVariableData={existingCols}
                  dropdownOptions={memoizedMentionValues}
                  showEditEmbed={false}
                  mentionEnabled={true}
                  onRoleChange={(newRole) => {
                    // Update role while keeping content the same
                    field.onChange({
                      ...field.value,
                      role: newRole,
                    });
                  }}
                  onPromptChange={(content) => {
                    field.onChange({
                      ...field.value,
                      content,
                    });
                  }}
                  onGeneratePrompt={() => onGeneratePrompt(_idx)}
                  onImprovePrompt={() => onImprovePrompt(_idx)}
                  expandable
                  expandPrompt={expandPrompt?.[_idx] ?? false}
                  setExpandPrompt={(value) => {
                    setExpandPrompt((prev) => {
                      const copy = { ...prev };
                      copy[_idx] = value;
                      return copy;
                    });
                  }}
                  jinjaMode={jinjaMode}
                />
                {error?.content?.message && (
                  <Typography
                    id={`config.messages.${_idx}`}
                    variant="caption"
                    color={"error.main"}
                  >
                    {error?.content?.message}
                  </Typography>
                )}
              </Box>
            );
          }}
        />
      ))}
    </Box>
  );
};

PromptTemplateMessageList.propTypes = {
  control: PropTypes.object,
  promptField: PropTypes.string,
  messages: PropTypes.array,
  remove: PropTypes.func,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  jinjaMode: PropTypes.bool,
};

export default PromptTemplateMessageList;
