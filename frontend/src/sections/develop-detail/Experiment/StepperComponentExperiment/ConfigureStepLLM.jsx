import {
  Box,
  Button,
  IconButton,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import { usePromptExecutions } from "src/api/develop/prompt";
import CustomSelectField from "src/components/FormSearchField/CustomSearchSelect";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import AgentPromptRenderer from "./AgentPromptRenderer";
import { getRandomId } from "src/utils/utils";
import { useDebounce } from "src/hooks/use-debounce";
import { useGetListOfAgents } from "../../../../api/experiment/use-get-agents";
import { PROMPT_CONFIG_TYPE, PROMPT_CONFIG_TYPES } from "../common";

const transformPromptToFormSchema = (listPrompt) => {
  const modelArray = [
    {
      id: getRandomId(),
      value: listPrompt?.model,
      ...listPrompt?.model_detail,
    },
  ];
  return {
    _itemId: getRandomId(),
    experimentType: "llm",
    promptId: listPrompt?.id || "",
    promptVersion: null,
    agentId: listPrompt?.agentId || listPrompt?.agent_id || null,
    agentVersion: listPrompt?.agentVersion || listPrompt?.agent_version || null,
    model: modelArray,
    configuration: {
      toolChoice:
        listPrompt?.configuration?.toolChoice ||
        listPrompt?.toolChoice ||
        "auto",
      tools: listPrompt?.configuration?.tools || listPrompt?.tools || [],
    },
    modelParams: {},
    name: listPrompt?.name || "Untitled",
    outputFormat: "string",
    // Draft state lives on the version, not the prompt — populated by
    // AgentPromptRenderer once the user selects a version (TH-4334).
    isDraft: false,
  };
};

const transformAgentToFormSchema = (agent) => {
  return {
    _itemId: getRandomId(),
    experimentType: "llm",
    promptId: null,
    promptVersion: null,
    agentId: agent?.id || agent?.agentId || "",
    name: agent?.name,
    agentVersion:
      agent?.activeVersionId || agent?.id || agent?.agentVersion || null,
    outputFormat: "string",
    // Draft state lives on the version — populated by AgentPromptRenderer
    // once the user selects a version (TH-4355).
    isDraft: false,
  };
};

const ConfigureStepLLM = ({
  control,
  setValue,
  errors,
  allColumns,
  getValues,
  watch,
  unregister,
  setError,
  clearErrors,
}) => {
  const typesOfConfigs = PROMPT_CONFIG_TYPES;

  const [addPromptAnchor, setAddPromptAnchor] = useState(null);
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearchValue = useDebounce(search, 500);
  const watchPromptConfig = useWatch({
    control,
    name: "promptConfig",
  });

  const {
    data: promptsData,
    fetchNextPage: fetchNextPromptListPage,
    hasNextPage: promptListHasNextPage,
    isFetchingNextPage: isFetchingPromptNextPage,
    isPending: promptsIsPending,
    error: promptsError,
    refetch: refetchPrompts,
  } = usePromptExecutions(
    anchorEl?.typeofConfig === PROMPT_CONFIG_TYPE.PROMPT,
    debouncedSearchValue,
    "chat",
  );
  const {
    data: agentsData,
    fetchNextPage: fetchNextAgentListPage,
    hasNextPage: agentListHasNextPage,
    isFetchingNextPage: isFetchingAgentNextPage,
    isPending: agentsIsPending,
    error: agentsError,
    refetch: refetchAgents,
  } = useGetListOfAgents(
    anchorEl?.typeofConfig === PROMPT_CONFIG_TYPE.AGENT,
    debouncedSearchValue,
    [],
    false,
  );

  const options = useMemo(() => {
    if (!promptsData?.pages) return [];

    return promptsData.pages.flatMap(
      (page) => page.results || page.data || page || [],
    );
  }, [promptsData]);
  const totalPromptsCount = promptsData?.pages?.[0]?.count ?? null;
  const noPromptsAvailable = totalPromptsCount === 0 && !debouncedSearchValue;
  const updatedPrompts = options.map((prompt) =>
    transformPromptToFormSchema(prompt),
  );

  const agentOptions = useMemo(() => {
    if (!agentsData?.pages) return [];
    return agentsData.pages.flatMap(
      (page) => page.graphs || page.results || page.data || [],
    );
  }, [agentsData]);
  const totalAgentsCount =
    agentsData?.pages?.[0]?.metadata?.total_count ??
    agentsData?.pages?.[0]?.count ??
    null;
  const noAgentsAvailable = totalAgentsCount === 0 && !debouncedSearchValue;
  const updatedAgents = agentOptions.map((agent) =>
    transformAgentToFormSchema(agent),
  );

  const handleClosePopover = () => {
    setAnchorEl(null);
    setSearch("");
    setAddPromptAnchor(null);
  };

  const { remove, fields } = useFieldArray({
    control,
    name: "promptConfig",
  });

  // react-hook-form stashes array-level errors under `.root.message`, while
  // item-level Zod issues still land on `.message`. Read both so messages
  // from `ctx.addIssue({ path: [] })` in the array superRefine (e.g. the
  // draft-prompt block — TH-4334) actually reach the renderer.
  const promptConfigError =
    errors?.promptConfig?.root?.message || errors?.promptConfig?.message;

  return (
    <Stack spacing={2}>
      <Button
        onClick={(e) => setAddPromptAnchor(e.currentTarget)}
        startIcon={
          <SvgColor
            sx={{
              height: 16,
              width: 16,
            }}
            src={"/assets/icons/ic_add.svg"}
          />
        }
        sx={{
          width: "fit-content",

          bgcolor: addPromptAnchor ? "purple.o10" : "background.paper",
        }}
        color="primary"
        variant="outlined"
        size="large"
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Add Prompt/Agents
        </Typography>
      </Button>
      <Popover
        open={Boolean(addPromptAnchor)}
        anchorEl={addPromptAnchor}
        onClose={() => setAddPromptAnchor(null)}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
        sx={{
          "& .MuiPopover-paper": {
            borderRadius: "2px !important",
            padding: 0.5,
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.paper",
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 0.25,
            width: "137px",
          }}
        >
          {typesOfConfigs.map((config, i) => (
            <Box
              key={i}
              onClick={(e) => {
                setAnchorEl({
                  targetContainer: e.currentTarget,
                  typeofConfig: config.value,
                });
              }}
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 1,
                padding: 3 / 8,
                cursor: "pointer",
                borderRadius: 0.5,
                backgroundColor:
                  Boolean(anchorEl) && anchorEl?.typeofConfig === config.value
                    ? "action.hover"
                    : "transparent",
                "&:hover": {
                  backgroundColor: "action.hover",
                },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <SvgColor sx={{ width: 20 }} src={config.icon} />
                <Typography sx={{ fontSize: 14 }}>{config.label}</Typography>
              </Box>
              <IconButton
                size="small"
                sx={{ cursor: "pointer", pointerEvents: "none" }}
              >
                <Iconify icon="line-md:chevron-right" width={16} height={16} />
              </IconButton>
            </Box>
          ))}
        </Box>
      </Popover>
      <ShowComponent condition={Boolean(promptConfigError)}>
        <Typography variant="body2" color={"error.main"}>
          {promptConfigError}
        </Typography>
      </ShowComponent>
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl?.targetContainer}
        onClose={handleClosePopover}
        anchorOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "left",
        }}
        slotProps={{
          paper: {
            sx: {
              ml: 1,
            },
          },
        }}
        sx={{
          "& .MuiPopover-paper": {
            borderRadius: "2px !important",
            padding: 0.5,

            border: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        <Box>
          {Boolean(anchorEl) &&
            anchorEl?.typeofConfig === PROMPT_CONFIG_TYPE.PROMPT && (
              <CustomSelectField
                name="promptConfig"
                control={control}
                data={updatedPrompts}
                isPending={promptsIsPending}
                fetchNextPage={fetchNextPromptListPage}
                hasNextPage={promptListHasNextPage}
                isFetchingNextPage={isFetchingPromptNextPage}
                multiple={true}
                onSearchChange={setSearch}
                searchQuery={search}
                width={"238px"}
                onRefresh={refetchPrompts}
                onClose={handleClosePopover}
                placeholder="Select Prompt"
                label="Select Prompt"
                height={"265px"}
                zeroOptionsMessage={
                  noPromptsAvailable
                    ? "No prompts found for LLM type. Create a new prompt to get started"
                    : options.length === 0 &&
                        !promptsIsPending &&
                        !debouncedSearchValue
                      ? "No prompts found"
                      : ""
                }
                zeroOptionsActionLabel="Add New Prompt"
                zeroOptionsActionUrl="/dashboard/workbench/all"
                error={promptsError ?? null}
                getOptionLabel={(option) => {
                  const name = option?.name || "";
                  return name.length > 30
                    ? `${name.substring(0, 30)}...`
                    : name;
                }}
                getOptionValue={(option) => option?.promptId}
              />
            )}
          {Boolean(anchorEl) &&
            anchorEl?.typeofConfig === PROMPT_CONFIG_TYPE.AGENT && (
              <CustomSelectField
                name="promptConfig"
                control={control}
                data={updatedAgents}
                isPending={agentsIsPending}
                fetchNextPage={fetchNextAgentListPage}
                hasNextPage={agentListHasNextPage}
                isFetchingNextPage={isFetchingAgentNextPage}
                multiple={true}
                onSearchChange={setSearch}
                searchQuery={search}
                width={"238px"}
                onRefresh={refetchAgents}
                onClose={handleClosePopover}
                placeholder="Select Agent"
                label="Select Agent"
                height={"265px"}
                zeroOptionsMessage={
                  noAgentsAvailable
                    ? "No agents found for LLM type. Create a new agent to get started"
                    : agentOptions.length === 0 &&
                        !agentsIsPending &&
                        !debouncedSearchValue
                      ? "No agents found"
                      : ""
                }
                zeroOptionsActionLabel="Add New Agent"
                zeroOptionsActionUrl="/dashboard/agents"
                error={agentsError ?? null}
                getOptionLabel={(option) => option?.name || ""}
                getOptionValue={(option) => option?.agentId}
              />
            )}
        </Box>
      </Popover>
      <Stack spacing={1}>
        {watchPromptConfig.map((prompt, index) => {
          const field = fields[index];
          const stableKey =
            prompt?._itemId ||
            field?.id ||
            `prompt-${prompt.promptId || prompt.agentId}`;
          return (
            <AgentPromptRenderer
              unregister={unregister}
              watch={watch}
              getValues={getValues}
              allColumns={allColumns}
              onRemove={() => {
                // Remove from fields array if it exists
                if (fields[index]) {
                  remove(index);
                }
                // Always remove from form value to ensure cleanup
                const updatedPrompts = watchPromptConfig.filter(
                  (_, i) => i !== index,
                );
                setValue("promptConfig", updatedPrompts);
                clearErrors("promptConfig");
              }}
              control={control}
              index={index}
              errors={errors}
              prompt={prompt}
              setValue={setValue}
              setError={setError}
              clearErrors={clearErrors}
              key={stableKey}
              origin="run-experiment"
              type={
                prompt?.agentId
                  ? PROMPT_CONFIG_TYPE.AGENT
                  : PROMPT_CONFIG_TYPE.PROMPT
              }
              fieldPrefix={`promptConfig.${index}`}
            />
          );
        })}
      </Stack>
    </Stack>
  );
};

export default ConfigureStepLLM;

ConfigureStepLLM.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  errors: PropTypes.object.isRequired,
  jsonSchemas: PropTypes.object,
  allColumns: PropTypes.array,
  getValues: PropTypes.func.isRequired,
  watch: PropTypes.func.isRequired,
  unregister: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
  clearErrors: PropTypes.func.isRequired,
};
