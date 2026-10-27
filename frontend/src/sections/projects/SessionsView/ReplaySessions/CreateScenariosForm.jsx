import {
  Box,
  Collapse,
  IconButton,
  Stack,
  styled,
  Typography,
  CircularProgress,
} from "@mui/material";
import React, { useState, useEffect, useRef } from "react";
import SvgColor from "../../../../components/svg-color/svg-color";
import { useFormContext } from "react-hook-form";
import FormTextFieldV2 from "../../../../components/FormTextField/FormTextFieldV2";
import PersonaSection from "../../../scenarios/PersonaSection";
// import ColumnSection from "../../../scenarios/scenario-detail/ColumnSection";
import { CustomAlert } from "../../../../components/CustomAlert/CustomAlert";
import ExpandableTextField from "src/components/ExpandableTextField/ExpandableTextFiels";
import { useReplayConfiguration } from "./useReplayConfiguration";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "notistack";
import {
  useReplaySessionsStoreShallow,
  useSessionsGridStoreShallow,
} from "./store";
import { useCreateAgentDefinitionVersion } from "src/api/agent-definition/agent-definition-version";
import { useCreateAgentDefinition } from "../../../../api/agent-definition/agent-definition-version";

export const StyledBox = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  border: "1px solid",
  borderColor: theme.palette.divider,
  backgroundColor: theme.palette.background.neutral,
  borderRadius: 1,
}));

const StyledBoxContainer = styled(Box)(({ theme }) => ({
  border: "1px solid",
  borderColor: theme.palette.background.neutral,
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.spacing(0.5),
  padding: theme.spacing(2),
  my: theme.spacing(2),
}));

const getExpandActions = (onSaveClick, isLoading = false) => [
  <CustomTooltip
    key={"1"}
    show={!isLoading}
    title="Save Agent Definition"
    placement="top"
    arrow
    type={"black"}
    size="small"
  >
    <IconButton
      onClick={onSaveClick}
      disabled={isLoading}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        padding: "5px",
        borderRadius: "4px",
        position: "relative",
        bgcolor: "background.paper",
        "&:hover": {
          bgcolor: "background.paper",
        },
        "&:disabled": {
          opacity: 0.6,
        },
      }}
      size="small"
    >
      {isLoading ? (
        <CircularProgress
          size={16}
          sx={{
            color: "text.primary",
          }}
        />
      ) : (
        <SvgColor
          sx={{
            height: "16px",
            width: "16px",
            bgcolor: "text.primary",
          }}
          src="/assets/icons/ic_floppy_save.svg"
        />
      )}
    </IconButton>
  </CustomTooltip>,
];

