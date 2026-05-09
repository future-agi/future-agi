import React, { useMemo, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useFieldArray } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import PromptCard from "src/components/PromptCards/PromptCard";
import { PromptRoles } from "src/utils/constants";
import {
  extractVariableFromAllCols,
  getDropdownOptionsFromCols,
} from "./common";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useDevelopDetailContext } from "src/pages/dashboard/Develop/Context/DevelopDetailContext";
import TemplateFormatSelector from "src/sections/workbench/createPrompt/Playground/TemplateFormatSelector";

const PromptTemplatesSection = ({
  control,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  onOpenGeneratePromptDrawer,
  setValue,
  watch,
  allInvalidVariables,
  errors,
  clearErrors,
  onOpenImprovePromptDrawer,
}) => {
  const { getActionSource, clearActionSource } = useDevelopDetailContext();
  const { fields, append, remove } = useFieldArray({
    control,
    name: "config.messages",
  });
  const [expandPrompt, setExpandPrompt] = useState({});

  const messages = watch("config.messages");

  const memoizedMentionValues = useMemo(() => {
    return getDropdownOptionsFromCols(
      allColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [allColumns, jsonSchemas, derivedVariables]);

  const existingCols = useMemo(() => {
    return extractVariableFromAllCols(
      allColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [allColumns, jsonSchemas, derivedVariables]);

  const onPromptChange = (content, index) => {
    setValue(`config.messages[${index}].content`, content, {
      shouldDirty: true,
    });
    clearErrors(`config.messages[${index}].content`);
  };

  const onRoleChange = (role, index) => {
    setValue(`config.messages[${index}].role`, role, {
      shouldDirty: true,
    });
    clearErrors(`config.messages[${index}].content`);
  };

  const templateFormat = watch("config.template_format") || "mustache";

  const existingRoles = messages.map((p) => p?.role);
  return (
    <Box sx={{ gap: "16px", display: "flex", flexDirection: "column" }}>
      <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
        <TemplateFormatSelector
          value={templateFormat}
          onChange={(v) =>
            setValue("config.template_format", v, { shouldDirty: true })
          }
        />
      </Box>
      {/* {fields.map(({ id }, index) => (
            <PromptSection
              key={id}
              control={control}
              onRemove={index === 0 ? undefined : () => remove(index)}
              allColumns={allColumns}
              roleSelectDisabled={false}
              prefixControlString={`config.messages.${index}`}
              onGeneratePrompt={() => onOpenGeneratePromptDrawer({
                state: true,
                index
              })}
            />
          ))} */}
      {fields.map(({ id }, _idx) => (
        <Stack key={id}>
          <PromptCard
            key={id}
            role={messages?.[_idx]?.role}
            required={_idx == 1 && messages?.[_idx]?.role === PromptRoles.USER}
            index={_idx}
            prompt={messages?.[_idx]?.content}
            onRemove={_idx === 0 ? undefined : () => remove(_idx)}
            onRoleChange={(role) => onRoleChange(role, _idx)}
            onPromptChange={(content) => onPromptChange(content, _idx)}
            appliedVariableData={existingCols}
            dropdownOptions={memoizedMentionValues}
            showEditEmbed={false}
            mentionEnabled={true}
            onGeneratePrompt={() => {
              onOpenGeneratePromptDrawer({
                state: true,
                index: _idx,
              });
              trackEvent(Events.datasetGeneratePromptClicked, {
                [PropertyName.type]: getActionSource() || "Run prompt",
              });
              clearActionSource();
            }}
            onImprovePrompt={() => {
              onOpenImprovePromptDrawer({
                state: true,
                index: _idx,
              });
            }}
            expandable
            expandPrompt={expandPrompt?.[_idx] ?? false}
            setExpandPrompt={(value) => {
              setExpandPrompt((prev) => {
                const copy = { ...prev };
                copy[_idx] = value;
                return copy;
              });
            }}
            viewOptions={{
              allowAllRoleChange: true,
              allowRoleChange: true,
            }}
            existingRoles={existingRoles}
            jinjaMode={templateFormat === "jinja"}
          />
          {errors?.config?.messages?.[_idx]?.content?.message && (
            <Typography
              id={`config.messages.${_idx}`}
              variant="caption"
              color={"error.main"}
            >
              {errors?.config?.messages?.[_idx]?.content?.message}
            </Typography>
          )}
          {Boolean(_idx === fields?.length - 1) && (
            <Typography
              id={"invalid-variables-message"}
              typography="s2"
              color={
                allInvalidVariables?.length > 0 ? "red.500" : "text.primary"
              }
              fontWeight={"fontWeightRegular"}
              sx={{
                mt: (theme) => theme.spacing(1),
              }}
            >
              Use the variables corresponding to the column names to optimize
              the run prompt for best performance.
            </Typography>
          )}
        </Stack>
      ))}

      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Button
          size="small"
          startIcon={<Iconify icon="material-symbols:add" />}
          onClick={() => {
            append({
              id: getRandomId(),
              role: PromptRoles.USER,
              content: [
                {
                  type: "text",
                  text: "",
                },
              ],
            });
          }}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            padding: "6px 16px",
            color: "text.primary",
          }}
        >
          <Typography typography="s2" fontWeight={"fontWeightMedium"}>
            Add Message
          </Typography>
        </Button>
      </Box>
    </Box>
  );
};

PromptTemplatesSection.propTypes = {
  control: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  onOpenGeneratePromptDrawer: PropTypes.func,
  setValue: PropTypes.func,
  watch: PropTypes.any,
  allInvalidVariables: PropTypes.array,
  errors: PropTypes.object,
  clearErrors: PropTypes.func,
  onOpenImprovePromptDrawer: PropTypes.func,
};

export default PromptTemplatesSection;
