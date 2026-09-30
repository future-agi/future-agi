import React, { useMemo, useRef, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Drawer,
  FormControlLabel,
  IconButton,
  InputAdornment,
  Radio,
  RadioGroup,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import { useForm, useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { customModelValidation } from "./validation";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import ConfigureCustomModel from "./ConfigureCustomModel";
import APIKeyForm from "src/components/custom-model-dropdown/APIKeyForm";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import { ShowComponent } from "src/components/show";
import {
  AZURE_ENDPOINT_TYPES,
  AzureEndpointOptions,
  CustomModalOptions,
  defaultValues,
  FIELD_NAMES,
  MODEL_PROVIDERS,
  RADIO_VALUES,
  TABS,
} from "./constants";
import {
  createClearOnFocus,
  createHandleApiBaseChange,
  createHandleApiVersionChange,
  getModelFields,
  getAwsCredentialFields,
  getOpenAiKeyFields,
  getVertexKeyFields,
  getAzureKeyFields,
  getCustomModelFields,
  inferAzureEndpointType,
} from "./helper";

import logger from "src/utils/logger";
import { ConfirmDialog } from "src/components/custom-dialog";
import Image from "src/components/image";
import { LOGO_WITH_BLACK_BACKGROUND } from "src/components/custom-model-dropdown/common";

const commonAttribute = {
  fullWidth: true,
  size: "small",
};

const AddCustomModalForm = ({
  onClose,
  handleClose,
  onRefresh = () => {},
  data,
  edit,
  control,
  handleSubmit,
  modelProvider,
  setValue,
  getValues,
  currentTab,
  setCurrentTab,
  isValid,
}) => {
  const [radioValue, setRadioValue] = useState(RADIO_VALUES.CUSTOM_PROVIDER);
  const clearedFieldsRef = useRef({});
  const theme = useTheme();
  const queryClient = useQueryClient();

  const handleRadioChange = (e) => {
    const newValue = e.target.value;
    setRadioValue(newValue);

    if (newValue === RADIO_VALUES.CONFIGURE_CUSTOM_MODEL) {
      setValue("modelProvider", "custom");
      setValue("customConfiguration", [
        { key: "api_base", value: "", disabled: true },
        { key: "x_api_key", value: "", disabled: false },
      ]);
    } else {
      setValue("modelProvider", "");
      setValue("customConfiguration", []);
    }

    // Reset other provider fields
    resetProviderFields();
  };

  const { mutate, isPending: loading } = useMutation({
    /**
     *
     * @param {Object} newPayload
     * @returns
     */
    mutationFn: async (newPayload) => {
      if (edit) {
        const payload = { ...newPayload, id: data.id };
        return axios.patch(
          endpoints.settings.customModal.editCustomModel,
          payload,
        );
      } else {
        return axios.post(
          endpoints.settings.customModal.createCustomModal,
          newPayload,
        );
      }
    },
    onSuccess: (data) => {
      const message = data?.data?.message;
      queryClient.invalidateQueries({ queryKey: ["customModals"] });
      enqueueSnackbar(
        message || `Model has been ${edit ? "updated" : "added"}`,
        { variant: "success" },
      );
      onRefresh();
      onClose();
    },
  });

  const onSubmit = (data) => {
    logger.debug(data);
    let configJson = {};

    if (data.modelProvider === MODEL_PROVIDERS.CUSTOM) {
      configJson.headers = {};
      data?.customConfiguration?.forEach((element) => {
        configJson.headers[element.key] = element.value;
      });
      configJson.api_base = data.apiBase;
      configJson.custom_provider = true;
      delete data.apiBase;
    }

    if (data.modelProvider === MODEL_PROVIDERS.VERTEX_AI) {
      try {
        configJson =
          typeof data[FIELD_NAMES.VERTEX_CREDENTIAL_JSON] === "string"
            ? JSON.parse(data[FIELD_NAMES.VERTEX_CREDENTIAL_JSON])
            : data[FIELD_NAMES.VERTEX_CREDENTIAL_JSON];
      } catch (error) {
        configJson = data[FIELD_NAMES.VERTEX_CREDENTIAL_JSON];
      }
      if (data.vertexLocation) {
        configJson.location = data.vertexLocation;
      }
    }

    if (
      data.modelProvider === MODEL_PROVIDERS.BEDROCK ||
      data.modelProvider === MODEL_PROVIDERS.SAGEMAKER
    ) {
      if (currentTab === TABS.JSON && data[FIELD_NAMES.AWS_CREDENTIALS_JSON]) {
        try {
          configJson =
            typeof data[FIELD_NAMES.AWS_CREDENTIALS_JSON] === "string"
              ? JSON.parse(data[FIELD_NAMES.AWS_CREDENTIALS_JSON])
              : data[FIELD_NAMES.AWS_CREDENTIALS_JSON];
        } catch (error) {
          configJson = data[FIELD_NAMES.AWS_CREDENTIALS_JSON];
        }
      } else {
        configJson.aws_access_key_id = data.awsAccessKeyId;
        configJson.aws_secret_access_key = data.awsSecretAccessKey;
        configJson.aws_region_name = data.awsRegionName;
      }
    }

    if (data.modelProvider === MODEL_PROVIDERS.AZURE) {
      if (
        currentTab === TABS.JSON &&
        data[FIELD_NAMES.AZURE_CREDENTIALS_JSON]
      ) {
        try {
          configJson =
            typeof data[FIELD_NAMES.AZURE_CREDENTIALS_JSON] === "string"
              ? JSON.parse(data[FIELD_NAMES.AZURE_CREDENTIALS_JSON])
              : data[FIELD_NAMES.AZURE_CREDENTIALS_JSON];
        } catch (error) {
          configJson = data[FIELD_NAMES.AZURE_CREDENTIALS_JSON];
        }
      } else {
        configJson.api_base = data.azureApiBase;
        if (data.azureApiVersion) {
          configJson.api_version = data.azureApiVersion;
        }
        configJson.api_key = data.apiKey;
        configJson.azure_endpoint_type = data.azureEndpointType;
      }
    }

    if (data.modelProvider === MODEL_PROVIDERS.OPENAI) {
      if (
        currentTab === TABS.JSON &&
        data[FIELD_NAMES.OPENAI_CREDENTIALS_JSON]
      ) {
        try {
          configJson =
            typeof data[FIELD_NAMES.OPENAI_CREDENTIALS_JSON] === "string"
              ? JSON.parse(data[FIELD_NAMES.OPENAI_CREDENTIALS_JSON])
              : data[FIELD_NAMES.OPENAI_CREDENTIALS_JSON];
        } catch (error) {
          configJson = { key: data[FIELD_NAMES.OPENAI_CREDENTIALS_JSON] };
        }
      } else {
        configJson.key = data.key;
        configJson.api_base = data.apiBaseUrl;
      }
    }

    const payload = {
      model_name: data.modelName,
      model_provider: data.modelProvider,
      input_token_cost: data.inputTokenCost,
      output_token_cost: data.outputTokenCost,
      config_json: configJson,
    };

    trackEvent(Events.customModelAdded, {
      [PropertyName.formFields]: {
        payload,
      },
    });

    // console.log("Formatted Data:", payload);
    mutate(payload);
  };

  const clearOnFocus = useMemo(
    () => createClearOnFocus(setValue, clearedFieldsRef),
    [setValue],
  );

  // Memoize handleApiBaseChange
  const handleApiBaseChange = useMemo(
    () => createHandleApiBaseChange({ modelProvider, setValue, getValues }),
    [modelProvider, setValue, getValues],
  );

  const handleApiVersionChange = useMemo(
    () => createHandleApiVersionChange({ modelProvider, setValue, getValues }),
    [modelProvider, setValue, getValues],
  );

  const resetProviderFields = (newProvider) => {
    const fieldsToReset = [
      "key",
      "apiBaseUrl",
      "awsAccessKeyId",
      "awsSecretAccessKey",
      "awsRegionName",
      "awsCredentialsJson",
      "vertexCredentialJson",
      "vertexLocation",
      "azureApiBase",
      "azureApiVersion",
      "azureEndpointType",
      "apiKey",
      "azureCredentialJson",
      "openAiCredentialJson",
      "customConfiguration",
    ];

    // Reset all provider fields
    fieldsToReset.forEach((field) => {
      if (field === "customConfiguration") {
        setValue(field, []);
        return;
      }
      if (field === "azureEndpointType") {
        setValue(field, AZURE_ENDPOINT_TYPES.FOUNDRY);
        return;
      }
      setValue(field, "");
    });

    // Reset UI state
    setCurrentTab("Form");
    clearedFieldsRef.current = {};

    // Set provider-specific configurations
    if (newProvider === "custom") {
      setValue("customConfiguration", [
        { key: "api_base", value: "", disabled: true },
        { key: "x_api_key", value: "", disabled: false },
      ]);
    }
  };

  const modelFields = useMemo(() => getModelFields(), []);

  const customModelFields = useMemo(() => getCustomModelFields(), []);

  const awsCredentialFields = useMemo(
    () => getAwsCredentialFields(clearOnFocus),
    [clearOnFocus],
  );

  const openAiKeyFields = useMemo(
    () => getOpenAiKeyFields(clearOnFocus),
    [clearOnFocus],
  );

  const vertexKeyFields = useMemo(
    () => getVertexKeyFields(clearOnFocus),
    [clearOnFocus],
  );

  const azureEndpointType = useWatch({ control, name: "azureEndpointType" });

  const azureKeyFields = useMemo(() => {
    const fields = getAzureKeyFields(
      clearOnFocus,
      handleApiBaseChange,
      handleApiVersionChange,
    );
    if (azureEndpointType === AZURE_ENDPOINT_TYPES.FOUNDRY) {
      return fields.filter((f) => f.fieldName !== "azureApiVersion");
    }
    return fields;
  }, [
    clearOnFocus,
    handleApiBaseChange,
    handleApiVersionChange,
    azureEndpointType,
  ]);

  const rootContainerStyles = useMemo(
    () => ({
      display: "flex",
      height: "100vh",
    }),
    [],
  );

  const formContainerStyles = useMemo(
    () => ({
      padding: theme.spacing(2.5),
      display: "flex",
      flexDirection: "column",
      gap: theme.spacing(1),
      width: "570px",
    }),
    [theme],
  );

  const headerSectionStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      marginBottom: theme.spacing(1.25),
    }),
    [theme],
  );

  const closeButtonStyles = useMemo(
    () => ({
      position: "absolute",
      top: theme.spacing(1.5),
      right: theme.spacing(1.5),
      color: "text.primary",
    }),
    [theme],
  );

  const dynamicFieldsWrapperStyles = useMemo(
    () => ({
      flex: 1,
      display: "flex",
      flexDirection: "column",
      gap: theme.spacing(1.875),
    }),
    [theme],
  );

  const tabWrapperStyles = useMemo(
    () => ({
      height: "38px",
      marginBottom: 0,
      alignSelf: "flex-start",
    }),
    [],
  );

  const tabStyles = useMemo(
    () => ({
      paddingTop: theme.spacing(0.5),
    }),
    [theme],
  );

  const buttonGroupStyles = useMemo(
    () => ({
      display: "flex",
      gap: theme.spacing(1.875),
    }),
    [theme],
  );

  return (
    <Box sx={rootContainerStyles}>
      <Box
        sx={formContainerStyles}
        component="form"
        onSubmit={(e) => {
          e.preventDefault();
          e.stopPropagation();
          handleSubmit(onSubmit)();
        }}
      >
        <Box sx={headerSectionStyles}>
          <Typography fontWeight={700} color="text.primary">
            {edit ? "Update" : "Add"} Model
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            fontWeight={"fontweightRegular"}
          >
            Add your own custom model to Future AGI
          </Typography>
          <IconButton
            onClick={handleClose}
            sx={closeButtonStyles}
            aria-label="Close custom model modal"
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Box>
        <Stack direction="row" spacing={2}>
          <RadioGroup
            row
            name="modelType"
            value={radioValue}
            onChange={handleRadioChange}
            aria-label="Model type selection"
          >
            <FormControlLabel
              value={RADIO_VALUES.CUSTOM_PROVIDER}
              control={<Radio />}
              label="From model Provider"
            />
            <FormControlLabel
              value={RADIO_VALUES.CONFIGURE_CUSTOM_MODEL}
              control={<Radio />}
              label="Configure Custom Model"
            />
          </RadioGroup>
        </Stack>
        <Box sx={dynamicFieldsWrapperStyles}>
          <ShowComponent
            condition={radioValue === RADIO_VALUES.CUSTOM_PROVIDER}
          >
            <Box sx={dynamicFieldsWrapperStyles}>
              <FormSearchSelectFieldControl
                {...commonAttribute}
                control={control}
                fieldName="modelProvider"
                options={CustomModalOptions.map((option) => ({
                  ...option,
                  component: (
                    <Box sx={{ padding: (theme) => theme.spacing(0.75, 1) }}>
                      <Box
                        display="flex"
                        flexDirection="row"
                        alignItems="center"
                        gap="8px"
                      >
                        {!!option.logo && (
                          <Image
                            src={option.logo}
                            alt={option.label}
                            width={24}
                            height={24}
                            disableThemeFilter={
                              !LOGO_WITH_BLACK_BACKGROUND.includes(
                                option?.value?.toLowerCase(),
                              )
                            }
                            style={{ objectFit: "contain" }}
                          />
                        )}
                        <Typography
                          typography="s1"
                          fontWeight="fontWeightMedium"
                          color="text.primary"
                        >
                          {option.label}
                        </Typography>
                      </Box>
                    </Box>
                  ),
                }))}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      {(() => {
                        const currentValue =
                          control._formValues?.modelProvider ||
                          control._defaultValues?.modelProvider;
                        const selectedOption = CustomModalOptions.find(
                          (opt) => opt.value === currentValue,
                        );

                        return selectedOption?.logo?.trim() ? (
                          <Image
                            src={selectedOption.logo}
                            alt={selectedOption.label}
                            width={24}
                            height={24}
                            disableThemeFilter={
                              !LOGO_WITH_BLACK_BACKGROUND.includes(
                                selectedOption?.value?.toLowerCase(),
                              )
                            }
                            style={{ objectFit: "contain" }}
                          />
                        ) : null;
                      })()}
                    </InputAdornment>
                  ),
                }}
                label="Model Provider"
                required
                placeholder="Choose Model Provider"
                onChange={(e) => {
                  if (e.target.value === MODEL_PROVIDERS.CUSTOM) {
                    setValue("customConfiguration", [
                      { key: "api_base", value: "", disabled: true },
                      { key: "x_api_key", value: "", disabled: false },
                    ]);
                  } else {
                    setValue("customConfiguration", []);
                  }
                  resetProviderFields(modelProvider);
                  trackEvent(Events.modelProviderAccessed);
                }}
                disabled={edit}
              />

              <ShowComponent condition={!!modelProvider}>
                <React.Fragment>
                  {modelFields.map((field) => (
                    <APIKeyForm
                      key={field.fieldName}
                      control={control}
                      fieldName={field.fieldName}
                      label={field.label}
                      placeholder={field.placeholder}
                      type={field.type}
                      inputProps={field.inputProps}
                      onChange={field.onChange}
                      onFocusInput={clearOnFocus}
                      onSubmit={() => {}}
                      handleSubmit={() => {}}
                      required={field.required}
                      fieldType={field?.fieldType}
                      {...commonAttribute}
                    />
                  ))}
                  <ShowComponent
                    condition={modelProvider !== MODEL_PROVIDERS.VERTEX_AI}
                  >
                    <TabWrapper sx={tabWrapperStyles}>
                      <CustomTabs
                        textColor="primary"
                        value={currentTab}
                        onChange={(e, value) => setCurrentTab(value)}
                        TabIndicatorProps={{
                          style: {
                            backgroundColor: theme.palette.primary.main,
                            opacity: 0.08,
                            height: "100%",
                            borderRadius: "4px",
                          },
                        }}
                      >
                        <CustomTab
                          label={TABS.FORM}
                          value={TABS.FORM}
                          disabled={false}
                          sx={tabStyles}
                        />

                        <CustomTab
                          label={TABS.JSON}
                          value={TABS.JSON}
                          sx={tabStyles}
                        />
                      </CustomTabs>
                    </TabWrapper>
                  </ShowComponent>

                  <ShowComponent
                    condition={[
                      MODEL_PROVIDERS.BEDROCK,
                      MODEL_PROVIDERS.SAGEMAKER,
                    ].includes(modelProvider)}
                  >
                    <ShowComponent condition={currentTab === TABS.JSON}>
                      <APIKeyForm
                        control={control}
                        fieldName={FIELD_NAMES.AWS_CREDENTIALS_JSON}
                        label="AWS Credentials"
                        placeholder="Enter AWS credentials in JSON format"
                        showJsonField={true}
                        isJsonKey={true}
                        onFocusInput={clearOnFocus}
                        {...commonAttribute}
                      />
                    </ShowComponent>
                    <ShowComponent condition={currentTab !== TABS.JSON}>
                      <>
                        {awsCredentialFields.map((field) => (
                          <APIKeyForm
                            key={field.fieldName}
                            control={control}
                            fieldName={field.fieldName}
                            label={field.label}
                            placeholder={field.placeholder}
                            onChange={field.onChange}
                            onFocusInput={field.onFocus}
                            showJsonField={true}
                            isJsonKey={false}
                            {...commonAttribute}
                          />
                        ))}
                      </>
                    </ShowComponent>
                  </ShowComponent>
                  <ShowComponent
                    condition={modelProvider === MODEL_PROVIDERS.VERTEX_AI}
                  >
                    <>
                      {vertexKeyFields.map((field) => (
                        <APIKeyForm
                          key={field.fieldName}
                          control={control}
                          fieldName={field.fieldName}
                          label={field.label}
                          placeholder={field.placeholder}
                          isJsonKey={field.isJsonKey}
                          onFocusInput={field.onFocus}
                          showJsonField={true}
                          readOnly={false}
                          {...commonAttribute}
                        />
                      ))}
                    </>
                  </ShowComponent>
                  <ShowComponent
                    condition={modelProvider === MODEL_PROVIDERS.AZURE}
                  >
                    <ShowComponent condition={currentTab === TABS.JSON}>
                      <APIKeyForm
                        control={control}
                        fieldName={FIELD_NAMES.AZURE_CREDENTIALS_JSON}
                        label="Azure Credentials"
                        placeholder="Enter Azure credentials in JSON format"
                        isJsonKey={true}
                        showJsonField={true}
                        readOnly={false}
                        onFocusInput={clearOnFocus}
                        {...commonAttribute}
                      />
                    </ShowComponent>
                    <ShowComponent condition={currentTab !== TABS.JSON}>
                      <>
                        <FormSearchSelectFieldControl
                          {...commonAttribute}
                          control={control}
                          fieldName="azureEndpointType"
                          options={AzureEndpointOptions}
                          label="Azure Endpoint Type"
                          required
                          placeholder="Choose endpoint type"
                        />
                        {azureKeyFields.map((field) => (
                          <APIKeyForm
                            key={field.fieldName}
                            control={control}
                            fieldName={field.fieldName}
                            label={field.label}
                            placeholder={field.placeholder}
                            onChange={field.onChange}
                            onFocusInput={field.onFocus}
                            {...commonAttribute}
                          />
                        ))}
                      </>
                    </ShowComponent>
                  </ShowComponent>

                  <ShowComponent
                    condition={modelProvider === MODEL_PROVIDERS.OPENAI}
                  >
                    <ShowComponent condition={currentTab === TABS.JSON}>
                      <APIKeyForm
                        control={control}
                        fieldName={FIELD_NAMES.OPENAI_CREDENTIALS_JSON}
                        label={"OpenAI Credentials"}
                        placeholder="Enter OpenAI credentials in JSON format"
                        isJsonKey={true}
                        showJsonField={true}
                        readOnly={false}
                        onFocusInput={clearOnFocus}
                        {...commonAttribute}
                      />
                    </ShowComponent>
                    <ShowComponent condition={currentTab !== TABS.JSON}>
                      <>
                        {openAiKeyFields.map((field) => (
                          <APIKeyForm
                            key={field.fieldName}
                            control={control}
                            fieldName={field.fieldName}
                            label={field.label}
                            placeholder={field.placeholder}
                            onFocusInput={field.onFocus}
                            onChange={field.onChange}
                            {...commonAttribute}
                          />
                        ))}
                      </>
                    </ShowComponent>
                  </ShowComponent>
                </React.Fragment>
              </ShowComponent>
            </Box>
          </ShowComponent>
          <ShowComponent
            condition={radioValue === RADIO_VALUES.CONFIGURE_CUSTOM_MODEL}
          >
            {customModelFields.map((field) => (
              <APIKeyForm
                key={field.fieldName}
                control={control}
                fieldName={field.fieldName}
                label={field.label}
                placeholder={field.placeholder}
                helperText={field.helperText}
                type={field.type}
                inputProps={field.inputProps}
                onChange={field.onChange}
                onFocusInput={clearOnFocus}
                required={field.required}
                fieldType={field?.fieldType}
                {...commonAttribute}
              />
            ))}
            <ConfigureCustomModel
              control={control}
              fieldName="customConfiguration"
              commonAttribute={commonAttribute}
              clearOnFocus={clearOnFocus}
            />
          </ShowComponent>
        </Box>
        <Box sx={buttonGroupStyles}>
          <LoadingButton
            fullWidth
            type="button"
            size="small"
            variant="outlined"
            onClick={handleClose}
            aria-label="Cancel model submission"
          >
            Cancel
          </LoadingButton>
          <LoadingButton
            variant="contained"
            color="primary"
            type="submit"
            fullWidth
            size="small"
            loading={loading}
            aria-label={edit ? "Update custom model" : "Add custom model"}
            disabled={!isValid}
          >
            {edit ? "Update" : "Add"} Custom model
          </LoadingButton>
        </Box>
      </Box>
    </Box>
  );
};

