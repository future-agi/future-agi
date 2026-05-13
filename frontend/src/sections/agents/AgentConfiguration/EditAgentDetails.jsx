import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, Typography, Grid, Stack, Button } from "@mui/material";
import PropTypes from "prop-types";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import Image from "src/components/image";
import {
  languageOptions,
  pinCodeOptions,
} from "src/components/agent-definitions/helper";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { useNavigate, useParams } from "react-router";
import SwitchField from "src/components/Switch/SwitchField";
import LanguageMultiSelect from "../CreateNewAgent/AgentBasicInfoStep/LanguageMultiSelect";
import { useWatch } from "react-hook-form";
import {
  AGENT_TYPES,
  AUTH_METHODS_BY_PROVIDER,
  VOICE_CHAT_PROVIDERS,
  INBOUND_OUTBOUND_COPY,
  isLiveKitProvider,
  validateLiveKitCredentials,
} from "../constants";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";
import { PROVIDER_CHOICES } from "../constants";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import SvgColor from "src/components/svg-color";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { spin } from "../../../animations/animations";
import { formatDistanceToNow } from "date-fns";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import { MODEL_TYPES } from "../../develop-detail/RunPrompt/common";
import { useAuthContext } from "src/auth/hooks";

const EditAgentDetails = ({
  control,
  errors,
  trigger,
  setValue,
  getValues,
}) => {
  const { orgLimit } = useAuthContext();
  const { agentDefinitionId } = useParams();
  const [lastFetchedAt, setLastFetchedAt] = useState(null);
  const [showSyncSuccess, setShowSyncSuccess] = useState(false);
  const syncSuccessTimeoutRef = useRef(null);
  const apiKey = useWatch({
    control,
    name: "apiKey",
    defaultValue: getValues("apiKey"),
  });
  const agentName = useWatch({
    control,
    name: "agentName",
    defaultValue: getValues("agentName"),
  });
  const assistantId = useWatch({
    control,
    name: "assistantId",
    defaultValue: getValues("assistantId"),
  });
  const observabilityEnabled = useWatch({
    control,
    name: "observabilityEnabled",
    defaultValue: getValues("observabilityEnabled"),
  });
  const selectedProvider = useWatch({
    control,
    name: "provider",
    defaultValue: getValues("provider"),
  });

  const authenticationMethod = useWatch({
    control,
    name: "authenticationMethod",
    defaultValue: getValues("authenticationMethod"),
  });

  const agentType = useWatch({
    control,
    name: "agentType",
    defaultValue: getValues("agentType"),
  });

  const inbound = useWatch({
    control,
    name: "inbound",
    defaultValue: getValues("inbound"),
  });

  // LiveKit credential validation
  const livekitUrl = useWatch({ control, name: "livekitUrl" });
  const livekitApiKey = useWatch({ control, name: "livekitApiKey" });
  const livekitApiSecret = useWatch({ control, name: "livekitApiSecret" });
  const livekitAgentName = useWatch({ control, name: "livekitAgentName" });
  const [livekitValidation, setLivekitValidation] = useState("idle");
  const [livekitValidationError, setLivekitValidationError] = useState("");

  const validateLivekitCredentials = async () => {
    setLivekitValidation("validating");
    setLivekitValidationError("");
    const result = await validateLiveKitCredentials({
      livekitUrl,
      livekitApiKey,
      livekitApiSecret,
      livekitAgentName,
      agentDefinitionId,
    });
    if (result.valid) {
      setLivekitValidation("valid");
    } else {
      setLivekitValidation("invalid");
      setLivekitValidationError(result.error);
    }
  };

  const { mutate, isPending } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) =>
      axios.post(endpoints.agentDefinitions.fetchAssistantFromProvider, data),
    onSuccess: (data) => {
      const providerData = data?.data?.result;
      if (!agentName?.includes(providerData?.name)) {
        setValue("agentName", `${agentName} (${providerData?.name})`, {
          shouldDirty: true,
        });
      }
      setValue("assistantId", providerData?.assistant_id, {
        shouldDirty: true,
      });
      setValue("commitMessage", providerData?.commit_message, {
        shouldDirty: true,
      });
      setValue("description", providerData?.prompt, { shouldDirty: true });
      setValue("apiKey", providerData?.api_key, { shouldDirty: true });
      setLastFetchedAt(new Date());
      setShowSyncSuccess(true);
    },
    
  });

  useEffect(() => {
    if (showSyncSuccess) {
      if (syncSuccessTimeoutRef.current) {
        clearTimeout(syncSuccessTimeoutRef.current);
      }
      syncSuccessTimeoutRef.current = setTimeout(() => {
        setShowSyncSuccess(false);
      }, 2500);
    }

    return () => {
      if (syncSuccessTimeoutRef.current) {
        clearTimeout(syncSuccessTimeoutRef.current);
      }
    };
  }, [showSyncSuccess]);

  const debounceTimeoutRef = useRef(null);

  const debouncedMutate = useCallback(
    (data) => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
      }
      debounceTimeoutRef.current = setTimeout(() => {
        mutate(data);
      }, 500);
    },
    [mutate],
  );

  useEffect(() => {
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
      }
    };
  }, []);

  const handleSyncWithProvider = async () => {
    if (!apiKey || !assistantId) {
      enqueueSnackbar({
        message: "Please add a valid API key and assistant ID",
        variant: "warning",
      });
      return;
    }
    const isValid = await trigger(["apiKey", "assistantId"]);
    if (isValid) {
      mutate({
        api_key: apiKey,
        assistant_id: assistantId,
        provider: provider,
      });
    }
  };

  const canEnableObservability = Boolean(apiKey && assistantId);
  const keysRequired = inbound === false;
  // "others" provider brings its own endpoint, so outbound has no
  // meaning (nothing to call). Lock the toggle to inbound.
  const outboundLocked = selectedProvider === "others";

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null, {
    status: true,
  });

  const provider = useWatch({
    control,
    name: "provider",
  });

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  useEffect(() => {
    if (observabilityEnabled && (!apiKey || !assistantId)) {
      setValue("observabilityEnabled", false);
    }
  }, [apiKey, assistantId, observabilityEnabled, setValue]);

  return (
    <Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {/* Agent Name and Description */}
        <Box display="flex" flexDirection="column" gap={3}>
          <Box display="flex" flexDirection="column" gap={3}>
            <FormTextFieldV2
              label="Agent Name"
              required
              control={control}
              fieldName="agentName"
              placeholder="Enter agent name"
              size="small"
              fullWidth
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
            />
            <FormSearchSelectFieldControl
              control={control}
              fieldName="agentType"
              label="Agent type"
              required
              placeholder="Select agent type"
              size="small"
              fullWidth
              disabled={true}
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
              options={[
                {
                  label: "Voice",
                  value: "voice",
                },
                {
                  label: "Chat",
                  value: "text",
                },
              ]}
            />
            <ShowComponent condition={agentType === AGENT_TYPES.CHAT}>
              <CustomModelDropdownControl
                control={control}
                fieldName="model"
                label="Model Used"
                fullWidth
                searchDropdown
                size="small"
                inputSx={{
                  "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
                    fontWeight: "fontWeightMedium",
                    color: "text.disabled",
                  },
                  "&.Mui-focused.MuiInputLabel-shrink": {
                    color: "text.disabled",
                  },
                  "& .MuiInputLabel-root.Mui-focused": {
                    color: "text.secondary",
                  },
                }}
                showIcon
                requireUserApiKey={false}
                modelObjectKey={"modelDetails"}
                extraParams={{ model_type: MODEL_TYPES.LLM }}
              />
            </ShowComponent>
          </Box>
          <Box>
            <LanguageMultiSelect
              control={control}
              fieldName="languages"
              label="Select language"
              placeholder="Select language"
              size="small"
              required
              options={languageOptions.map((option) => ({
                label: option.label,
                id: option.value,
              }))}
              fullWidth
              dropDownMaxHeight={250}
              helperText={
                <Typography
                  typography={"s3"}
                  color={"text.primary"}
                  fontWeight={"fontWeightRegular"}
                >
                  Select the languages in which your agent can converse in
                </Typography>
              }
            />
          </Box>
        </Box>
        {/* Assistant ID and Provider */}
        <Box display="flex" flexDirection="column" gap={3}>
          <ShowComponent condition={agentType === AGENT_TYPES.VOICE}>
            <FormSearchSelectFieldControl
              fieldName="provider"
              control={control}
              placeholder="Select Provider"
              label="Provider"
              fullWidth
              required
              size="small"
              options={VOICE_CHAT_PROVIDERS}
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
              onChange={(e) => {
                const value = e.target.value;
                const mainProviders = [
                  "vapi",
                  "retell",
                  "elevenlabs",
                  "livekit",
                  "livekit_bridge",
                ];

                // Clear authenticationMethod only if switching to or from "others"
                const isPrevMain = mainProviders.includes(selectedProvider);
                const isNextMain = mainProviders.includes(value);

                if (value !== selectedProvider) {
                  if (isPrevMain && isNextMain) {
                    // between vapi/retell/elevenlabs → keep authenticationMethod
                  } else {
                    setValue("authenticationMethod", "");
                    setValue("apiKey", "");
                  }
                  // Clear LiveKit fields when switching away from livekit
                  if (isLiveKitProvider(selectedProvider)) {
                    setValue("livekitUrl", "");
                    setValue("livekitApiKey", "");
                    setValue("livekitApiSecret", "");
                    setValue("livekitAgentName", "");
                    setValue("livekitConfigJson", {});
                    setValue("livekitMaxConcurrency", 2);
                  }
                  // "others" provider has no outbound path (user's own
                  // endpoint, nothing for us to call), so snap back to
                  // inbound if the user had outbound selected before.
                  if (value === "others") {
                    setValue("inbound", true, { shouldDirty: true });
                  }
                }
              }}
            />
            <ShowComponent condition={provider === PROVIDER_CHOICES.RETELL}>
              <Stack
                sx={{
                  borderRadius: 0.5,
                  bgcolor: "blue.o10",
                  border: "1px solid",
                  borderColor: "blue.200",
                  p: 1,
                  alignItems: "center",
                }}
                direction={"row"}
                gap={1}
              >
                <Iconify icon="ci:info" sx={{ color: "blue.400" }} width={16} />
                <Stack direction={"row"} gap={0.5}>
                  <Typography typography={"s2_1"} color={"blue.500"}>
                    Please add
                  </Typography>
                  <Typography
                    typography={"s2_1"}
                    onClick={() => {
                      copyToClipboard(
                        "https://api.futureagi.com/tracer/webhook",
                      );
                      enqueueSnackbar({
                        message: "Copied to clipboard",
                        variant: "success",
                      });
                    }}
                    color={"blue.500"}
                    sx={{
                      textDecorationLine: "underline",
                      ":hover": {
                        cursor: "pointer",
                      },
                    }}
                  >
                    https://api.futureagi.com/tracer/webhook
                  </Typography>
                  <Typography typography={"s2_1"} color={"blue.500"}>
                    to the Agent Level Webhook URL on Retell
                  </Typography>
                </Stack>
              </Stack>
            </ShowComponent>
            <ShowComponent
              condition={
                selectedProvider !== "others" &&
                !isLiveKitProvider(selectedProvider)
              }
            >
              <Stack direction={"column"} gap={0.75}>
                <Stack direction={"row"} alignItems={"center"} gap={2}>
                  <FormTextFieldV2
                    control={control}
                    fieldName="assistantId"
                    placeholder="asst_xxx"
                    required={observabilityEnabled || keysRequired}
                    label="Assistant ID"
                    fullWidth
                    size="small"
                    disabled={isPending}
                    error={errors && !!errors.assistantId?.message}
                    helperText={errors && errors.assistantId?.message}
                    sx={{
                      "& .MuiInputLabel-root": {
                        fontWeight: 500,
                      },
                    }}
                    onChange={(e) => {
                      trigger("assistantId");
                      const value = e.target.value;
                      if (apiKey && value && selectedProvider) {
                        debouncedMutate({
                          api_key: apiKey,
                          assistant_id: value,
                          provider: selectedProvider,
                        });
                      }
                    }}
                  />
                  <Button
                    variant="outlined"
                    color="primary"
                    disabled={isPending}
                    sx={{
                      flexShrink: 0,
                    }}
                    startIcon={
                      <SvgColor
                        src="/assets/icons/ic_refresh.svg"
                        sx={{
                          animation: isPending
                            ? `${spin} 1s linear infinite`
                            : "none",
                        }}
                      />
                    }
                    onClick={handleSyncWithProvider}
                  >
                    Sync with provider
                  </Button>
                </Stack>
                <ShowComponent condition={isPending}>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "row",
                      alignItems: "center",
                      gap: 1,
                      color: "blue.600",
                    }}
                  >
                    <SvgColor
                      src="/assets/icons/ic_refresh.svg"
                      sx={{
                        animation: `${spin} 1s linear infinite`,
                        width: "16px",
                        height: "16px",
                      }}
                    />
                    <Typography
                      typography={"s3"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {`Syncing with ${selectedProvider}...`}
                    </Typography>
                  </Box>
                </ShowComponent>
                <ShowComponent condition={showSyncSuccess && !isPending}>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "row",
                      alignItems: "center",
                      gap: 0.5,
                      color: "green.600",
                    }}
                  >
                    <SvgColor
                      src="/assets/icons/ic_success_fill.svg"
                      sx={{ width: 16, height: 16, color: "green.600" }}
                    />
                    <Typography
                      typography={"s3"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {`Synced with ${selectedProvider}`}
                    </Typography>
                  </Box>
                </ShowComponent>
              </Stack>
            </ShowComponent>
            <ShowComponent
              condition={
                selectedProvider !== "others" &&
                !isLiveKitProvider(selectedProvider)
              }
            >
              <FormSearchSelectFieldControl
                control={control}
                fieldName="authenticationMethod"
                label="Authentication Method"
                placeholder="Select authentication method"
                size="small"
                fullWidth
                sx={{
                  "& .MuiInputLabel-root": {
                    fontWeight: 500,
                  },
                }}
                options={AUTH_METHODS_BY_PROVIDER[selectedProvider] || []}
                required={observabilityEnabled}
              />
            </ShowComponent>
            <ShowComponent
              condition={
                selectedProvider !== "others" &&
                !isLiveKitProvider(selectedProvider)
              }
            >
              <FormTextFieldV2
                control={control}
                fieldName="apiKey"
                placeholder="Enter API key"
                label="Provider API Key"
                required={observabilityEnabled || keysRequired}
                error={errors && !!errors.apiKey?.message}
                helperText={errors && errors.apiKey?.message}
                fullWidth
                size="small"
                onChange={(e) => {
                  trigger("apiKey");
                  const value = e.target.value;
                  if (assistantId && value && selectedProvider) {
                    debouncedMutate({
                      api_key: value,
                      assistant_id: assistantId,
                      provider: selectedProvider,
                    });
                  }
                }}
              />
            </ShowComponent>
          </ShowComponent>
          {/* LiveKit-specific fields */}
          <ShowComponent condition={isLiveKitProvider(selectedProvider)}>
            <FormTextFieldV2
              control={control}
              fieldName="livekitUrl"
              placeholder="https://your-project.livekit.cloud"
              label="LiveKit Server URL"
              required
              fullWidth
              size="small"
              error={errors && !!errors.livekitUrl?.message}
              helperText={errors && errors.livekitUrl?.message}
            />
            <FormTextFieldV2
              control={control}
              fieldName="livekitApiKey"
              placeholder="APIxxxxxxxxxxxxxxxx"
              label="LiveKit API Key"
              required
              fullWidth
              size="small"
              error={errors && !!errors.livekitApiKey?.message}
              helperText={errors && errors.livekitApiKey?.message}
            />
            <FormTextFieldV2
              control={control}
              fieldName="livekitApiSecret"
              placeholder="Paste the secret you copied when creating the key"
              label="LiveKit API Secret"
              type="password"
              required
              fullWidth
              size="small"
              error={errors && !!errors.livekitApiSecret?.message}
              helperText={errors && errors.livekitApiSecret?.message}
            />
            <Stack direction="row" spacing={1} alignItems="center">
              <Box sx={{ flex: 1 }}>
                <FormTextFieldV2
                  control={control}
                  fieldName="livekitAgentName"
                  placeholder="e.g. test-agent"
                  label="Agent Name"
                  required
                  fullWidth
                  size="small"
                  error={errors && !!errors.livekitAgentName?.message}
                  helperText={errors && errors.livekitAgentName?.message}
                />
              </Box>
              <Button
                variant="outlined"
                size="small"
                onClick={validateLivekitCredentials}
                disabled={
                  livekitValidation === "validating" ||
                  !livekitUrl ||
                  !livekitApiKey ||
                  !livekitApiSecret ||
                  !livekitAgentName
                }
                sx={{
                  minWidth: 120,
                  height: 40,
                  whiteSpace: "nowrap",
                  fontSize: 13,
                  borderColor:
                    livekitValidation === "valid"
                      ? "success.main"
                      : livekitValidation === "invalid"
                        ? "error.main"
                        : "divider",
                  color:
                    livekitValidation === "valid"
                      ? "success.main"
                      : livekitValidation === "invalid"
                        ? "error.main"
                        : "text.secondary",
                }}
              >
                {livekitValidation === "validating"
                  ? "Testing..."
                  : livekitValidation === "valid"
                    ? "Connected"
                    : livekitValidation === "invalid"
                      ? "Failed"
                      : "Test Connection"}
              </Button>
            </Stack>
            {livekitValidation === "invalid" && livekitValidationError && (
              <Typography typography="s3" color="error.main" sx={{ mt: -1 }}>
                {livekitValidationError}
              </Typography>
            )}
            <FormTextFieldV2
              control={control}
              fieldName="livekitConfigJson"
              placeholder='{"key": "value"}'
              label="Room Config JSON (Optional)"
              fullWidth
              size="small"
              multiline
              rows={6}
            />
            <FormTextFieldV2
              control={control}
              fieldName="livekitMaxConcurrency"
              label="Max Concurrent Sessions"
              placeholder="5"
              type="number"
              size="small"
              fullWidth
              inputProps={{ min: 1, max: orgLimit }}
              helperText={`Max simultaneous test calls. If you have multiple agent workers, set this to the total capacity across all workers (default: 5, max: ${orgLimit})`}
            />
          </ShowComponent>
          <ShowComponent
            condition={
              selectedProvider === "others" &&
              authenticationMethod === "basicAuth"
            }
          >
            <Stack direction={"row"} spacing={1.5}>
              <FormTextFieldV2
                control={control}
                fieldName="username"
                label="Username"
                placeholder="Add username"
                size="small"
                fullWidth
                required
              />
              <FormTextFieldV2
                control={control}
                fieldName="password"
                label="Password"
                placeholder="Add password"
                size="small"
                fullWidth
                required
              />
            </Stack>
          </ShowComponent>
          <ShowComponent
            condition={
              selectedProvider === "others" &&
              authenticationMethod === "bearerToken"
            }
          >
            <FormTextFieldV2
              control={control}
              fieldName="token"
              label="Token"
              required
              placeholder="Add token"
              size="small"
              fullWidth
            />
          </ShowComponent>

          {/* <ShowComponent
            condition={
              agentType === AGENT_TYPES.CHAT && authenticationMethod === "api_key"
            }
          >
            <AddHeadersSection control={control} />
          </ShowComponent> */}
          <FormSearchSelectFieldControl
            disabled={false}
            label="Select Knowledge Base"
            placeholder="Select"
            size="small"
            control={control}
            fieldName={`knowledgeBase`}
            fullWidth
            // createLabel="Create knowledge base"
            // handleCreateLabel={() => setOpenKnowledgeBase(true)}
            options={knowledgeBaseOptions}
            emptyMessage={"No knowledge base has been added"}
          />
          <Box>
            <FormTextFieldV2
              label="Prompt/Chains"
              required
              control={control}
              fieldName="description"
              placeholder="Describe the agent's purpose and functions"
              helperText={
                lastFetchedAt &&
                `Last synced with ${provider} ${formatDistanceToNow(lastFetchedAt)} ago`
              }
              multiline
              rows={3}
              size="small"
              fullWidth
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                },
              }}
            />
          </Box>
        </Box>
        {/* Contact Number and Pin Code */}
        <ShowComponent
          condition={
            agentType === AGENT_TYPES.VOICE &&
            !isLiveKitProvider(selectedProvider)
          }
        >
          <Box>
            <Grid container spacing={2} alignItems="flex-start">
              <Grid item>
                <FormSearchSelectFieldControl
                  control={control}
                  fieldName={"countryCode"}
                  label="Country Code"
                  required
                  size={"small"}
                  placeholder={"Select country code"}
                  sx={{
                    "& .MuiInputLabel-root": {
                      fontWeight: 500,
                    },
                  }}
                  options={pinCodeOptions.map((pinCodeOption) => {
                    return {
                      label:
                        `${pinCodeOption.label} (+${pinCodeOption.value})`
                          .length > 20
                          ? `${pinCodeOption.label.slice(0, 13)}... (+${pinCodeOption.value})`
                          : `${pinCodeOption.label} (+${pinCodeOption.value})`,
                      value: pinCodeOption.value,
                      component: (
                        <Box
                          sx={{
                            py: 1,
                            pr: 1,
                            display: "flex",
                            flexDirection: "row",
                            width: "100%",
                            alignItems: "center",
                            justifyContent: "space-between",
                          }}
                        >
                          <Box display="flex" alignItems="center" gap={1}>
                            <Image
                              src={pinCodeOption.countryFlag}
                              width="20px"
                              wrapperProps={{
                                style: {
                                  display: "flex",
                                  alignItems: "center",
                                },
                              }}
                            />
                            <Typography
                              variant="body2"
                              maxWidth={"100px"}
                              noWrap
                              textOverflow={"ellipsis"}
                            >
                              {pinCodeOption.label}
                            </Typography>
                          </Box>
                          <Typography
                            variant="body2"
                            fontWeight="fontWeightRegular"
                          >
                            +{pinCodeOption.value}
                          </Typography>
                        </Box>
                      ),
                    };
                  })}
                />
              </Grid>
              <Grid item xs>
                <FormTextFieldV2
                  control={control}
                  label="Contact Number"
                  required
                  fieldName="contactNumber"
                  placeholder="Phone Number"
                  size="small"
                  fullWidth
                  error={!!errors.contactNumber}
                  helperText={errors.contactNumber?.message}
                  sx={{
                    "& .MuiInputLabel-root": {
                      fontWeight: 500,
                    },
                  }}
                  onChange={() => trigger("contactNumber")}
                />
              </Grid>
            </Grid>
          </Box>
        </ShowComponent>

        {/* Language */}
        {/* Inbound Switch */}
        <ShowComponent condition={agentType === AGENT_TYPES.VOICE}>
          <Stack spacing={1}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              border={"1px solid"}
              borderColor={"background.neutral"}
              borderRadius={"8px !important"}
              p={1.5}
              zIndex={999}
            >
              <Box display={"flex"} flexDirection={"column"}>
                <Typography
                  typography="s1"
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  {inbound
                    ? INBOUND_OUTBOUND_COPY.inbound.title
                    : INBOUND_OUTBOUND_COPY.outbound.title}
                </Typography>
                <Typography
                  typography="s2_1"
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  {inbound
                    ? INBOUND_OUTBOUND_COPY.inbound.description
                    : INBOUND_OUTBOUND_COPY.outbound.description}
                </Typography>
              </Box>
              <CustomTooltip
                show={true}
                title={
                  outboundLocked
                    ? "Outbound calls aren't supported for the Others provider. The agent uses your own endpoint, so only inbound is available."
                    : inbound
                      ? INBOUND_OUTBOUND_COPY.inbound.tooltip
                      : INBOUND_OUTBOUND_COPY.outbound.tooltip
                }
                placement="bottom"
                arrow
                size="small"
                type="black"
                slotProps={{
                  tooltip: {
                    sx: {
                      maxWidth: "200px !important",
                    },
                  },
                }}
              >
                <Box>
                  <SwitchField
                    control={control}
                    fieldName="inbound"
                    label=""
                    onChange={() => trigger(["apiKey", "assistantId"])}
                    labelPlacement="end"
                    disabled={outboundLocked}
                  />
                </Box>
              </CustomTooltip>
            </Box>
            {outboundLocked && (
              <Typography
                typography="s2_1"
                fontWeight="fontWeightRegular"
                color="text.secondary"
              >
                Outbound is not supported for the Others provider. This agent
                uses your own endpoint, which we can only receive calls into.
              </Typography>
            )}
          </Stack>
        </ShowComponent>
        <ShowComponent
          condition={
            agentType === AGENT_TYPES.VOICE &&
            selectedProvider !== "others" &&
            !isLiveKitProvider(selectedProvider)
          }
        >
          <Stack>
            <CustomTooltip
              title="You need to add api key and assistant id to enable observability"
              show={!canEnableObservability}
              size="small"
              arrow
              type={undefined}
            >
              <span
                style={{
                  width: "fit-content",
                }}
              >
                <FormCheckboxField
                  control={control}
                  fieldName="observabilityEnabled"
                  label="Enable Observability"
                  labelPlacement="end"
                  helperText={""}
                  defaultValue={true}
                  onChange={() => trigger(["apiKey", "assistantId"])}
                  disabled={!canEnableObservability}
                />
              </span>
            </CustomTooltip>
            <Typography
              typography="s2"
              color="text.disabled"
              sx={{ marginLeft: 1 }}
              fontWeight="500"
            >
              Enable if you want to monitor this agent
            </Typography>
          </Stack>
        </ShowComponent>
      </Box>
      {/* we are temporarily not allowing user to create  Knowledge Base from here due to some cases we will comeback with a solution soon  */}

      {/* <CreateKnowledgeBaseDrawer
        open={openKnowledgeBase}
        onClose={() => setOpenKnowledgeBase(false)}
        setHasData={null}
        refreshGrid={(id) => {
          queryClient.invalidateQueries(["knowledge-base"]);
          setValue("knowledgeBase", id, { shouldDirty: true });
        }}
      /> */}
    </Box>
  );
};

EditAgentDetails.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object,
  setValue: PropTypes.func,
  getValues: PropTypes.func,
  trigger: PropTypes.func,
};

export default EditAgentDetails;
