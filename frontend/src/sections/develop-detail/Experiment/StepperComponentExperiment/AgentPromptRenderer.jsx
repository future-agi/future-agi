import {
  Box,
  Button,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useWatch, useFieldArray } from "react-hook-form";
import { usePromptVersions } from "src/api/develop/prompt";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import NewModelRenderWithParamsTool from "./NewModelRenderWithParamsTool";
import CustomTooltip from "src/components/tooltip";
import ChipForVariables from "./chipForVariables.jsx";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import { getOutputOptions, PROMPT_CONFIG_TYPE } from "../common";
import { useGetAgentVersions } from "../../../../api/experiment/use-get-agents.js";
import { VERSION_STATUS } from "src/sections/agent-playground/utils/constants";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useDebounce } from "src/hooks/use-debounce";
import SearchableSelectControl from "src/components/searchable-select-control/SearchableSelectControl";
const AgentPromptRenderer = ({
  prompt,
  onRemove,
  setValue,
  control,
  index,
  getValues,
  watch,
  unregister,
  allColumns,
  setError: _setError,
  clearErrors: _clearErrors,
  errors,
  origin = "run-experiment",
  fieldPrefix = "config",
  type = "prompt",
}) => {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const {
    data: versionsData,
    isPending: versionIsPending,
    refetch: refetchVersions,
    fetchNextPage: fetchNextVersionsPage,
    hasNextPage: hasNextVersionsPage,
    isFetchingNextPage: isFetchingNextVersionsPage,
  } = usePromptVersions(
    type === PROMPT_CONFIG_TYPE.PROMPT ? prompt?.promptId : null,
    debouncedSearch,
  );
  const {
    data: agentVersionsData,
    isPending: agentVersionIsPending,
    fetchNextPage: fetchNextAgentVersionsPage,
    hasNextPage: hasNextAgentVersionsPage,
    isFetchingNextPage: isFetchingNextAgentVersionsPage,
  } = useGetAgentVersions(
    type === PROMPT_CONFIG_TYPE.AGENT ? prompt?.agentId : null,
    debouncedSearch,
  );

  const pickupLatestVersion = useRef(false);

  const versionsOptions = useMemo(() => {
    return versionsData?.pages?.flatMap((page) => page?.results ?? []) || [];
  }, [versionsData]);

  const agentVersionsOptions = useMemo(() => {
    return (
      agentVersionsData?.pages?.flatMap(
        (page) => page?.result?.versions ?? [],
      ) || []
    );
  }, [agentVersionsData]);

  const { fields, remove, replace } = useFieldArray({
    control,
    name: `${fieldPrefix}.model`,
  });

  // Watch the prompt/agent version to trigger validation on change
  const watchedPromptVersion = useWatch({
    control,
    name: `${fieldPrefix}.promptVersion`,
  });

  const watchedAgentVersion = useWatch({
    control,
    name: `${fieldPrefix}.agentVersion`,
  });

  // Memoize column headers to prevent unnecessary re-renders
  const columnHeaders = useMemo(() => {
    return allColumns?.map((col) => col.headerName ?? col?.name) || [];
  }, [allColumns]);

  // Derive variablesInfo from data and store unmapped count in form
  const prevNotMappedRef = useRef(null);
  const variablesInfo = useMemo(() => {
    let result;
    let selectedVersion;
    if (type === "prompt") {
      selectedVersion = versionsOptions?.find(
        (v) => v.id === watchedPromptVersion,
      );
      if (!selectedVersion?.variable_names) {
        result = { total: 0, notMapped: 0 };
      } else {
        const versionVariables = Object.keys(selectedVersion.variable_names);
        const missingCount = versionVariables.filter(
          (variable) => !columnHeaders.includes(variable),
        ).length;
        result = { total: versionVariables.length, notMapped: missingCount };
      }
    } else if (type === "agent") {
      selectedVersion = agentVersionsOptions?.find(
        (v) => v.id === watchedAgentVersion,
      );
      if (
        !selectedVersion?.global_variables ||
        selectedVersion.global_variables.length === 0
      ) {
        result = { total: 0, notMapped: 0 };
      } else {
        const missingCount = selectedVersion.global_variables.filter(
          (variable) => !columnHeaders.includes(variable),
        ).length;
        result = {
          total: selectedVersion.global_variables.length,
          notMapped: missingCount,
        };
      }
    } else {
      result = { total: 0, notMapped: 0 };
    }

    if (prevNotMappedRef.current !== result.notMapped) {
      prevNotMappedRef.current = result.notMapped;
      setValue(`${fieldPrefix}.unmappedVariables`, result.notMapped, {
        shouldValidate: false,
      });
    }
    return result;
  }, [
    versionsOptions,
    agentVersionsOptions,
    watchedPromptVersion,
    watchedAgentVersion,
    columnHeaders,
    type,
    setValue,
    fieldPrefix,
  ]);

  // Set initial prompt version
  useEffect(() => {
    if (type !== "prompt") return;
    if (
      versionsOptions?.length === 0 ||
      (prompt?.promptVersion && !pickupLatestVersion?.current)
    ) {
      return;
    }
    pickupLatestVersion.current = false;
    setValue(`${fieldPrefix}.promptVersion`, versionsOptions?.[0]?.id);
  }, [type, versionsOptions, prompt?.promptVersion, setValue, fieldPrefix]);

  // Mirror the selected version's draft flag + version label onto the
  // form item so the schema's superRefine can block "Next" when a draft
  // version is picked, and emit a per-item error naming the exact
  // version the user needs to save or swap (TH-4334).
  useEffect(() => {
    if (type !== PROMPT_CONFIG_TYPE.PROMPT) return;
    const selected = versionsOptions?.find(
      (v) => v?.id === watchedPromptVersion,
    );
    // versionLabel is informational (used in the error message) — no
    // need to revalidate on it. isDraft drives the schema's draft guard,
    // so validate immediately: the card goes red the moment the user
    // picks a draft version, instead of waiting for the next Next-click.
    setValue(`${fieldPrefix}.versionLabel`, selected?.template_version || "", {
      shouldValidate: false,
    });
    setValue(`${fieldPrefix}.isDraft`, Boolean(selected?.is_draft), {
      shouldValidate: true,
    });
  }, [type, watchedPromptVersion, versionsOptions, setValue, fieldPrefix]);

  // Set initial agent version
  useEffect(() => {
    if (type !== "agent") return;
    if (
      agentVersionsOptions?.length === 0 ||
      (prompt?.agentVersion && !pickupLatestVersion?.current)
    ) {
      return;
    }
    pickupLatestVersion.current = false;
    setValue(`${fieldPrefix}.agentVersion`, agentVersionsOptions?.[0]?.id);
  }, [type, agentVersionsOptions, prompt?.agentVersion, setValue, fieldPrefix]);

  // Mirror the selected agent version's draft flag + version label onto
  // the form item so the shared `promptConfig` superRefine blocks "Next"
  // when a draft agent version is picked, reusing the same `isDraft` /
  // `versionLabel` fields as the prompt branch (TH-4355). The versions
  // endpoint returns `status` as a string; agent-playground's
  // `VERSION_STATUS.DRAFT` is the canonical match.
  useEffect(() => {
    if (type !== PROMPT_CONFIG_TYPE.AGENT) return;
    // Wait for the versions list and a concrete selection before
    // writing. Otherwise the effect fires on every render during fetch
    // with `selected === undefined`, which would write `isDraft=false`
    // + `shouldValidate: true` and prematurely clear a live draft error.
    if (!agentVersionsOptions?.length || !watchedAgentVersion) return;
    const selected = agentVersionsOptions.find(
      (v) => v?.id === watchedAgentVersion,
    );
    if (!selected) return;
    setValue(
      `${fieldPrefix}.versionLabel`,
      selected.version_number != null ? `v${selected.version_number}` : "",
      { shouldValidate: false },
    );
    setValue(
      `${fieldPrefix}.isDraft`,
      selected.status === VERSION_STATUS.DRAFT,
      { shouldValidate: true },
    );
  }, [type, watchedAgentVersion, agentVersionsOptions, setValue, fieldPrefix]);

  const watchModels = useWatch({
    control,
    name: `${fieldPrefix}.model`,
  });
  const matched = variablesInfo.total - variablesInfo.notMapped;
  const errorMessage =
    (variablesInfo.notMapped > 0
      ? `Variable mismatch: ${matched}/${variablesInfo.total} variables matched. The ${type === "agent" ? "agent" : "prompt"} variables don't match the dataset columns.`
      : null) ||
    errors?.promptConfig?.[index]?.unmappedVariables?.message ||
    errors?.promptConfig?.[index]?.isDraft?.message ||
    errors?.promptConfig?.[index]?.model?.message;
  return (
    <Box
      sx={{
        border: "1px solid ",
        padding: "10px 16px",
        borderRadius: "5px",
        backgroundColor: errorMessage ? "red.o5" : "background.default",
        borderColor: errorMessage ? "error.main" : "divider",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            gap: 2,
          }}
        >
          <CustomTooltip
            title={prompt?.name}
            size="small"
            arrow={true}
            show={prompt?.name?.length > 40}
          >
            <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
              {prompt?.name?.length > 40
                ? `${prompt.name.substring(0, 40)}...`
                : prompt?.name}
            </Typography>
          </CustomTooltip>

          <ShowComponent condition={type === PROMPT_CONFIG_TYPE.PROMPT}>
            <SearchableSelectControl
              control={control}
              fieldName={`${fieldPrefix}.promptVersion`}
              options={versionsOptions}
              isPending={versionIsPending}
              fetchNextPage={fetchNextVersionsPage}
              hasNextPage={hasNextVersionsPage}
              isFetchingNextPage={isFetchingNextVersionsPage}
              searchQuery={search}
              onSearchChange={setSearch}
              getOptionLabel={(v) =>
                v?.is_default === true
                  ? `${v?.template_version} (default)`
                  : v?.template_version ?? ""
              }
              getOptionValue={(v) => v?.id}
              placeholder="Select version"
            />
          </ShowComponent>

          <ShowComponent condition={type === PROMPT_CONFIG_TYPE.AGENT}>
            <SearchableSelectControl
              control={control}
              fieldName={`${fieldPrefix}.agentVersion`}
              options={agentVersionsOptions}
              isPending={agentVersionIsPending}
              fetchNextPage={fetchNextAgentVersionsPage}
              hasNextPage={hasNextAgentVersionsPage}
              isFetchingNextPage={isFetchingNextAgentVersionsPage}
              searchQuery={search}
              onSearchChange={setSearch}
              getOptionLabel={(v) => `v${v?.version_number}`}
              getOptionValue={(v) => v?.id}
              placeholder="Select version"
            />
          </ShowComponent>
        </Box>
        {onRemove && (
          <IconButton
            size="small"
            onClick={onRemove}
            sx={{
              width: 22,
              height: 22,
              padding: "3px",
              borderRadius: "2px",
              backgroundColor: "background.default",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <SvgColor
              sx={{
                height: "20px",
                width: "20px",
                color: "error.main",
              }}
              src={"/assets/icons/ic_delete.svg"}
            />
          </IconButton>
        )}
      </Box>
      <ShowComponent
        condition={origin === "run-experiment" && type !== "agent"}
      >
        <Stack spacing={1}>
          {fields.length > 0 &&
            fields.map((field, modelIdx) => {
              const handleRemoveModel = () => {
                remove(modelIdx);
                unregister(
                  `${fieldPrefix}.modelParams.${watchModels?.[modelIdx]?.value}`,
                );
              };
              return (
                <NewModelRenderWithParamsTool
                  selectedModel={watchModels?.[modelIdx]}
                  modelIndex={modelIdx}
                  index={index}
                  control={control}
                  setValue={setValue}
                  key={field.id}
                  getValues={getValues}
                  useWatch={watch}
                  modelParamsFieldName={`${fieldPrefix}.modelParams.${watchModels?.[modelIdx]?.value}`}
                  modelType={"llm"}
                  voiceFieldName={`${fieldPrefix}.model.${modelIdx}.voices`}
                  fieldPrefix={fieldPrefix}
                  onRemove={fields?.length === 1 ? null : handleRemoveModel}
                  deleteIcon="/assets/icons/ic_close.svg"
                />
              );
            })}
        </Stack>

        <ShowComponent condition={type === PROMPT_CONFIG_TYPE.PROMPT}>
          <FormSearchSelectFieldControl
            fullWidth
            label="Output Format"
            size="small"
            required={true}
            control={control}
            fieldName={`${fieldPrefix}.outputFormat`}
            options={getOutputOptions["llm"] || []}
            sx={{ backgroundColor: "background.paper" }}
          />
        </ShowComponent>
        <ShowComponent
          condition={
            type !== "agent" &&
            watchModels?.length > 0 &&
            watchModels?.[0]?.value
          }
        >
          <CustomModelDropdownControl
            fieldName="model"
            fieldPrefix={fieldPrefix}
            modelObjectKey={null}
            multiple={true}
            control={control}
            openSelectModel={open}
            setOpenSelectModel={setOpen}
            showButtons={true}
            extraParams={{ model_type: "llm" }}
            customTrigger={
              <Button
                startIcon={
                  <SvgColor
                    sx={{
                      height: 20,
                      width: 20,
                    }}
                    src="/assets/icons/ic_add.svg"
                  />
                }
                variant="outlined"
                size={"small"}
                sx={{ bgcolor: "background.paper" }}
                onClick={() => setOpen(true)}
              >
                Add models
              </Button>
            }
            onChange={(e) => {
              // Use replace from useFieldArray to properly update the array
              replace(e.target.value || []);
            }}
          />
        </ShowComponent>
      </ShowComponent>

      <ShowComponent condition={type === "prompt" || type === "agent"}>
        <ShowComponent condition={variablesInfo?.total > 0}>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              padding: "12px 8px",
              backgroundColor: "background.paper",
            }}
          >
            <ShowComponent condition={variablesInfo.notMapped === 0}>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <Typography typography="s2" fontWeight={"fontWeightMedium"}>
                  {`${`${variablesInfo?.total}/${variablesInfo?.total} variables mapped to dataset columns`}`}
                </Typography>
                <ChipForVariables status="all" />
              </Box>
            </ShowComponent>
            <ShowComponent condition={variablesInfo.notMapped > 0}>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <Typography typography="s2" fontWeight={"fontWeightMedium"}>
                  {`${variablesInfo?.total - variablesInfo?.notMapped}/${variablesInfo?.total} variables mapped to dataset columns`}
                </Typography>
                <ChipForVariables
                  status={
                    variablesInfo?.total === variablesInfo?.notMapped
                      ? "none"
                      : "partial"
                  }
                />
                <Button
                  size="small"
                  sx={{ color: "blue.500", bgcolor: "background.paper" }}
                  startIcon={
                    <SvgColor
                      sx={{
                        height: 16,
                        width: 16,
                      }}
                      src="/assets/icons/ic_reload.svg"
                    />
                  }
                  onClick={() => {
                    refetchVersions();
                    pickupLatestVersion.current = true;
                  }}
                >
                  Refresh
                </Button>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 1,
                  mt: 1,
                }}
              >
                <Typography typography="s2" fontWeight={"fontWeightMedium"}>
                  {type === "prompt"
                    ? "Update variable names in workbench to match dataset columns."
                    : "Update global variables in agent to match dataset columns."}
                </Typography>
                <Button
                  variant="outlined"
                  sx={{
                    bgcolor: "background.paper",
                    px: 1.5,
                    borderColor: "border.light",
                  }}
                  onClick={() => {
                    const url =
                      type === "prompt"
                        ? `/dashboard/workbench/create/${prompt?.promptId}`
                        : `/dashboard/agents/playground/${prompt?.agentId}`;
                    window.open(url, "_blank");
                  }}
                  startIcon={
                    <SvgColor src="/assets/icons/ic_arrow_top_right.svg" />
                  }
                >
                  {type === "prompt"
                    ? "Go to Workbench"
                    : "Go to Agent Playground"}
                </Button>
              </Box>
            </ShowComponent>
          </Box>
        </ShowComponent>
      </ShowComponent>

      <ShowComponent condition={Boolean(errorMessage)}>
        <Typography
          typography={"s2"}
          fontWeight={"fontWeightMedium"}
          color="error.main"
        >
          {errorMessage}
        </Typography>
      </ShowComponent>
    </Box>
  );
};

export default AgentPromptRenderer;

AgentPromptRenderer.propTypes = {
  prompt: PropTypes.object,
  onRemove: PropTypes.func,
  setValue: PropTypes.func,
  control: PropTypes.object,
  index: PropTypes.number,
  getValues: PropTypes.func,
  watch: PropTypes.func,
  unregister: PropTypes.func,
  allColumns: PropTypes.array,
  setError: PropTypes.func,
  clearErrors: PropTypes.func,
  errors: PropTypes.object,
  origin: PropTypes.string,
  fieldPrefix: PropTypes.string,
  type: PropTypes.string,
};