const AddCustomModal = ({ open, onClose, onRefresh, data, edit }) => {
  const [currentTab, setCurrentTab] = useState(TABS.FORM);
  const {
    control,
    handleSubmit,
    watch,
    setValue,
    reset,
    getValues,
    formState: { errors, isValid, isDirty },
  } = useForm({
    mode: "onChange",
    defaultValues: defaultValues,
    resolver: zodResolver(customModelValidation(currentTab)),
  });

  const theme = useTheme();
  const modelProvider = watch("modelProvider");
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
  const { data: modelData, isFetching } = useQuery({
    queryKey: ["get-single-custom-model", data?.id],
    queryFn: () =>
      axios.get(endpoints.settings.customModal.editCustomModel, {
        params: { id: data?.id },
      }),
    select: (data) => data.data?.result,
    enabled: !!data?.id,
    // staleTime: 1000,
  });

  const handleClose = () => {
    if (isDirty) {
      setConfirmDialogOpen(true);
    } else {
      resetFormAndClose(); // safe close if no changes
    }
  };

  const resetFormAndClose = () => {
    setValue("modelProvider", "");
    reset();
    onClose();
  };

  useMemo(() => {
    if (!isFetching && modelData) {
      Object.keys(modelData).forEach((key) => {
        if (key !== "configJson") {
          setValue(key, modelData[key]?.toString());
        } else {
          if (modelData.modelProvider === MODEL_PROVIDERS.CUSTOM) {
            setValue(
              "customConfiguration",
              Object.entries(modelData?.configJson)?.map(([key, value]) => ({
                key: key,
                value: value,
                disabled: key === "api_base",
              })),
            );
          }
          if (modelData.modelProvider === MODEL_PROVIDERS.VERTEX_AI) {
            const vertexConfig = modelData[key];
            if (vertexConfig?.location) {
              setValue("vertexLocation", vertexConfig.location);
              const { location, ...creds } = vertexConfig;
              setValue("vertexCredentialJson", JSON.stringify(creds, null, 2));
            } else {
              setValue(
                "vertexCredentialJson",
                JSON.stringify(vertexConfig, null, 2),
              );
            }
          }
          if (
            [
              MODEL_PROVIDERS.BEDROCK,
              MODEL_PROVIDERS.SAGEMAKER,
              MODEL_PROVIDERS.AZURE,
            ].includes(modelData.modelProvider)
          ) {
            Object.keys(modelData.configJson).forEach((configKey) => {
              setValue(configKey, modelData?.configJson?.[configKey]);
            });
            // For Azure, infer endpoint type if not stored (backward compatibility)
            if (
              modelData.modelProvider === MODEL_PROVIDERS.AZURE &&
              !modelData.configJson?.azureEndpointType &&
              modelData.configJson?.apiBase
            ) {
              const inferred =
                inferAzureEndpointType(modelData.configJson.apiBase) ||
                "foundry";
              setValue("azureEndpointType", inferred);
            }
          }
        }
      });
    } else {
      Object.entries(defaultValues).forEach(([key, value]) => {
        setValue(key, value);
      });
    }
  }, [modelData, isFetching, setValue]);

  const drawerPaperStyles = useMemo(
    () => ({
      height: "100vh",
      width: "570px",
      position: "flex",
      zIndex: 1000,
      display: "flex",
      flexDirection: "column",
    }),
    [],
  );

  const backdropStyles = useMemo(
    () => ({
      backgroundColor: "transparent",
    }),
    [],
  );

  const drawerBoxStyles = useMemo(
    () => ({
      backgroundColor: theme.palette.background.paper,
    }),
    [theme],
  );

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: drawerPaperStyles,
      }}
      ModalProps={{
        BackdropProps: {
          style: backdropStyles,
        },
      }}
    >
      <Box sx={drawerBoxStyles}>
        <AddCustomModalForm
          onClose={onClose}
          handleClose={handleClose}
          onRefresh={onRefresh}
          edit={edit}
          data={data}
          control={control}
          handleSubmit={handleSubmit}
          modelProvider={modelProvider}
          setValue={setValue}
          currentTab={currentTab}
          setCurrentTab={setCurrentTab}
          errors={errors}
          getValues={getValues}
          isValid={isValid}
        />
      </Box>
      <ConfirmDialog
        open={confirmDialogOpen}
        onClose={() => setConfirmDialogOpen(false)}
        title="Discard changes?"
        content="You have unsaved changes. Are you sure you want to close this form?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            sx={{ paddingX: "24px" }}
            onClick={() => {
              setConfirmDialogOpen(false);
              resetFormAndClose();
            }}
          >
            Confirm
          </Button>
        }
      />
    </Drawer>
  );
};

export default AddCustomModal;

AddCustomModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onRefresh: PropTypes.func,
  data: PropTypes.object,
  edit: PropTypes.bool,
};

AddCustomModalForm.propTypes = {
  onClose: PropTypes.func,
  handleClose: PropTypes.func,
  onRefresh: PropTypes.func,
  data: PropTypes.object,
  edit: PropTypes.bool,
  control: PropTypes.any,
  handleSubmit: PropTypes.func,
  modelProvider: PropTypes.string,
  setValue: PropTypes.func,
  errors: PropTypes.object,
  currentTab: PropTypes.string,
  setCurrentTab: PropTypes.func,
  getValues: PropTypes.func,
  isValid: PropTypes.bool,
};
