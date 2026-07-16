import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ShowComponent } from "src/components/show";
import Iconify from "../../../components/iconify";
import HeadingAndSubHeading from "../../../components/HeadingAndSubheading/HeadingAndSubheading";
import { Evals_Docs_mapping } from "src/sections/evals/constant";
import { copyToClipboard } from "src/utils/utils";
import SvgColor from "../../../components/svg-color";
import TooltipForEvals from "./TooltipForEvalPopover";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "../../../components/FromSearchSelectField";
import { paths } from "src/routes/paths";
import FieldSelection from "src/sections/develop-detail/Common/FieldSelection";
import { useGetJsonColumnSchema } from "src/api/develop/develop-detail";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { FormCheckboxField } from "../../../components/FormCheckboxField";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { LoadingButton } from "@mui/lab";
import { ADD_AND_RUN_BUTTON_MAPPER, ADD_BUTTON_MAPPER } from "./common";
import { useWatch } from "react-hook-form";
import ConfigRenderer from "./ConfigRenderer";

const requiredRunPrompt = [
  "dataset",
  "run-experiment",
  "run-optimization",
  "experiment",
];

const inferConfigType = (value) => {
  if (value && typeof value === "object" && "type" in value) {
    return value.type;
  }
  if (Array.isArray(value)) {
    return "list";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? "integer" : "string";
  }
  if (typeof value === "boolean") {
    return "boolean";
  }
  if (value && typeof value === "object") {
    return "dict";
  }
  return "string";
};