const AgentDefinitionDetails = () => {
  const [collapsed, setCollapsed] = useState(false);
  const {
    control,
    watch,
    getValues,
    setValue,
    formState: { errors },
  } = useFormContext();
  const hasAgentPrompt = watch("hasAgentPrompt");
  const agentDefExists = watch("agentDefExists");
  const agentDefinitionId = watch("agentDefinition.id");
  const {
    textContent: { agentDefinitionDescription: configDescription },
  } = useReplayConfiguration();

  const { createdReplay } = useReplaySessionsStoreShallow((s) => ({
    createdReplay: s.createdReplay,
  }));
  const isVoice = createdReplay?.suggestions?.agentType === "voice";
  const agentDefinitionDescription = isVoice
    ? "Agent definition has been created based on the calls you chose"
    : configDescription;

  // Track original/default values
  const originalValuesRef = useRef({
    name: null,
    prompt: null,
    defaultVersion: null,
  });

  // Initialize original values on mount
  useEffect(() => {
    const currentValues = getValues();
    if (
      originalValuesRef.current.name === null &&
      currentValues?.agentDefinition?.name
    ) {
      originalValuesRef.current = {
        name: currentValues.agentDefinition.name,
        prompt: currentValues.agentDefinition.prompt,
        defaultVersion: currentValues.agentDefinition.version,
      };
    }
  }, [getValues]);

  // Helper function to increment version
  const incrementVersion = (version) => {
    if (!version) return "v1";
    const match = version.match(/^v(\d+)$/i);
    if (match) {
      const num = parseInt(match[1], 10);
      return `v${num + 1}`;
    }
    // If version format is unexpected, try to extract number or default to v2
    const numMatch = version.match(/(\d+)/);
    if (numMatch) {
      const num = parseInt(numMatch[1], 10);
      return `v${num + 1}`;
    }
    return "v2"; // Default increment if format is unknown
  };

  // Handler to check and update version when name or prompt changes
  const handleFieldChange = (fieldType) => (e) => {
    const currentValues = getValues();
    const originalName = originalValuesRef.current.name;
    const originalPrompt = originalValuesRef.current.prompt;
    const originalDefaultVersion = originalValuesRef.current.defaultVersion;

    // Only proceed if original values are initialized
    if (
      originalName === null ||
      originalPrompt === null ||
      !originalDefaultVersion
    ) {
      return;
    }

    const currentName =
      fieldType === "name"
        ? e.target.value
        : currentValues?.agentDefinition?.name;
    const currentPrompt =
      fieldType === "prompt"
        ? e.target.value
        : currentValues?.agentDefinition?.prompt;

    const nameChanged = currentName !== originalName;
    const promptChanged = currentPrompt !== originalPrompt;

    // If either changed, increment version from default
    if (nameChanged || promptChanged) {
      const incrementedVersion = incrementVersion(originalDefaultVersion);
      const currentVersion = getValues("agentDefinition.version");

      // Only update if version hasn't been set to the incremented value yet
      if (currentVersion !== incrementedVersion) {
        setValue("agentDefinition.version", incrementedVersion, {
          shouldValidate: false,
        });
      }
    } else {
      // If values are back to original, reset to default version
      const currentVersion = getValues("agentDefinition.version");
      if (currentVersion !== originalDefaultVersion) {
        setValue("agentDefinition.version", originalDefaultVersion, {
          shouldValidate: false,
        });
      }
    }
  };

  const { mutate: saveAgentDefinitionVersion, isPending: isSaving } =
    useCreateAgentDefinitionVersion({
      agentDefinitionId: agentDefinitionId,
    });

  const { mutate: createAgentDefinition, isPending: isCreating } =
    useCreateAgentDefinition();

  const handleSaveAgentDefinition = () => {
    // Get the current form values
    if (errors?.agentDefinition?.name || errors?.agentDefinition?.prompt) {
      return;
    }
    const data = getValues() || {};

    const payload = {
      agentName: data?.agentDefinition?.name,
      description: data?.agentDefinition?.prompt,
      prompt: data?.agentDefinition?.prompt,
      agentType: data?.agentType,
      commitMessage: "Agent definition created from sessions",
      inbound: true,
      languages: ["en"],
      replay_session_id: createdReplay?.id,
    };
    // Call the mutation
    if (agentDefExists && agentDefinitionId) {
      saveAgentDefinitionVersion(payload, {
        onSuccess: () => {
          enqueueSnackbar("Agent definition updated successfully", {
            variant: "success",
          });
        },
      });
    } else {
      createAgentDefinition(payload, {
        onSuccess: () => {
          enqueueSnackbar("Agent definition created successfully", {
            variant: "success",
          });
        },
      });
    }
  };

  const isSavingOrCreating = isSaving || isCreating;

  return (
    <StyledBox>
      <Stack>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Agent Definition
          </Typography>
          <IconButton
            onClick={() => setCollapsed(!collapsed)}
            sx={{
              color: "text.primary",
              p: 0,
            }}
            size="small"
          >
            <SvgColor
              sx={{
                height: "24px",
                width: "24px",
                transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
                transition: "transform 0.3s ease",
                transformOrigin: "center",
              }}
              src="/assets/icons/custom/lucide--chevron-down.svg"
            />
          </IconButton>
        </Stack>

        <Collapse in={!collapsed}>
          {hasAgentPrompt ? (
            <Typography
              typography={"s2_1"}
              color={"text.disabled"}
              sx={{
                mr: 5,
                maxWidth: "97%",
              }}
            >
              {agentDefinitionDescription}
            </Typography>
          ) : (
            <CustomAlert
              sx={{
                mt: 2,
              }}
              variant="warning"
              message="The selected sessions don't have enough context to generate an agent prompt. Please enter your prompt manually below."
            />
          )}
        </Collapse>
      </Stack>
      <Collapse in={!collapsed}>
        <StyledBoxContainer
          sx={{
            mt: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Stack direction={"row"} alignItems={"center"} gap={2}>
            <FormTextFieldV2
              control={control}
              fieldName="agentDefinition.name"
              label={`Agent definition (${createdReplay?.suggestions?.agentType === "voice" ? "Voice" : "Chat"})`}
              required
              fullWidth
              size="small"
              disabled={!agentDefExists}
              onChange={handleFieldChange("name")}
            />
            <FormTextFieldV2
              control={control}
              fieldName="agentDefinition.version"
              label="Version"
              size="small"
              disabled
            />
          </Stack>
          <ExpandableTextField
            fieldName="agentDefinition.prompt"
            dialogTitle="Agent prompt"
            textFieldLabel="Prompt"
            otherActions={getExpandActions(
              handleSaveAgentDefinition,
              isSavingOrCreating,
            )}
            onSave={handleFieldChange("prompt")}
          >
            <FormTextFieldV2
              control={control}
              fieldName="agentDefinition.prompt"
              label="Agent prompt"
              required
              fullWidth
              size="small"
              multiline
              rows={4}
              onChange={handleFieldChange("prompt")}
            />
          </ExpandableTextField>
        </StyledBoxContainer>
      </Collapse>
    </StyledBox>
  );
};

const ScenarioDetails = () => {
  const [collapsed, setCollapsed] = useState(false);
  const { selectAll, toggledNodes, totalRowCount } =
    useSessionsGridStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      totalRowCount: s.totalRowCount,
    }));
  const { control } = useFormContext();
  const { createdReplay } = useReplaySessionsStoreShallow((s) => ({
    createdReplay: s.createdReplay,
  }));
  const itemLabel =
    createdReplay?.suggestions?.agentType === "voice" ? "calls" : "sessions";

  return (
    <StyledBox>
      <Stack>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Scenario Details
          </Typography>
          <IconButton
            onClick={() => setCollapsed(!collapsed)}
            sx={{
              color: "text.primary",
              p: 0,
            }}
            size="small"
          >
            <SvgColor
              sx={{
                height: "24px",
                width: "24px",
                transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
                transition: "transform 0.3s ease",
                transformOrigin: "center",
              }}
              src="/assets/icons/custom/lucide--chevron-down.svg"
            />
          </IconButton>
        </Stack>
        <Collapse in={!collapsed}>
          <Typography
            typography={"s2_1"}
            color={"text.secondary"}
            sx={{
              mr: 5,
              maxWidth: "97%",
            }}
          >
            Defines the test cases, personas, and conversation flows that your
            AI agent will encounter during simulations.
          </Typography>
        </Collapse>
      </Stack>
      <Collapse in={!collapsed}>
        <StyledBoxContainer
          sx={{
            mt: 2,
          }}
        >
          <Stack gap={2}>
            <FormTextFieldV2
              control={control}
              fieldName="scenarioName"
              label="Scenario name"
              required
              fullWidth
              size="small"
            />
            <Stack direction={"row"} alignItems={"flex-start"} gap={2}>
              <Box
                sx={{
                  padding: (theme) => theme.spacing(1.25, 1),
                  backgroundColor: (theme) => theme.palette.background.neutral,
                  width: "100%",
                }}
              >
                <Typography typography={"s1"} color={"text.primary"}>
                  {`# ${itemLabel}: `}
                  <Typography
                    fontWeight={"fontWeightMedium"}
                    component={"span"}
                    typography={"s1"}
                    color={"green.500"}
                  >
                    {selectAll
                      ? totalRowCount - toggledNodes?.length
                      : toggledNodes?.length}
                  </Typography>
                </Typography>
              </Box>
              <FormTextFieldV2
                control={control}
                fieldName="numOfScenarios"
                label="Number of scenarios"
                required
                fullWidth
                size="small"
              />
            </Stack>
          </Stack>
        </StyledBoxContainer>
      </Collapse>
    </StyledBox>
  );
};

