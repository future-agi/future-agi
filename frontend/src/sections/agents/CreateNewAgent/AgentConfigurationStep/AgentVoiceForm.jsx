import {
  Box,
  Button,
  CircularProgress,
  Grid,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState, useRef, useCallback } from "react";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import {
  AGENT_TYPES,
  AUTH_METHODS_BY_PROVIDER,
  INBOUND_OUTBOUND_COPY,
  VOICE_CHAT_PROVIDERS,
  isLiveKitProvider,
  validateLiveKitCredentials,
} from "../../constants";
import { ShowComponent } from "src/components/show";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import SwitchField from "src/components/Switch/SwitchField";
import AddHeadersSection from "../AddHeadersSection";
import CreateNewAgentCards from "../../CreateNewAgentCards";
import Image from "src/components/image";
import { useFormContext, useWatch } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { spin } from "../../../../animations/animations";
import { pinCodeOptions } from "src/components/agent-definitions/helper";
import { useAuthContext } from "src/auth/hooks";

export default function AgentVoiceForm() {
  const { orgLimit } = useAuthContext();
  const { control, setValue: setFormValue } = useFormContext();
  const { getValues, setValue, clearErrors, trigger, setError } =
    useFormContext();

  const [showSuccess, setShowSuccess] = useState(false);
  const successTimeoutRef = useRef(null);
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
  const agentType = useWatch({
    control,
    name: "agentType",
    defaultValue: getValues("agentType"),
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
  const inbound = useWatch({
    name: "inbound",
    control,
  });

  // LiveKit credential validation
  const livekitUrl = useWatch({ control, name: "livekitUrl" });
  const livekitApiKey = useWatch({ control, name: "livekitApiKey" });
  const livekitApiSecret = useWatch({ control, name: "livekitApiSecret" });
  const livekitAgentName = useWatch({ control, name: "livekitAgentName" });

  const [livekitValidation, setLivekitValidation] = useState("idle");
  const [livekitValidationError, setLivekitValidationError] = useState("");

  // Reset validation when credentials change + auto-validate with debounce
  const autoValidateTimer = useRef(null);
  const prevCredsRef = useRef("");
  const abortControllerRef = useRef(null);

  const runValidation = useCallback(async () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    abortControllerRef.current = new AbortController();

    setLivekitValidation("validating");
    setLivekitValidationError("");
    const result = await validateLiveKitCredentials({
      livekitUrl,
      livekitApiKey,
      livekitApiSecret,
      livekitAgentName,
    });
    // Ignore result if aborted
    if (abortControllerRef.current?.signal.aborted) return;

    if (result.valid) {
      setLivekitValidation("valid");
      setFormValue("_livekitCredentialsValid", true);
    } else {
      setLivekitValidation("invalid");
      setLivekitValidationError(result.error);
      setFormValue("_livekitCredentialsValid", false);
    }
  }, [
    livekitUrl,
    livekitApiKey,
    livekitApiSecret,
    livekitAgentName,
    setFormValue,
  ]);

  useEffect(() => {
    if (!isLiveKitProvider(selectedProvider)) return;

    // Note: we deliberately do NOT rewrite wss:// -> https:// in the form
    // state. The backend normalizes the scheme in three places (validate
    // endpoint, livekit config, bridge connector), so storing the user's
    // typed value as-is is safe. Mutating the input mid-typing causes
    // cursor jumps and looks like the field is broken. [TH-4131]

    const credsKey = `${livekitUrl}|${livekitApiKey}|${livekitApiSecret}|${livekitAgentName}`;
    if (credsKey === prevCredsRef.current) return;
    prevCredsRef.current = credsKey;

    setLivekitValidation("idle");
    setLivekitValidationError("");
    setFormValue("_livekitCredentialsValid", false);

    if (autoValidateTimer.current) clearTimeout(autoValidateTimer.current);
    if (abortControllerRef.current) abortControllerRef.current.abort();

    const allFilled =
      livekitUrl && livekitApiKey && livekitApiSecret && livekitAgentName;
    if (allFilled) {
      autoValidateTimer.current = setTimeout(runValidation, 1500);
    }

    return () => {
      if (autoValidateTimer.current) clearTimeout(autoValidateTimer.current);
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    livekitUrl,
    livekitApiKey,
    livekitApiSecret,
    livekitAgentName,
    selectedProvider,
  ]);

  const { mutate, isPending, status } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) =>
      axios.post(endpoints.agentDefinitions.fetchAssistantFromProvider, data),
    onSuccess: (data) => {
      clearErrors("assistantId");
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
      setShowSuccess(true);
    },
    meta: {
      errorHandled: true,
    },
    onError: () => {
      setError("assistantId", {
        type: "manual",
        message: `Syncing with ${selectedProvider} failed, Recheck the ID `,
      });
    },
  });

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

  const canEnableObservability = Boolean(apiKey && assistantId);
  const keysRequired = inbound === false;
  // "others" provider brings its own endpoint, so outbound has no
  // meaning (nothing to call). Lock the toggle to inbound.
  const outboundLocked = selectedProvider === "others";

  useEffect(() => {
    if (observabilityEnabled && (!apiKey || !assistantId)) {
      setValue("observabilityEnabled", false);
    }
  }, [apiKey, assistantId, observabilityEnabled, setValue]);

  // Cleanup debounce timeout on unmount
  useEffect(() => {
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
      }
    };
  }, []);

  // Auto-hide success message after 2.5 seconds
  useEffect(() => {
    if (showSuccess) {
      if (successTimeoutRef.current) {
        clearTimeout(successTimeoutRef.current);
      }
      successTimeoutRef.current = setTimeout(() => {
        setShowSuccess(false);
      }, 2500);
    }

    return () => {
      if (successTimeoutRef.current) {
        clearTimeout(successTimeoutRef.current);
      }
    };
  }, [showSuccess]);

  const getVapiFetch = () => {
    return (
      <Box>
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
                animation: isPending ? `${spin} 1s linear infinite` : "none",
                width: "16px",
                height: "16px",
              }}
            />
            <Typography typography={"s3"} fontWeight={"fontWeightMedium"}>
              {`Syncing with ${selectedProvider}...`}
            </Typography>
          </Box>
        </ShowComponent>
        <ShowComponent condition={showSuccess && !isPending}>
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
            <Typography typography={"s3"} fontWeight={"fontWeightMedium"}>
              {`Synced with ${selectedProvider}`}
            </Typography>
          </Box>
        </ShowComponent>
      </Box>
    );
  };
  return (
    <>
      <Box display="flex" flexDirection="column" gap={2}>
        <FormSearchSelectFieldControl
          control={control}
          fieldName="provider"
          label="Voice/Chat Provider"
          placeholder="Select the provider powering your agent"
          size="small"
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
          required
          options={VOICE_CHAT_PROVIDERS}
          onChange={(e) => {
            const value = e.target.value;
            const mainProviders = [
              "vapi",
              "retell",
              "elevenlabs",
              "livekit",
              "livekit_bridge",
            ];
            // "bland" is intentionally excluded: its raw-authorization key is
            // distinct from these Bearer providers, so switching into or out of
            // Bland must clear the key rather than carry a stale one.

            // Clear authenticationMethod only if switching to or from "others"
            const isPrevMain = mainProviders.includes(selectedProvider);
            const isNextMain = mainProviders.includes(value);

            if (value !== selectedProvider) {
              if (isPrevMain && isNextMain) {
                // between vapi/retell/elevenlabs → keep authenticationMethod
              } else {
                setValue("authenticationMethod", "");
                setValue("apiKey", "");
                clearErrors("apiKey");
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
        {/* <FormTextFieldV2
              control={control}
              fieldName="apiEndpoint"
              placeholder="Enter API endpoint"
              label="API Endpoint"
              fullWidth
              sx={{
                "& .MuiInputLabel-root": {
                  fontWeight: 500,
                  },
                  }}
                  size="small"
                  /> */}
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
            placeholder="Add authentication method to communicate with the provider"
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
          <FormTextFieldV2
            control={control}
            fieldName="apiKey"
            label="Provider API Key"
            placeholder="Enter your provider’s API key to authenticate the agent"
            required={observabilityEnabled || keysRequired}
            size="small"
            fullWidth
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
          <FormTextFieldV2
            control={control}
            fieldName="assistantId"
            placeholder="Enter the Assistant ID to sync from your provider"
            label="Assistant ID"
            required={observabilityEnabled || keysRequired}
            disabled={isPending}
            fullWidth
            sx={{
              "& .MuiInputLabel-root": {
                fontWeight: 500,
              },
            }}
            size="small"
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
            helperText={getVapiFetch()}
          />
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="flex-start"
            border={"1px solid"}
            borderColor={"divider"}
            borderRadius={"8px !important"}
            zIndex={999}
          >
            <Box display={"flex"} flexDirection={"column"} p={1.5}>
              <Typography
                typography="s1"
                fontWeight={"fontWeightMedium"}
                color={"text.primary"}
              >
                Enable observability (Requires API key)
              </Typography>
              <Box display="flex" alignItems="center" gap="4px">
                <Typography
                  typography="s2_1"
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  Turn on observability to track calls, monitor logs, and debug
                  agent behaviour
                </Typography>
                <Link
                  href="https://docs.futureagi.com/docs/observe"
                  color="blue.500"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                  fontSize="13px"
                  sx={{ textDecoration: "underline" }}
                >
                  Learn more
                </Link>
              </Box>
            </Box>

            <Box p={0.5}>
              <CustomTooltip
                title="Add an API key and Assistant ID to enable observability"
                show={!canEnableObservability}
                size="small"
                arrow
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
                    fieldName="observabilityEnabled"
                    label=""
                    labelPlacement="end"
                    disabled={!canEnableObservability}
                    onChange={() => {
                      trigger(["apiKey", "assistantId"]);
                    }}
                  />
                </Box>
              </CustomTooltip>
            </Box>
          </Box>
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
            helperText="Paste your LiveKit project URL — wss:// works too."
          />
          <FormTextFieldV2
            control={control}
            fieldName="livekitApiKey"
            placeholder="APIxxxxxxxxxxxxxxxx"
            label="LiveKit API Key"
            required
            fullWidth
            size="small"
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
          />
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            <FormTextFieldV2
              control={control}
              fieldName="livekitAgentName"
              placeholder="e.g. test-agent"
              label="Agent Name"
              required
              fullWidth
              size="small"
            />
            <Button
              variant="outlined"
              size="small"
              onClick={runValidation}
              disabled={
                livekitValidation === "validating" ||
                !livekitUrl ||
                !livekitApiKey ||
                !livekitApiSecret
              }
              sx={{
                textTransform: "none",
                fontWeight: "fontWeightMedium",
                fontSize: 13,
                borderColor: "action.selected",
                color: "text.primary",
                height: 40,
                minWidth: 160,
                mt: 0.25,
                px: 2.5,
                borderRadius: 0.5,
                flexShrink: 0,
              }}
            >
              Test Connection
            </Button>
          </Stack>
          {/* Validation status */}
          {livekitValidation === "validating" && (
            <Box display="flex" alignItems="center" gap={0.75}>
              <CircularProgress size={14} />
              <Typography typography="s2" color="text.secondary">
                Testing connection...
              </Typography>
            </Box>
          )}
          {livekitValidation === "valid" && (
            <Box display="flex" alignItems="center" gap={0.5}>
              <SvgColor
                src="/assets/icons/ic_success_fill.svg"
                sx={{ width: 16, height: 16, color: "green.600" }}
              />
              <Typography
                typography="s2"
                fontWeight="fontWeightMedium"
                color="green.600"
              >
                Connection successful
              </Typography>
            </Box>
          )}
          {livekitValidation === "invalid" && (
            <Box display="flex" alignItems="center" gap={0.5}>
              <SvgColor
                src="/assets/icons/ic_failed.svg"
                sx={{ width: 16, height: 16, color: "red.600" }}
              />
              <Typography
                typography="s2"
                fontWeight="fontWeightMedium"
                color="red.600"
              >
                {livekitValidationError}
              </Typography>
            </Box>
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
        <ShowComponent
          condition={
            agentType === AGENT_TYPES.CHAT && authenticationMethod === "api_key"
          }
        >
          <AddHeadersSection control={control} />
        </ShowComponent>
      </Box>
      <ShowComponent condition={!isLiveKitProvider(selectedProvider)}>
        <CreateNewAgentCards
          title={"Contact Information"}
          subtitle={
            "Calls will be routed to(inbound)/from(Outbound) this phone number. Ensure it is correct."
          }
        >
          <Typography
            typography="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Use a valid phone number capable of receiving calls
          </Typography>
          <Box display="flex" flexDirection="column" gap={3}>
            <Grid container spacing={2} alignItems="flex-start">
              <Grid item xs={3.5}>
                <FormSearchSelectFieldControl
                  control={control}
                  fullWidth
                  fieldName="countryCode"
                  label="Country Code"
                  required
                  size="small"
                  placeholder="+1"
                  options={pinCodeOptions.map((pinCodeOption) => ({
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
                  }))}
                />
              </Grid>
              <Grid item xs={8.5}>
                <FormTextFieldV2
                  control={control}
                  label="Contact Number"
                  required
                  type="number"
                  fieldName="contactNumber"
                  placeholder="Contact number"
                  size="small"
                  fullWidth
                  sx={{
                    "& .MuiInputLabel-root": {
                      fontWeight: 500,
                    },
                  }}
                />
              </Grid>
            </Grid>

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
                  color={"text.secondary"}
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
                sx={{ mt: 1 }}
              >
                Outbound is not supported for the Others provider. This agent
                uses your own endpoint, which we can only receive calls into.
              </Typography>
            )}
          </Box>
        </CreateNewAgentCards>
      </ShowComponent>
      {/* Inbound/Outbound toggle for LiveKit (no phone number needed) */}
      <ShowComponent condition={isLiveKitProvider(selectedProvider)}>
        <Box
          display="flex"
          justifyContent="space-between"
          alignItems="center"
          border={"1px solid"}
          borderColor={"background.neutral"}
          borderRadius={"8px !important"}
          p={1.5}
          mt={2}
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
              inbound
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
                labelPlacement="end"
              />
            </Box>
          </CustomTooltip>
        </Box>
      </ShowComponent>
    </>
  );
}

AgentVoiceForm.propTypes = {};
