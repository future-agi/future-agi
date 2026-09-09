import PropTypes from "prop-types";
import React, { useState } from "react";
import PromptTemplateCard from "./PromptTemplateCard";
import ImportPrompt from "../RunPrompt/Modals/ImportPrompt";
import { getRandomId } from "src/utils/utils";
import { getDefaultPromptConfig } from "./common";
import GeneratePromptDrawer from "src/components/GeneratePromptDrawer";
import ImprovePromptDrawer from "src/components/ImprovePromptDrawer";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const EXCLUDED_CONFIG_KEYS = new Set([
  "model",
  "tools",
  "voiceId",
  "modelType",
  "toolChoice",
  "modelDetail",
  "outputFormat",
]);

const PromptTemplateSection = ({
  control,
  fields,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  removePrompt,
  setValue,
  updatePrompt,
  getValues,
  importedPrompts,
  setImportedPrompts,
  responseSchema,
  clearErrors,
  unregister,
  errors,
  watch,
}) => {
  const [openImportPromptModal, setOpenImportPromptModal] = useState(null);
  const handleOpenImportPrompt = (index) => {
    if (Number.isNaN(index)) return;
    setOpenImportPromptModal(index);
  };

  const [openGeneratePromptDrawer, setOpenGeneratePromptDrawer] = useState({
    state: false,
    index: null,
    parentIndex: null,
  });
  const [openImprovePromptDrawer, setOpenImprovePromptDrawer] = useState({
    state: false,
    index: null,
    parentIndex: null,
  });

  const handleApplyImportedPrompt = (data, index) => {
    const messages = data?.promptVersion?.promptConfigSnapshot?.messages;
    const promptConfig =
      data?.promptVersion?.promptConfigSnapshot?.configuration;
    const prompt = data?.prompt;
    const promptVersion = data?.promptVersion?.templateVersion;
    const modelType =
      data?.promptVersion?.promptConfigSnapshot?.configuration?.modelDetail
        ?.type;

    const configuration = {
      toolChoice: promptConfig?.toolChoice ?? "auto",
      template_format: promptConfig?.template_format ?? "mustache",
      tools:
        promptConfig?.tools?.length > 0
          ? promptConfig?.tools?.map((t) => ({
              id: getRandomId(),
              tool: {
                label: t.name,
                value: t.id,
                tool: t,
              },
            }))
          : [],
    };

    const model = [];

    if (promptConfig?.model) {
      model.push({
        id: getRandomId(),
        value: promptConfig?.model,
        providers: promptConfig?.modelDetail?.providers,
        logoUrl: promptConfig?.modelDetail?.logoUrl,
      });
    }

    if (modelType === "image_generation") {
      setValue(`promptConfig.${index}.modelType`, "image");
    } else if (modelType === "tts") {
      setValue(`promptConfig.${index}.modelType`, "tts");
    } else if (modelType === "stt") {
      setValue(`promptConfig.${index}.modelType`, "stt");
    } else {
      setValue(`promptConfig.${index}.modelType`, "llm");
    }

    setValue(`promptConfig.${index}.messages`, messages, {
      shouldValidate: true,
    });
    setValue(`promptConfig.${index}.name`, prompt?.name, {
      shouldValidate: true,
    });
    setValue(`promptConfig.${index}.version`, promptVersion, {
      shouldValidate: true,
    });

    // Set model type
    setValue(
      `promptConfig.${index}.modelType`,
      promptConfig?.modelType ?? "llm",
      { shouldValidate: true },
    );

    // Set configuration (tools)
    setValue(`promptConfig.${index}.configuration`, configuration, {
      shouldValidate: true,
    });
    setValue(`promptConfig.${index}.configuration.tools`, configuration.tools, {
      shouldValidate: true,
    });

    // Set model
    setValue(`promptConfig.${index}.model`, model);

    // Set imported voice if TTS model with voiceId
    const importedVoiceId = promptConfig?.voiceId;
    if (modelType === "tts" && importedVoiceId && model.length > 0) {
      setValue(`promptConfig.${index}.voice`, [
        {
          model: model[0].value,
          voices: [importedVoiceId],
        },
      ]);
    }

    // Set imported model params (exclude known non-param config keys)
    if (model.length > 0) {
      const importedModelParams = {};
      Object.entries(promptConfig || {}).forEach(([key, value]) => {
        if (!EXCLUDED_CONFIG_KEYS.has(key)) {
          importedModelParams[key] = value;
        }
      });
      if (Object.keys(importedModelParams).length > 0) {
        setValue(
          `promptConfig.${index}.modelParams.${model[0].value}`,
          importedModelParams,
        );
      }
      // Set model params for the imported model so ModelFieldItem picks up saved values
      if (promptConfig?.model) {
        const savedModelParams = { ...promptConfig };
        // Remove keys that are not model params
        delete savedModelParams.model;
        delete savedModelParams.modelDetail;
        delete savedModelParams.tools;
        delete savedModelParams.toolChoice;
        delete savedModelParams.modelType;
        delete savedModelParams.outputFormat;

        setValue(
          `promptConfig.${index}.modelParams.${promptConfig.model}`,
          savedModelParams,
        );
      }
    }

    setImportedPrompts((prev) => ({
      ...prev,
      [index]: data,
    }));
  };

  const handleResetImportedPrompt = (index) => {
    setImportedPrompts((prev) => ({
      ...prev,
      [index]: undefined,
    }));
    updatePrompt(index, getDefaultPromptConfig());
  };

  const handleApplyPrompt = (messageIndex, prompt, parentIndex) => {
    const currentMessageFor = {
      ...getValues(`promptConfig.${parentIndex}.messages.${messageIndex}`),
      content: [{ type: "text", text: prompt }],
      id: getRandomId(),
    };
    setValue(
      `promptConfig.${parentIndex}.messages.${messageIndex}`,
      currentMessageFor,
    );
  };

  return (
    <>
      {fields.map((field, index) => (
        <PromptTemplateCard
          key={field.id}
          control={control}
          setValue={setValue}
          getValues={getValues}
          field={field}
          index={index}
          allColumns={allColumns}
          jsonSchemas={jsonSchemas}
          derivedVariables={derivedVariables}
          onRemove={() => {
            setImportedPrompts((prev) => ({
              ...prev,
              [index]: undefined,
            }));
            removePrompt(index);
          }}
          handleOpenImportPrompt={() => handleOpenImportPrompt(index)}
          promptImported={Boolean(importedPrompts[index])}
          handleResetImportedPrompt={() => handleResetImportedPrompt(index)}
          promptVersion={importedPrompts[index]}
          onGeneratePrompt={(childIndex) => {
            setOpenGeneratePromptDrawer({
              state: true,
              parentIndex: index,
              index: childIndex,
            });
            trackEvent(Events.datasetGeneratePromptClicked, {
              [PropertyName.type]: "Experiments",
            });
          }}
          responseSchema={responseSchema}
          onImprovePrompt={(childIndex) =>
            setOpenImprovePromptDrawer({
              state: true,
              parentIndex: index,
              index: childIndex,
            })
          }
          clearErrors={clearErrors}
          unregister={unregister}
          errors={errors}
          watch={watch}
        />
      ))}

      <ImportPrompt
        data={importedPrompts[openImportPromptModal]}
        handleApplyImportedPrompt={(data) =>
          handleApplyImportedPrompt(data, openImportPromptModal)
        }
        open={openImportPromptModal !== null}
        onClose={() => {
          setOpenImportPromptModal(null);
        }}
        allColumns={allColumns}
      />
      <GeneratePromptDrawer
        allColumns={allColumns}
        onApplyPrompt={(promptFor, generatedPrompt) =>
          handleApplyPrompt(
            promptFor,
            generatedPrompt,
            openGeneratePromptDrawer.parentIndex,
          )
        }
        promptFor={openGeneratePromptDrawer.index}
        open={openGeneratePromptDrawer.state}
        onClose={() =>
          setOpenGeneratePromptDrawer({
            index: null,
            parentIndex: null,
            state: false,
          })
        }
      />
      <ImprovePromptDrawer
        open={openImprovePromptDrawer.state}
        onClose={() => {
          setOpenImprovePromptDrawer({
            index: null,
            state: false,
            parentIndex: null,
          });
        }}
        variables={allColumns?.map((col) => col.headerName) ?? []}
        existingPrompt={
          getValues(
            `promptConfig.${openImprovePromptDrawer?.parentIndex}.messages.${openImprovePromptDrawer?.index}.content`,
          ) ?? ""
        }
        onApplyPrompt={(promptFor, generatedPrompt) =>
          handleApplyPrompt(
            promptFor,
            generatedPrompt,
            openImprovePromptDrawer.parentIndex,
          )
        }
        promptFor={openImprovePromptDrawer.index}
      />
    </>
  );
};

PromptTemplateSection.propTypes = {
  control: PropTypes.object,
  fields: PropTypes.array,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  removePrompt: PropTypes.func,
  setValue: PropTypes.func,
  resetField: PropTypes.func,
  updatePrompt: PropTypes.func,
  getValues: PropTypes.func,
  drawerOpen: PropTypes.bool,
  importedPrompts: PropTypes.object,
  setImportedPrompts: PropTypes.func,
  responseSchema: PropTypes.array,
  clearErrors: PropTypes.func,
  unregister: PropTypes.func,
  errors: PropTypes.object,
  watch: PropTypes.func,
};

export default PromptTemplateSection;
