import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import axios, { endpoints } from "src/utils/axios";
import { getAgentFormValues } from "./common";
import { useMutation } from "@tanstack/react-query";
import logger from "src/utils/logger";
import { AGENT_TYPES, isLiveKitProvider } from "./constants";

export const useAgentConfigForm = (schema, agentDetails) => {
  const {
    control,
    handleSubmit,
    reset,
    watch,
    formState,
    setError,
    setValue,
    getValues,
    trigger,
  } = useForm({
    mode: "onSubmit",
    resolver: zodResolver(schema, undefined, { mode: "async" }),
    defaultValues: getAgentFormValues(agentDetails),
  });

  useEffect(() => {
    if (agentDetails?.configuration_snapshot) {
      reset(getAgentFormValues(agentDetails));
    }
  }, [agentDetails, reset]);

  return {
    control,
    handleSubmit,
    reset,
    watch,
    formState,
    setError,
    setValue,
    getValues,
    trigger,
  };
};

export const useAgentSubmit = ({
  agentDefinitionId,
  reset,
  queryClient,
  enqueueSnackbar,
  setError,
  setSelectedVersion,
  onClose,
}) => {
  const { isPending, mutateAsync } = useMutation({
    mutationFn: (payload) =>
      axios.post(
        endpoints.agentDefinitions.createVersion(agentDefinitionId),
        payload,
      ),
  });

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
      if (data.agentType === "voice") {
        const fullContactNumber = data?.countryCode
          ? `+${data.countryCode}${data.contactNumber.trim()}`
          : data.contactNumber.trim();
        payload.contact_number = fullContactNumber;
        delete payload.model;
        delete payload.model_details;

        // Parse LiveKit fields for backend
        if (isLiveKitProvider(data.provider)) {
          payload.contact_number = "";
          payload.livekit_max_concurrency =
            parseInt(data.livekitMaxConcurrency, 10) || 5;
          if (
            !payload.livekit_config_json ||
            (typeof payload.livekit_config_json === "string" &&
              !payload.livekit_config_json.trim())
          ) {
            payload.livekit_config_json = {};
          } else if (typeof payload.livekit_config_json === "string") {
            try {
              payload.livekit_config_json = JSON.parse(
                payload.livekit_config_json,
              );
            } catch (e) {
              logger.error("Failed to parse livekit_config_json", e);
              payload.livekit_config_json = {};
            }
          }
        }

        // Remove LiveKit fields for non-LiveKit providers
        if (!isLiveKitProvider(data.provider)) {
          delete payload.livekit_url;
          delete payload.livekit_api_key;
          delete payload.livekit_api_secret;
          delete payload.livekit_agent_name;
          delete payload.livekit_config_json;
          delete payload.livekit_max_concurrency;
        }
      } else {
        // Remove voice-specific fields for non-voice agents
        delete payload.country_code;
        payload["contact_number"] = ""; //dummy number
        delete payload.provider;
        delete payload.api_key;
        delete payload.assistant_id;
        delete payload.observability_enabled;
        delete payload.authentication_method;
        delete payload.livekit_url;
        delete payload.livekit_api_key;
        delete payload.livekit_api_secret;
        delete payload.livekit_agent_name;
        delete payload.livekit_config_json;
        delete payload.livekit_max_concurrency;
      }

      if (payload?.authentication_method !== "api_key") {
        delete payload.api_key;
        delete payload.assistant_id;
        delete payload.observability_enabled;
      }

      //remove empty headers
      payload.headers = payload.headers?.filter(
        (header) => header?.key?.trim() !== "" && header?.value?.trim() !== "",
      );

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

      payload = Object.fromEntries(
        Object.entries(payload).filter(([, value]) => value !== ""),
      );

      const { livekit_api_key, livekit_api_secret, ...safePayload } = payload;
      trackEvent(Events.updateAgentDefClicked, {
        [PropertyName.id]: agentDefinitionId,
        [PropertyName.source]: "agent_def",
        [PropertyName.formFields]: safePayload,
      });

      const response = await mutateAsync(payload);

      reset(data);

      queryClient.invalidateQueries({
        queryKey: ["agent-definition-versions", agentDefinitionId],
      });

      enqueueSnackbar(
        response.data?.message || "Version updated successfully!",
        { variant: "success" },
      );

      setSelectedVersion(response.data?.version?.id || "");

      if (onClose) onClose(response);
    } catch (err) {
      setError(
        err.response?.data?.error ||
          err.response?.data?.details?.non_field_errors?.[0] ||
          "Failed to update agent definition. Please try again.",
      );
    }
  };

  return { onSubmit, isPending };
};