export default function EvaluationMappingFormContent({
  hideBackButtons,
  isSampleFutureAgiBuilt,
  setCurrentTab,
  setVisibleSection,
  selectedEval,
  onBack,
  hideTitle,
  onClose,
  isEditMode,
  evalsData,
  evalConfig,
  members,
  filteredVisibleItems,
  handleEvalDelete,
  setShowAll,
  showAll,
  control,
  disabledName,
  isViewMode,
  isFutureagiBuilt,
  alwaysShowModel = false,
  modelsToShow,
  hideModel,
  modeHelpMessage,
  hideKnowledgeBase,
  knowledgeBaseOptions,
  isEvalConfig,
  filteredRequiredKeys,
  filteredColumns,
  datasetId,
  _module,
  transformedOptionalKeys,
  hideFieldColumns,
  groupedRequiredKeys,
  groupFunctionParamsRequirements,
  mappingError,
  isPending,
  onFormSave,
  handleSubmit,
  evalConfiguration,
  role,
  saveButtonTitle,
  showTest,
  onTest,
  isTesting,
  testLabel,
  showAdd,
  setIsDirty,
  onSubmit,
  saveLabel,
  runLabel,
  showDefaultButton,
  saveGroup,
  removedEvals,
  formState,
  hideGroupHeader = false,
  hideAddGroupButton = false,
}) {
  const [_, setSearchParams] = useSearchParams();
  const theme = useTheme();
  const navigate = useNavigate();
  const model = useWatch({
    control,
    name: "model",
  });
  const visibleModels = (modelsToShow || []).filter((m) =>
    ["turing_large", "turing_small", "turing_flash"].includes(m.value),
  );
  const functionConfigSchema =
    evalConfig?.functionParamsSchema ||
    evalConfig?.function_params_schema ||
    evalConfig?.config?.functionParamsSchema ||
    evalConfig?.config?.function_params_schema ||
    null;

  const functionConfigKeys =
    functionConfigSchema && typeof functionConfigSchema === "object"
      ? Object.keys(functionConfigSchema)
      : [];

  const groupParamSchema = Object.entries(
    groupFunctionParamsRequirements || {},
  ).reduce((acc, [paramName, details]) => {
    if (details?.schema) {
      acc[paramName] = details.schema;
    }
    return acc;
  }, {});
  const groupParamKeys = Object.keys(groupParamSchema);

  const { data: jsonSchemas = {} } = useGetJsonColumnSchema(datasetId, {
    enabled: Boolean(datasetId) && _module === "dataset",
  });

  const emptyMessage = () => {
    return isFutureagiBuilt
      ? model === ""
        ? "Select a model first"
        : selectedEval?.name === "prompt_instruction_adherence" &&
            requiredRunPrompt?.includes(_module)
          ? "No run-prompt column"
          : "No suitable column"
      : undefined;
  };
  return (
    <>
      <ShowComponent condition={!hideBackButtons}>
        <Box display="flex" justifyContent="space-between">
          <Button
            size="small"
            onClick={() => {
              if (isSampleFutureAgiBuilt) {
                setCurrentTab("groups"), setVisibleSection("config");
                return;
              }

              if (selectedEval?.isGroupEvals) {
                setSearchParams((prev) => ({
                  ...Object.fromEntries(prev),
                  groupId: selectedEval.id,
                }));
                setVisibleSection("create-group");
              } else {
                onBack();
              }
            }}
            variant="outlined"
            sx={{
              borderRadius: theme.spacing(0.5),
              paddingX: theme.spacing(1.5),
              paddingY: theme.spacing(0.5),
              borderColor: "action.hover",
            }}
            startIcon={
              <Iconify
                icon="lucide:chevron-left"
                sx={{
                  width: theme.spacing(2),
                  height: theme.spacing(2),
                  marginRight: "-4px",
                  // background:'red',
                }}
              />
            }
          >
            <ShowComponent condition={selectedEval?.isGroupEvals}>
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                {isSampleFutureAgiBuilt ? "Back" : "      Back to group review"}
              </Typography>
            </ShowComponent>
            <ShowComponent condition={!selectedEval?.isGroupEvals}>
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                Back
              </Typography>
            </ShowComponent>
          </Button>
          <IconButton
            onClick={onClose}
            sx={{
              padding: 0,
              paddingY: 0,
            }}
          >
            <Iconify
              icon="line-md:close"
              sx={{
                width: theme.spacing(3),
                height: theme.spacing(3),
                color: "text.primary",
              }}
            />
          </IconButton>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!hideTitle && !selectedEval?.isGroupEvals}>
        <Box
          display={"flex"}
          flexDirection={"row"}
          justifyContent={"space-between"}
          gap={1}
        >
          <HeadingAndSubHeading
            heading={
              <Typography typography={"m3"} fontWeight={"fontWeightSemiBold"}>
                {isEditMode
                  ? evalsData?.template_name ?? selectedEval?.name
                  : selectedEval?.name ?? evalsData?.template_name}
              </Typography>
            }
            subHeading={
              <Typography
                typography={"s1"}
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                {selectedEval?.description ?? evalsData?.description}
              </Typography>
            }
          />
          {Evals_Docs_mapping[evalConfig?.name] && (
            <Button
              variant="outlined"
              size="small"
              sx={{
                color: "text.primary",
                minWidth: "100px",
                maxWidth: "100px",
                marginLeft: "auto",
                fontSize: "12px",
                whiteSpace: "nowrap",
                borderColor: "text.disabled",
              }}
              startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
              component="a"
              href="https://docs.futureagi.com/docs/evaluation/builtin"
              target="_blank"
              rel="noopener noreferrer"
            >
              View Docs
            </Button>
          )}
        </Box>
      </ShowComponent>
      <ShowComponent condition={selectedEval?.isGroupEvals && !hideGroupHeader}>
        <Stack gap={0.25}>
          <Typography
            typography={"m2"}
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Run Group
          </Typography>
          <Typography
            typography={"s1"}
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            Run group with the selected evaluations
          </Typography>
        </Stack>
      </ShowComponent>
      <ShowComponent condition={selectedEval?.isGroupEvals}>
        <Stack gap={1}>
          <Typography
            typography={"s2"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Selected Evals ({members?.length ?? 0})
          </Typography>
          <Stack direction={"row"} gap={0.5} flexWrap={"wrap"}>
            {filteredVisibleItems?.map((evalItem) => (
              <Box
                key={evalItem?.eval_template_id}
                sx={{
                  height: "26px",
                  borderRadius: "4px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  px: 1,
                  py: 0.5,
                  backgroundColor: "action.hover",
                }}
              >
                <TooltipForEvals selectedEvalItem={evalItem}>
                  <Typography
                    sx={{
                      fontSize: "12px",
                      fontWeight: 500,
                      color: "primary.main",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {evalItem?.name}
                  </Typography>
                </TooltipForEvals>
                <ShowComponent
                  condition={typeof handleEvalDelete === "function"}
                >
                  {filteredVisibleItems?.length !== 1 &&
                    !isSampleFutureAgiBuilt && (
                      <IconButton
                        onClick={() =>
                          handleEvalDelete(evalItem?.eval_template_id)
                        }
                        sx={{
                          padding: 0,
                          color: "primary.main",
                          cursor: "pointer",
                          ml: 1,
                        }}
                      >
                        <Iconify
                          sx={{ height: 12, width: 12 }}
                          icon="mingcute:close-line"
                        />
                      </IconButton>
                    )}
                </ShowComponent>
              </Box>
            ))}
            {members?.length > 10 && (
              <Box
                sx={{
                  display: "flex",
                  my: "auto",
                  alignItems: "center",
                  gap: 0.5,
                  height: "18px",

                  fontSize: "10px",
                  fontWeight: 500,
                  color: "primary.main",
                }}
              >
                <Typography
                  fontSize={12}
                  onClick={() => setShowAll((prev) => !prev)}
                >
                  {showAll ? "Show Less" : "Show More"}
                </Typography>

                <IconButton
                  size="small"
                  onClick={() => setShowAll((prev) => !prev)}
                  sx={{ padding: 0 }}
                >
                  <Iconify
                    icon={showAll ? "tabler:chevron-up" : "tabler:chevron-down"}
                    width={16}
                    height={16}
                    sx={{ color: "primary.main" }}
                  />
                </IconButton>
              </Box>
            )}
          </Stack>
        </Stack>
      </ShowComponent>
      <ShowComponent condition={!selectedEval?.isGroupEvals}>
        <HeadingAndSubHeading
          heading={
            <FormTextFieldV2
              control={control}
              fieldName={"name"}
              helperText={undefined}
              label={"Name"}
              required
              fullWidth
              disabled={disabledName || isViewMode}
              size={"small"}
              onBlur={undefined}
              placeholder={"Enter name"}
            />
          }
        />
      </ShowComponent>
      {(isFutureagiBuilt || alwaysShowModel) &&
        visibleModels.length > 0 &&
        !hideModel && (
        <HeadingAndSubHeading
          heading={
            <FormSearchSelectFieldControl
              control={control}
              disabled={isViewMode}
              options={visibleModels.map((model) => {
                return {
                  ...model,
                  component: (
                    <Box sx={{ padding: theme.spacing(0.75, 1) }}>
                      <Box
                        display={"flex"}
                        flexDirection={"row"}
                        alignItems={"center"}
                        gap={"8px"}
                      >
                        <img
                          src={"/favicon/logo.svg"}
                          style={{
                            height: theme.spacing(2),
                            width: theme.spacing(2),
                          }}
                        />
                        <Typography
                          typography="s1"
                          fontWeight={"fontWeightMedium"}
                          color={"text.primary"}
                        >
                          {model.label}
                        </Typography>
                      </Box>
                      <Typography
                        typography={"s2"}
                        sx={{
                          marginLeft: theme.spacing(3),
                          wordWrap: "break-word",
                          whiteSpace: "normal",
                        }}
                        color={"text.primary"}
                      >
                        {model.description}
                      </Typography>
                    </Box>
                  ),
                };
              })}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <img
                      src={"/favicon/logo.svg"}
                      style={{
                        height: theme.spacing(2),
                        width: theme.spacing(2),
                      }}
                    />
                  </InputAdornment>
                ),
              }}
              error={formState.errors?.model && formState.errors.model.message}
              fieldName={"model"}
              label={"Language Model"}
              size={"small"}
              fullWidth
              required
            />
          }
          subHeading={
            modeHelpMessage
              ? modeHelpMessage
              : "The model to use for evaluation"
          }
        />
      )}
      <ShowComponent condition={!hideKnowledgeBase}>
        <HeadingAndSubHeading
          heading={
            <FormSearchSelectFieldControl
              disabled={isViewMode}
              label="Knowledge base"
              placeholder="Choose knowledge base"
              size="small"
              control={control}
              fieldName={`kbId`}
              fullWidth
              createLabel="Create knowledge base"
              handleCreateLabel={() => navigate(paths.dashboard.knowledge_base)}
              options={knowledgeBaseOptions}
              emptyMessage={"No knowledge base has been added"}
            />
          }
          subHeading="Allows the LLM to leverage domain-specific or specialized information when evaluating"
        />
      </ShowComponent>
      <Box
        display={"flex"}
        py={selectedEval?.isGroupEvals ? "unset" : theme.spacing(2)}
        px={selectedEval?.isGroupEvals ? "unset" : theme.spacing(1.5)}
        gap={theme.spacing(1.5)}
        border={selectedEval?.isGroupEvals ? "unset" : `1px solid`}
        borderColor={"divider"}
        borderRadius={theme.spacing(0.5)}
        flexDirection={"column"}
      >
        <HeadingAndSubHeading
          heading="Required Inputs"
          subHeading={
            isEvalConfig && isViewMode
              ? "Map the required parameters mentioned below for evaluating output"
              : "Choose the columns from the dataset to map to the required parameters for evaluation"
          }
          required
        />
        <ShowComponent condition={!selectedEval?.isGroupEvals}>
          {filteredRequiredKeys.map((key, index) => {
            // For prompt_instruction_adherence on dataset screen, filter and rename columns for "prompt" key
            let keyColumns = filteredColumns ?? [];
            if (
              evalConfig?.template_name === "prompt_instruction_adherence" &&
              key === "prompt" &&
              (_module === "dataset" || _module === "dataset-update")
            ) {
              keyColumns = keyColumns
                .filter((col) => col.originType === "run_prompt")
                .map((col) => ({
                  ...col,
                  headerName: `prompt-${col.headerName}-input`,
                }));
            }

            return (
              <Box
                key={`${key}-${index}`}
                display={"flex"}
                alignItems={"center"}
                gap={theme.spacing(1)}
              >
                <FieldSelection
                  field={filteredRequiredKeys[index]}
                  fieldName={`config.mapping.${key}`}
                  allColumns={keyColumns}
                  jsonSchemas={jsonSchemas}
                  control={control}
                  fullWidth
                  module={_module}
                  disabled={isViewMode}
                  isMultipleColumn={false}
                  check={undefined}
                  placeholder="Choose column"
                  isChecked={false}
                  handleCheckbox={undefined}
                  noOptions={emptyMessage()}
                  hideFieldColumns={hideFieldColumns}
                />
                <IconButton
                  sx={{
                    paddingY: 0,
                    paddingX: 0.5,
                    height: theme.spacing(3),
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    copyToClipboard(filteredRequiredKeys[index]);
                    enqueueSnackbar({
                      message: "Copied to clipboard",
                      variant: "success",
                    });
                  }}
                >
                  <Iconify
                    icon="tabler:copy"
                    sx={{
                      width: theme.spacing(2),
                      height: theme.spacing(2),
                    }}
                  />
                </IconButton>
              </Box>
            );
          })}
        </ShowComponent>
        <ShowComponent condition={selectedEval?.isGroupEvals}>
          {groupedRequiredKeys?.map((group, index) => {
            const evals = group?.evals?.map((evalItm) => evalItm?.name);
            const groupedKeys = group?.requiredKeys?.filter((gKey) =>
              filteredRequiredKeys?.includes(gKey),
            );
            const uniqueOptionalKeys = [
              ...new Set(
                group?.requiredKeys
                  ?.filter((item) => String(item).startsWith("OPT"))
                  ?.map((item) => {
                    return item.slice(4, item?.length);
                  })
                  ?.filter((item) => !filteredRequiredKeys?.includes(item)),
              ),
            ];
            if (groupedKeys?.length === 0 && uniqueOptionalKeys?.length === 0) {
              return <></>;
            }
            return (
              <Box
                key={index}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.5,
                  padding: theme.spacing(2, 1.5),
                  display: "flex",
                  flexDirection: "column",
                  gap: 1.5,
                }}
              >
                <ShowComponent
                  condition={
                    groupedKeys?.length > 0 || uniqueOptionalKeys?.length > 0
                  }
                >
                  <Stack direction={"row"} flexDirection={"row"} gap={1}>
                    <SvgColor
                      sx={{
                        height: 16,
                        width: 16,
                      }}
                      src="/assets/icons/ic_column_mapping.svg"
                    />
                    <Typography
                      sx={{
                        typography: "s2_1",
                        fontWeight: "fontWeightRegular",
                        color: "text.primary",
                      }}
                    >
                      Variable required for{" "}
                      <Typography
                        component={"span"}
                        sx={{
                          fontStyle: "italic",
                          typography: "s2_1",
                          fontWeight: "fontWeightRegular",
                          color: "text.primary",
                          flexShrink: 0,
                        }}
                      >
                        {evals?.slice(0, 3)?.join(", ")}
                        <TooltipForEvals
                          selectedEvalItem={evals?.slice(3, evals.length)}
                        >
                          {evals?.length > 3 && (
                            <Typography
                              component={"span"}
                              fontWeight={"fontWeightMedium"}
                              typography={"s2_1"}
                              color={"text.primary"}
                              fontStyle={"normal"}
                              sx={{
                                cursor: "pointer",
                              }}
                            >
                              {` +${evals?.length - 3} more`}
                            </Typography>
                          )}
                        </TooltipForEvals>
                      </Typography>
                    </Typography>
                  </Stack>
                </ShowComponent>
                <ShowComponent condition={groupedKeys?.length > 0}>
                  <Stack flexDirection={"column"} gap={2}>
                    {groupedKeys?.map((gKey, index) => (
                      <FieldSelection
                        key={index}
                        field={groupedKeys[index]}
                        fieldName={`config.mapping.${gKey}`}
                        allColumns={filteredColumns ?? []}
                        jsonSchemas={jsonSchemas}
                        control={control}
                        fullWidth
                        disabled={isViewMode}
                        isMultipleColumn={false}
                        check={undefined}
                        placeholder="Choose column"
                        isChecked={false}
                        handleCheckbox={undefined}
                        noOptions={emptyMessage()}
                        hideFieldColumns={hideFieldColumns}
                      />
                    ))}
                  </Stack>
                </ShowComponent>
                <ShowComponent condition={uniqueOptionalKeys?.length > 0}>
                  <Typography
                    sx={{
                      typography: "s2_1",
                      color: "text.primary",
                      fontWeight: "fontWeightRegular",
                    }}
                  >
                    Optional Variables
                  </Typography>
                  <Stack flexDirection={"column"} gap={2}>
                    {uniqueOptionalKeys?.map((gKey) => (
                      <FieldSelection
                        key={gKey}
                        field={gKey}
                        fieldName={`config.mapping.${gKey}`}
                        allColumns={filteredColumns ?? []}
                        jsonSchemas={jsonSchemas}
                        control={control}
                        fullWidth
                        disabled={isViewMode}
                        isMultipleColumn={false}
                        check={undefined}
                        placeholder="Choose column"
                        isChecked={false}
                        handleCheckbox={undefined}
                        noOptions={emptyMessage()}
                        hideFieldColumns={hideFieldColumns}
                      />
                    ))}
                  </Stack>
                </ShowComponent>
                {typeof mappingError === "object" && mappingError?.[index] && (
                  <Typography color="error" variant="body2">
                    {mappingError[index]}
                  </Typography>
                )}
              </Box>
            );
          })}
        </ShowComponent>
        {typeof mappingError === "string" && mappingError && (
          <Typography color="error" variant="body2" sx={{ mb: 1 }}>
            {mappingError}
          </Typography>
        )}
      </Box>

      {(!isPending || evalsData) &&
        transformedOptionalKeys &&
        transformedOptionalKeys.length > 0 && (
          <Box
            display={"flex"}
            py={theme.spacing(2)}
            px={theme.spacing(1.5)}
            gap={theme.spacing(1.5)}
            border={`1px solid`}
            borderColor={"divider"}
            borderRadius={theme.spacing(0.5)}
            flexDirection={"column"}
          >
            <HeadingAndSubHeading heading="Optional Inputs" />
            {transformedOptionalKeys.map((key, index) => {
              return (
                <Box
                  key={`${key}-${index}`}
                  display={"flex"}
                  alignItems={"center"}
                  gap={theme.spacing(1)}
                >
                  <FieldSelection
                    field={key}
                    fieldName={`config.mapping.${key}`}
                    allColumns={filteredColumns ?? []}
                    jsonSchemas={jsonSchemas}
                    control={control}
                    fullWidth
                    disabled={isViewMode}
                    isMultipleColumn={false}
                    check={undefined}
                    placeholder="Choose column"
                    isChecked={false}
                    handleCheckbox={undefined}
                    noOptions={emptyMessage()}
                    hideFieldColumns={hideFieldColumns}
                  />
                  <IconButton
                    sx={{
                      paddingY: 0,
                      paddingX: 0.5,
                      height: theme.spacing(3),
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      copyToClipboard(key);
                      enqueueSnackbar({
                        message: "Copied to clipboard",
                        variant: "success",
                      });
                    }}
                  >
                    <Iconify
                      icon="tabler:copy"
                      sx={{
                        width: theme.spacing(2),
                        height: theme.spacing(2),
                      }}
                    />
                  </IconButton>
                </Box>
              );
            })}
          </Box>
        )}
      <ShowComponent
        condition={!selectedEval?.isGroupEvals && functionConfigKeys.length > 0}
      >
        <Box
          display={"flex"}
          py={theme.spacing(2)}
          px={theme.spacing(1.5)}
          border={`1px solid`}
          borderColor={"black.50"}
          borderRadius={theme.spacing(0.5)}
          flexDirection={"column"}
          gap={theme.spacing(2)}
        >
          <HeadingAndSubHeading
            heading="Function Parameters"
            subHeading="Configure function-specific parameters for this evaluation"
          />
          {functionConfigKeys.map((key) => (
            <ConfigRenderer
              key={key}
              control={control}
              viewMode={isViewMode}
              fieldName={`config.params.${key}`}
              label={key}
              type={inferConfigType(functionConfigSchema[key])}
              required={Boolean(functionConfigSchema?.[key]?.required)}
              description={
                evalConfig?.configParamsDesc?.[key] ||
                evalConfig?.config_params_desc?.[key] ||
                evalConfig?.config?.configParamsDesc?.[key] ||
                evalConfig?.config?.config_params_desc?.[key]
              }
            />
          ))}
        </Box>
      </ShowComponent>
      <ShowComponent
        condition={selectedEval?.isGroupEvals && groupParamKeys.length > 0}
      >
        <Box
          display={"flex"}
          py={theme.spacing(2)}
          px={theme.spacing(1.5)}
          border={`1px solid`}
          borderColor={"black.50"}
          borderRadius={theme.spacing(0.5)}
          flexDirection={"column"}
          gap={theme.spacing(2)}
        >
          <HeadingAndSubHeading
            heading="Group Function Parameters"
            subHeading="These values are applied to all group evals that support the same parameter name"
          />
          {groupParamKeys.map((key) => {
            const details = groupFunctionParamsRequirements?.[key] || {};
            const supportedBy = details?.supportedBy || [];
            const supportedNames = supportedBy
              .map((item) => item?.name)
              .filter(Boolean);
            return (
              <ConfigRenderer
                key={key}
                control={control}
                viewMode={isViewMode}
                fieldName={`config.params.${key}`}
                label={key}
                type={inferConfigType(groupParamSchema[key])}
                required={
                  (
                    groupFunctionParamsRequirements?.[key]?.requiredFor ||
                    groupFunctionParamsRequirements?.[key]?.required_for ||
                    []
                  ).length > 0
                }
                description={
                  supportedNames.length > 0
                    ? `Used by: ${supportedNames.join(", ")}`
                    : "Applied to matching evals in this group"
                }
              />
            );
          })}
        </Box>
      </ShowComponent>
      <Box
        display={"flex"}
        py={theme.spacing(2)}
        px={theme.spacing(1.5)}
        border={`1px solid`}
        borderColor={"divider"}
        borderRadius={theme.spacing(0.5)}
      >
        <HeadingAndSubHeading
          heading={
            <FormCheckboxField
              control={control}
              fieldName={"errorLocalizer"}
              label={"Error Localization"}
              helperText={undefined}
              disabled={isViewMode}
              labelPlacement="end"
              defaultValue={formState.defaultValues.errorLocalizer}
              labelProps={{
                gap: theme.spacing(1),
              }}
              checkboxSx={{
                padding: 0,
                "&.Mui-checked": {
                  color: "primary.light",
                },
              }}
            />
          }
          subHeading="Pinpoints the errors in your LLM output"
        />
      </Box>
      <Box display={"flex"} flexGrow={1} />
      <ShowComponent condition={!selectedEval?.isGroupEvals}>
        <Box
          display={isEvalConfig ? "none" : "flex"}
          flexDirection={"row"}
          {...(isEvalConfig && { justifyContent: "flex-end" })}
          gap={theme.spacing(1.5)}
        >
          {onFormSave ? (
            <Button
              fullWidth={!isEvalConfig}
              color="primary"
              variant="contained"
              onClick={(e) => {
                e.preventDefault();
                if (isEvalConfig) {
                  onFormSave();
                  return;
                }
                handleSubmit((values) => {
                  onFormSave(values, [], {
                    ...evalConfiguration,
                    models: evalConfig?.models,
                  });
                })();
              }}
              disabled={
                !RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][
                  role
                ] || !formState.isValid
              }
            >
              <Typography typography="s2" fontWeight="fontWeightMedium">
                {saveButtonTitle || "Save Eval"}
              </Typography>
            </Button>
          ) : (
            <>
              <ShowComponent condition={!isViewMode}>
                <ShowComponent condition={showTest}>
                  <LoadingButton
                    fullWidth
                    variant="outlined"
                    onClick={onTest}
                    loading={isTesting}
                    disabled={!formState.isValid && testLabel !== "Cancel"}
                  >
                    <Typography
                      typography={"s2"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {testLabel}
                    </Typography>
                  </LoadingButton>
                </ShowComponent>
                <ShowComponent condition={showAdd}>
                  <Button
                    fullWidth
                    color="primary"
                    variant="outlined"
                    disabled={!formState.isValid}
                    onClick={(e) => {
                      e.preventDefault();
                      setIsDirty(false);
                      setTimeout(() => {
                        handleSubmit(onSubmit(false))();
                      }, 10);
                    }}
                  >
                    <Typography
                      typography={"s2"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {saveLabel || ADD_BUTTON_MAPPER[_module] || "Add"}
                    </Typography>
                  </Button>
                </ShowComponent>
                {showDefaultButton && (
                  <LoadingButton
                    fullWidth
                    color="primary"
                    variant="contained"
                    type="submit"
                    disabled={!formState.isValid}
                    loading={formState.isLoading}
                  >
                    <Typography
                      typography={"s2"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {ADD_AND_RUN_BUTTON_MAPPER[_module] ?? runLabel}
                    </Typography>
                  </LoadingButton>
                )}
              </ShowComponent>
            </>
          )}
        </Box>
      </ShowComponent>
      <ShowComponent
        condition={selectedEval?.isGroupEvals && !hideAddGroupButton}
      >
        <LoadingButton
          sx={{
            width: "fit-content",
            ml: "auto",
          }}
          type={saveGroup ? "button" : "submit"}
          disabled={!formState.isValid}
          loading={formState.isLoading}
          onClick={
            saveGroup && onFormSave
              ? handleSubmit((formData) => onFormSave(formData, removedEvals))
              : undefined
          }
          startIcon={
            <SvgColor
              sx={{
                height: "20px",
                width: "20px",
              }}
              src={"/assets/icons/ic_add.svg"}
            />
          }
          variant="contained"
          color="primary"
        >
          Add Group
        </LoadingButton>
      </ShowComponent>
    </>
  );
}