const AdditionalDetails = () => {
  const [collapsed, setCollapsed] = useState(false);
  const {
    control,
    // formState: { errors },
  } = useFormContext();
  return (
    <StyledBox>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Additional Details
        </Typography>
        <IconButton
          onClick={() => setCollapsed(!collapsed)}
          sx={{
            color: "text.primary",
            p: 0,
          }}
          size="small"
        >
          <SvgColor
            sx={{
              height: "24px",
              width: "24px",
              transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
              transition: "transform 0.3s ease",
              transformOrigin: "center",
            }}
            src="/assets/icons/custom/lucide--chevron-down.svg"
          />
        </IconButton>
      </Stack>
      <Collapse in={!collapsed}>
        <StyledBoxContainer
          sx={{
            mt: 2,
          }}
        >
          <Stack gap={4}>
            <PersonaSection
              description="Persona type defines user behavior, and intent for your scenario tests."
              control={control}
              gridColumns={1}
            />
            {/* <ColumnSection errors={errors} control={control} /> */}
          </Stack>
        </StyledBoxContainer>
      </Collapse>
    </StyledBox>
  );
};

const CreateScenariosForm = () => {
  return (
    <Stack gap={2}>
      <AgentDefinitionDetails />
      <ScenarioDetails />
      <AdditionalDetails />
    </Stack>
  );
};

CreateScenariosForm.displayName = "CreateScenariosForm";
export default React.memo(CreateScenariosForm);
