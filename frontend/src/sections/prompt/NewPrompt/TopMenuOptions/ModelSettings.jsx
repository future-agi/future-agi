import React, { useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import ModelOptions from "src/sections/develop-detail/Common/ModelOptions";
import { useForm } from "react-hook-form";
import { CustomModelSelection } from "src/sections/develop-detail/Common/CustomModelSelection/CustomModelSelection";
import ConfigureKeys from "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";

import DrawerHeaderbar from "./DrawerHeaderbar";

// Fields sent with the model settings saved event: the model, its numeric
// options and the ids of the selected tools (each tool row holds the selected
// option, whose value is the tool id). Tool definitions are not part of the
// event.
const textOrUndefined = (value) =>
  typeof value === "string" ? value : undefined;

const modelSettingsEventFields = (data) => {
  const config = data?.config ?? {};
  return {
    config: {
      model: textOrUndefined(config.model),
      temperature: config.temperature,
      topP: config.topP,
      maxTokens: config.maxTokens,
      presencePenalty: config.presencePenalty,
      frequencyPenalty: config.frequencyPenalty,
      responseFormat: textOrUndefined(config.responseFormat),
      toolChoice: textOrUndefined(config.toolChoice),
      tool_ids: Array.isArray(config.tools)
        ? config.tools.map((row) => row?.tool?.value).filter(Boolean)
        : [],
    },
  };
};

const ModelSettings = (props) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);
  const { onClose, handleModelSettingData, initialConfig, handleLabelsAdd } =
    props;
  // const { control } = useForm();
  const { control, handleSubmit, setValue } = useForm({
    defaultValues: {
      config: {
        model: initialConfig?.model || "",
        temperature: initialConfig?.temperature || 0.5,
        topP: initialConfig?.topP || 1,
        maxTokens: initialConfig?.maxTokens || 4085,
        presencePenalty: initialConfig?.presencePenalty || 1,
        frequencyPenalty: initialConfig?.frequencyPenalty || 1,
        responseFormat: initialConfig?.responseFormat || "text",
        toolChoice: initialConfig?.toolChoice || "",
        tools: initialConfig?.tools || [],
      },
    },
  });

  const onSubmit = (data) => {
    handleModelSettingData(data);
    trackEvent(Events.modelSettingsApplied, {
      [PropertyName.formFields]: modelSettingsEventFields(data),
    });
    handleLabelsAdd(null);
    onClose();
  };

  return (
    <Box
      sx={{
        padding: "16px 0 20px 0",
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <DrawerHeaderbar title="Model Settings" onClose={onClose} />

      <Box sx={{ padding: "15px 16px" }}>
        <ConfigureKeys
          open={isApiConfigurationOpen}
          onClose={() => setApiConfigurationOpen(false)}
        />

        <CustomModelSelection
          control={control}
          fieldName="config.model"
          label="Language Model"
          fullWidth
          size="small"
          MenuProps={{
            sx: {
              maxHeight: "400px",
            },
          }}
          onConfigOpen={() => setApiConfigurationOpen(true)}
        />
      </Box>

      <Typography
        variant="caption"
        fontWeight="fontWeightBold"
        color="text.disabled"
        lineHeight="30px"
        display="block"
        padding="16px 16px 0 16px"
      >
        Model Options
      </Typography>
      <Box
        sx={{
          padding: "10px 16px 0 20px",
          height: "calc(100% - 60px)",
          overflowX: "hidden",
          overflowY: "auto",
        }}
      >
        <ModelOptions
          control={control}
          fieldNamePrefix="config"
          hideAccordion
          setValue={setValue}
        />
      </Box>

      {/* action buttons */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "20px 16px 0 16px",
        }}
      >
        <Button
          fullWidth
          variant="outlined"
          onClick={() => {
            trackEvent(Events.modelSettingsCancelled);
            onClose();
          }}
        >
          Cancel
        </Button>
        <Button
          fullWidth
          variant="contained"
          color="primary"
          // onClick={() => {}}
          onClick={handleSubmit(onSubmit)}
        >
          Save
        </Button>
      </Box>
    </Box>
  );
};

ModelSettings.propTypes = {
  handleLabelsAdd: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  handleModelSettingData: PropTypes.any,
  initialConfig: PropTypes.object,
};

export default ModelSettings;