EvaluationMappingFormContent.propTypes = {
  hideBackButtons: PropTypes.bool,
  isSampleFutureAgiBuilt: PropTypes.bool,
  setCurrentTab: PropTypes.func,
  setVisibleSection: PropTypes.func,
  selectedEval: PropTypes.object,
  onBack: PropTypes.func,
  hideTitle: PropTypes.bool,
  onClose: PropTypes.func,
  isEditMode: PropTypes.bool,
  evalsData: PropTypes.object,
  evalConfig: PropTypes.object,
  members: PropTypes.array,
  filteredVisibleItems: PropTypes.array,
  handleEvalDelete: PropTypes.func,
  setShowAll: PropTypes.func,
  showAll: PropTypes.bool,
  control: PropTypes.object,
  disabledName: PropTypes.string,
  isViewMode: PropTypes.bool,
  isFutureagiBuilt: PropTypes.bool,
  alwaysShowModel: PropTypes.bool,
  modelsToShow: PropTypes.array,
  hideModel: PropTypes.bool,
  modeHelpMessage: PropTypes.string,
  hideKnowledgeBase: PropTypes.bool,
  knowledgeBaseOptions: PropTypes.array,
  isEvalConfig: PropTypes.bool,
  filteredRequiredKeys: PropTypes.array,
  filteredColumns: PropTypes.array,
  datasetId: PropTypes.string,
  _module: PropTypes.string,
  transformedOptionalKeys: PropTypes.array,
  hideFieldColumns: PropTypes.bool,
  groupedRequiredKeys: PropTypes.array,
  mappingError: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  groupFunctionParamsRequirements: PropTypes.object,
  isPending: PropTypes.bool,
  onFormSave: PropTypes.func,
  handleSubmit: PropTypes.func,
  evalConfiguration: PropTypes.object,
  role: PropTypes.string,
  saveButtonTitle: PropTypes.string,
  showTest: PropTypes.bool,
  onTest: PropTypes.func,
  isTesting: PropTypes.bool,
  testLabel: PropTypes.string,
  showAdd: PropTypes.bool,
  setIsDirty: PropTypes.func,
  onSubmit: PropTypes.func,
  saveLabel: PropTypes.string,
  runLabel: PropTypes.string,
  showDefaultButton: PropTypes.bool,
  saveGroup: PropTypes.bool,
  removedEvals: PropTypes.array,
  formState: PropTypes.object,
  hideGroupHeader: PropTypes.bool,
  hideAddGroupButton: PropTypes.bool,
};
