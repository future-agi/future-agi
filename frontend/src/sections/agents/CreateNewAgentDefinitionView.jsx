import {
  Box,
  Button,
  Divider,
  Grid,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useEffect, useMemo, useState } from "react";
import {
  createAgentDefinitionSchema,
  defaultAgentDefinitionValues,
  stepFields,
} from "./helper";
import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
// import CustomTooltip from "src/components/tooltip";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { useNavigate } from "react-router";
import { paths } from "src/routes/paths";
import { useSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import StepsTracker from "./CreateNewAgent/StepsTracker";
import { useCreateNewAgentStore } from "./store/createNewAgentStore";
import AgentConfigurationStep from "./CreateNewAgent/AgentConfigurationStep/AgentConfigurationStep";
import AgentBasicInfoStepRightSection from "./CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStepRightSection";
import AgentBasicInfoStep from "./CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStep";
import AgentConfigurationStepRightSection from "./CreateNewAgent/AgentConfigurationStep/AgentConfigurationStepRightSection";
import AgentBehaviourStepRightSection from "./CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection";
import AgentBehaviourStep from "./CreateNewAgent/AgentBehaviourStep/AgentBehaviourStep";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { AGENT_TYPES, isLiveKitProvider } from "./constants";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const CreateNewAgentDefinitionView = () => {
  const { role } = useAuthContext();
  const { currentStep, reset, nextStep, prevStep, setStepValidated } =
    useCreateNewAgentStore();
  const theme = useTheme();
  const methods = useForm({
    mode: "onSubmit",
    resolver: zodResolver(createAgentDefinitionSchema(), undefined, {
      mode: "async",
    }),
    defaultValues: defaultAgentDefinitionValues,
  });
  const {
    control,
    handleSubmit,
    getValues,
    formState: { errors, isSubmitting },
    trigger,
    watch,
  } = methods;

  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const [_, setError] = useState("");
  const livekitValidated = watch("_livekitCredentialsValid");

  const watchedValues = watch();
  const canProceed = useMemo(() => {
    const provider = watchedValues["provider"];
    const agentType = watchedValues["agentType"];
    const authenticationMethod = watchedValues["authenticationMethod"];
    const requiredFields = {
      0: ["agentType", "agentName", "languages"],
      1: ["provider"],
      2: ["description", "countryCode", "contactNumber", "commitMessage"],
    };

    if (isLiveKitProvider(provider)) {
      requiredFields[1].push(
        "livekitUrl",
        "livekitApiKey",
        "livekitApiSecret",
        "livekitAgentName",
      );
      // LiveKit doesn't need contact number
      requiredFields[2] = ["description", "commitMessage"];
    } else if (provider === "others") {
      if (authenticationMethod === "basicAuth") {
        requiredFields[1].push("username", "password");
      } else if (authenticationMethod === "bearerToken") {
        requiredFields[1].push("token");
      }
    }

    // if (watchedValues["agentType"] === AGENT_TYPES.CHAT) {
    //   requiredFields[1].pop();
    //   requiredFields[1].push("model");
    // }
    if (currentStep === 1 && agentType === AGENT_TYPES.CHAT) {
      return true;
    }
    const currentRequired = requiredFields[currentStep];

    const allFilled = currentRequired.every((field) => {
      const value = watchedValues[field]; // 👈 use watched values directly
      if (Array.isArray(value)) return value.length > 0;
      if (typeof value === "string") return value.trim() !== "";
      return value !== undefined && value !== null;
    });

    const hasErrors = currentRequired.some((field) => !!errors[field]);

    // LiveKit credentials must be validated on step 1
    if (currentStep === 1 && isLiveKitProvider(provider) && !livekitValidated) {
      return false;
    }

    return allFilled && !hasErrors;
  }, [watchedValues, errors, currentStep, livekitValidated]); // 👈 dependencies

  const handleNext = async () => {
    // @ts-ignore
    const valid = await trigger(stepFields[currentStep]);
    setStepValidated(currentStep, valid);

    if (valid && currentStep < 2) nextStep();
  };

  const onSubmit = async (data) => {
    try {
      // Build payload with snake_case keys for the backend
      let payload = {
        agent_type: data.agentType,
        agent_name: data.agentName,
        languages: data.languages,
        provider: data.provider,
        api_key: data.apiKey,
        assistant_id: data.assistantId,
        description: data.description,
        knowledge_base: data.knowledgeBase || null,
        // country_code: data.countryCode,
        contact_number: data.contactNumber,
        inbound: data.inbound,
        commit_message: data.commitMessage,
        observability_enabled: data.observabilityEnabled,
        authentication_method: "api_key",
        model: data.model,
        model_details: data.modelDetails,
        username: data.username,
        password: data.password,
        token: data.token,
        headers: data.headers,
        livekit_url: data.livekitUrl,
        livekit_api_key: data.livekitApiKey,
        livekit_api_secret: data.livekitApiSecret,
        livekit_agent_name: data.livekitAgentName,
        livekit_config_json: data.livekitConfigJson,
        livekit_max_concurrency: data.livekitMaxConcurrency,
      };

      // Only process and include voice-specific fields for voice agents
      if (data.agentType === AGENT_TYPES.VOICE) {
        if (isLiveKitProvider(data.provider)) {
          // LiveKit: no phone number needed, ensure config is a dict
          payload.contact_number = "";
          payload.livekit_max_concurrency =
            parseInt(data.livekitMaxConcurrency, 10) || 5;
          payload.livekit_config_json = data.livekitConfigJson || {};
          if (typeof payload.livekit_config_json === "string") {
            try {
              payload.livekit_config_json = payload.livekit_config_json.trim()
                ? JSON.parse(payload.livekit_config_json)
                : {};
            } catch (e) {
              logger.error("Failed to parse livekit_config_json", e);
              payload.livekit_config_json = {};
            }
          }
        } else {
          const fullContactNumber = data?.countryCode
            ? `+${data?.countryCode}${data.contactNumber.trim()}`
            : data.contactNumber.trim();
          payload.contact_number = fullContactNumber;
          // Clean up LiveKit fields
          delete payload.livekit_url;
          delete payload.livekit_api_key;
          delete payload.livekit_api_secret;
          delete payload.livekit_agent_name;
          delete payload.livekit_config_json;
          delete payload.livekit_max_concurrency;
        }
        delete payload.model;
        delete payload.model_details;
      } else {
        // Remove voice-specific fields for non-voice agents
        delete payload.country_code;
        payload["contact_number"] = ""; //dummy number
        delete payload.assistant_id;
        delete payload.observability_enabled;
        delete payload.provider;
        delete payload.api_key;
        delete payload.authentication_method;
        delete payload.livekit_url;
        delete payload.livekit_api_key;
        delete payload.livekit_api_secret;
        delete payload.livekit_agent_name;
        delete payload.livekit_config_json;
        delete payload.livekit_max_concurrency;
      }

      // Strip secrets and internal fields before analytics/submit
      const { livekit_api_key, livekit_api_secret, ...safePayload } = payload;
      trackEvent(Events.createAgentDefClicked, {
        [PropertyName.formFields]: safePayload,
      });

      if (payload?.authentication_method !== "api_key") {
        delete payload.api_key;
        delete payload.assistant_id;
        delete payload.observability_enabled;
      }

      if (
        (payload?.authentication_method === "api_key" &&
          payload.agent_type !== AGENT_TYPES.CHAT) ||
        payload?.headers?.length === 0
      ) {
        delete payload.headers;
      }
      if (payload?.authentication_method !== "basicAuth") {
        delete payload.username;
        delete payload.password;
      }
      if (payload?.authentication_method !== "bearerToken") {
        delete payload.token;
      }
      if (payload?.authentication_method === "noAuth") {
        delete payload.api_key;
        delete payload.assistant_id;
        delete payload.username;
        delete payload.password;
        delete payload.token;
      }

      // Strip all empty string values — omit keys not entered by the user
      payload = Object.fromEntries(
        Object.entries(payload).filter(
          ([_, value]) => value !== "" && value != null,
        ),
      );

      const response = await axios.post(
        endpoints.agentDefinitions.create,
        payload,
      );

      const newAgentDefId = response?.data?.agent?.id;
      enqueueSnackbar(response.data?.message || "Agent created successfully!", {
        variant: "success",
      });

      navigate(`${paths.dashboard.simulate.agentDefinition}/${newAgentDefId}`, {
        state: {
          newAgent: true,
          agentDefinitionId: newAgentDefId,
        },
      });
    } catch (err) {
      const apiError = err.response?.data;
      let message = "Failed to create agent definition. Please try again.";
      if (apiError) {
        if (apiError.error) {
          message = apiError.error;
        }

        if (apiError.details) {
          // Flatten all field errors into a single string
          const fieldErrors = Object.values(apiError.details).flat().join(" ");
          if (fieldErrors) message = fieldErrors;
        }

        if (apiError.details?.non_field_errors?.length) {
          message = apiError.details.non_field_errors.join(" ");
        }
      }

      setError(message);
      enqueueSnackbar(message, { variant: "error" });
    }
  };

  useEffect(() => {
    return () => {
      reset();
    };
  }, [reset]);

  const isLastStep = currentStep === stepFields.length - 1;
  return (
    <>
      <Box sx={{ backgroundColor: "background.paper" }}>
        <Box p={2}>
          <Box
            sx={{
              display: "flex",
              gap: "12px",
            }}
          >
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.disabled"
              onClick={() => {
                navigate(paths.dashboard.simulate.agentDefinition);
              }}
              sx={{ cursor: "pointer" }}
            >
              All Agent Definitions
            </Typography>
            <SvgColor src="/assets/icons/custom/lucide--chevron-right.svg" />
            <Typography typography="m3" fontWeight="fontWeightMedium">
              Create new agent definition
            </Typography>
          </Box>
        </Box>
        <Divider sx={{ borderColor: "divider" }} />
      </Box>
      <Box
        sx={{
          height: "calc(100vh - 60px)",
          overflow: "hidden",
        }}
      >
        <Grid container height="100%">
          {/* LEFT SECTION (7 parts) */}
          <Grid
            item
            xs={7}
            sx={{
              backgroundColor: "background.paper",
              display: "flex",
              flexDirection: "column",
              height: "100%",
              borderRight: "1px solid",
              borderColor: "divider",
              minHeight: 0,
            }}
          >
            {/* StepsTracker */}
            <Box p={2}>
              <StepsTracker />
            </Box>

            {/* Main scrollable content */}
            <Box
              flexGrow={1} // takes remaining space
              mt={2}
              p={2}
              sx={{
                overflow: "auto",
                "&::-webkit-scrollbar": { width: "6px" },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: "rgba(0, 0, 0, 0.3)",
                  borderRadius: "3px",
                },
                "&::-webkit-scrollbar-track": {
                  backgroundColor: "transparent",
                },
              }}
            >
              <FormProvider {...methods}>
                {currentStep === 0 && <AgentBasicInfoStep control={control} />}
                {currentStep === 1 && (
                  <AgentConfigurationStep
                    control={control}
                    getValues={getValues}
                  />
                )}
                {currentStep === 2 && (
                  <AgentBehaviourStep
                    control={control}
                    errors={errors}
                    trigger={trigger}
                  />
                )}
              </FormProvider>
            </Box>

            {/* Footer Buttons */}
            <Box
              mb={1.5}
              p={2}
              display="flex"
              justifyContent="space-between"
              borderTop="1px solid"
              borderColor="divider"
              flexShrink={0}
            >
              <Button
                startIcon={
                  <Iconify
                    icon="line-md:chevron-left"
                    width={16}
                    height={16}
                    color={"text.primary"}
                  />
                }
                onClick={prevStep}
                disabled={currentStep === 0}
                variant="outlined"
                sx={{
                  color: "text.primary",
                  padding: theme.spacing(0.125, 1.5),
                  height: 32,
                  fontWeight: "fontWeightMedium",
                  borderRadius: theme.spacing(0.5),
                  borderColor: "action.selected",
                }}
              >
                Back
              </Button>

              {!isLastStep ? (
                <Button
                  endIcon={
                    <Iconify
                      icon="line-md:chevron-right"
                      width={16}
                      height={16}
                    />
                  }
                  onClick={handleNext}
                  disabled={!canProceed}
                  variant="contained"
                  color="primary"
                  sx={{
                    padding: theme.spacing(0.125, 1.5),
                    height: 32,
                    fontWeight: "fontWeightMedium",
                    borderRadius: theme.spacing(0.5),
                    borderColor: "action.selected",
                  }}
                >
                  Next
                </Button>
              ) : (
                <LoadingButton
                  endIcon={
                    <Iconify
                      icon="line-md:chevron-right"
                      width={16}
                      height={16}
                    />
                  }
                  onClick={handleSubmit(onSubmit)}
                  variant="contained"
                  loading={isSubmitting}
                  disabled={
                    !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                  }
                  color="primary"
                  sx={{
                    padding: theme.spacing(0.125, 1.5),
                    height: 32,
                    fontWeight: "fontWeightMedium",
                    borderRadius: theme.spacing(0.5),
                    borderColor: "action.selected",
                  }}
                >
                  Create agent definition
                </LoadingButton>
              )}
            </Box>
          </Grid>

          {/* RIGHT SECTION (5 parts) */}
          <Grid
            item
            xs={5}
            sx={{
              backgroundColor: "background.paper",
              overflow: "auto",
              p: 2,
            }}
          >
            <Box>
              {currentStep === 0 && <AgentBasicInfoStepRightSection />}
              {currentStep === 1 && (
                <AgentConfigurationStepRightSection
                  control={control}
                  getValues={getValues}
                />
              )}
              {currentStep === 2 && (
                <AgentBehaviourStepRightSection control={control} />
              )}
            </Box>
          </Grid>
        </Grid>
      </Box>
    </>
  );
};

export default CreateNewAgentDefinitionView;
