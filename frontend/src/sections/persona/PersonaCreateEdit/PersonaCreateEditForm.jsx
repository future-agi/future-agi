import { Box, Button, Divider, Typography, Link } from "@mui/material";
import React from "react";
import PersonaBasicInfo from "./PersonaBasicInfo";
import { FormProvider, useForm } from "react-hook-form";
import PersonaConversationSetting from "./PersonaConversationSetting";
import PersonaAddCustomProperties from "./PersonaAddCustomProperties";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  getPersonaDefaultValues,
  PersonCreateValidationSchema,
} from "./common";
import { zodResolver } from "@hookform/resolvers/zod";
import PrintFormValues from "src/utils/PrintFormValues";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import logger from "src/utils/logger";
import { ShowComponent } from "src/components/show";
import { PersonaChatSettings } from "./PersonaChatSettings";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PersonaBehavioralSetting from "./PersonaBehaviouralSetting";

const PersonaCreateEditForm = ({
  onSuccess,
  onCancel,
  editPersona,
  type = AGENT_TYPES.VOICE,
}) => {
  const isEditMode = Boolean(editPersona);
  const form = useForm({
    defaultValues: getPersonaDefaultValues({
      ...editPersona,
      simulationType: type,
    }),
    resolver: zodResolver(PersonCreateValidationSchema),
  });
  const { setValue: _setValue } = form;
  logger.debug("form", form.formState.errors);

  const queryClient = useQueryClient();

  const { mutate: createPersona, isPending } = useMutation({
    mutationFn: (data) => axios.post(endpoints.persona.create, data),
    onSuccess: (data) => {
      onSuccess?.(data);
      queryClient.invalidateQueries({ queryKey: ["personas"], exact: false });
      enqueueSnackbar("Persona created successfully", { variant: "success" });
    },
  });

  const { mutate: updatePersona, isPending: isUpdating } = useMutation({
    mutationFn: (data) =>
      axios.patch(endpoints.persona.update(editPersona?.id), data),
    onSuccess: (data) => {
      onSuccess?.(data);
      queryClient.invalidateQueries({ queryKey: ["personas"], exact: false });
      enqueueSnackbar("Persona updated successfully", { variant: "success" });
    },
  });

  const onSubmit = (data) => {
    // Map camelCase form fields to snake_case for API
    const payload = {
      name: data.name,
      description: data.description,
      gender: data.gender,
      age_group: data.ageGroup,
      location: data.location,
      profession: data.profession,
      personality: data.personality,
      communication_style: data.communicationStyle,
      accent: data.accent,
      language: data.language,
      conversation_speed: data.conversationSpeed,
      background_sound: data.backgroundSound,
      finished_speaking_sensitivity: data.finishedSpeakingSensitivity,
      interrupt_sensitivity: data.interruptSensitivity,
      custom_properties: data.customProperties,
      additional_instruction: data.additionalInstruction,
      multilingual: data.multilingual,
      simulation_type: data.simulationType,
      punctuation: data.punctuation,
      typos_frequency: data.typosFrequency,
      slang_usage: data.slangUsage,
      regional_mix: data.regionalMix,
      emoji_usage: data.emojiUsage,
      tone: data.tone,
      verbosity: data.verbosity,
    };

    if (isEditMode) {
      const updatePayload = {
        ...payload,
        languages: data?.language,
      };
      delete updatePayload?.language;
      updatePersona(updatePayload);
    } else {
      createPersona(payload);
    }
  };

  logger.debug("form.formState.errors", form.formState.errors);
  return (
    <FormProvider {...form}>
      <Box
        sx={{
          padding: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
        }}
        component="form"
        onSubmit={form.handleSubmit(onSubmit)}
      >
        <PrintFormValues control={form.control} />
        <Box>
          <Box
            display="flex"
            alignItems="center"
            justifyContent="space-between"
            width="93%"
          >
            <Typography typography="m2" fontWeight={600}>
              {isEditMode ? "Edit persona" : "Create persona"}
            </Typography>
            <Link
              href="https://docs.futureagi.com/docs/simulation/concepts/personas"
              underline="always"
              color="blue.500"
              target="_blank"
              rel="noopener noreferrer"
              fontWeight="fontWeightMedium"
            >
              Learn more
            </Link>
          </Box>

          <Typography
            typography="s1"
            fontWeight="fontWeightRegular"
            color="text.secondary"
          >
            Create custom personas for more realistic scenario
          </Typography>
        </Box>
        <Divider flexItem orientation="horizontal" />
        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <PersonaBasicInfo />
          <PersonaBehavioralSetting type={type} showClearButton={true} />
          <ShowComponent condition={type === AGENT_TYPES.VOICE}>
            <PersonaConversationSetting showClearButton={true} />
          </ShowComponent>
          <ShowComponent condition={type === AGENT_TYPES.CHAT}>
            <PersonaChatSettings />
          </ShowComponent>

          <PersonaAddCustomProperties />
          <FormTextFieldV2
            label="Additional instructions"
            control={form.control}
            fieldName="additionalInstruction"
            size="small"
            fullWidth
            placeholder="Add instructions to help us create personas"
            multiline
            rows={3}
          />
        </Box>
        <Box sx={{ display: "flex", gap: 2 }}>
          <Button
            variant="outlined"
            color="primary"
            size="small"
            fullWidth
            onClick={onCancel}
          >
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            size="small"
            fullWidth
            type="submit"
            loading={isPending || isUpdating}
          >
            {isEditMode ? "Update" : "Save"}
          </LoadingButton>
        </Box>
      </Box>
    </FormProvider>
  );
};

PersonaCreateEditForm.propTypes = {
  onSuccess: PropTypes.func,
  onCancel: PropTypes.func,
  editPersona: PropTypes.object,
  type: PropTypes.string,
};

export default PersonaCreateEditForm;
