import React, { useState, useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  TextField,
  Typography,
  styled,
  Checkbox,
  Chip,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Paper,
  IconButton,
  CircularProgress,
  TablePagination,
  Container,
  useTheme,
  stepConnectorClasses,
  StepConnector,
  Stepper,
  Step,
  Stack,
  FormControlLabel,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSnackbar } from "notistack";
import { useRouter } from "src/routes/hooks";
import { useDebounce } from "src/hooks/use-debounce";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import EmptyLayout from "../EmptyLayout/EmptyLayout";
import SvgColor from "../svg-color";
import { FormSearchSelectFieldState } from "../FromSearchSelectField";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import {
  voiceEvalColumns,
  chatEvalColumns,
  getVersionedEvalName,
  useAgentDefinitions,
} from "./common";
import { useNavigate } from "react-router";
import { ShowComponent } from "../show";
import CustomTooltip from "../tooltip";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import UpdateKeysDialog from "src/sections/agents/AgentConfiguration/UpdateKeysDialog";
import { useScenarioColumnConfig } from "src/sections/test/common";
import { isUUID } from "src/utils/utils";
import { IOSSwitch } from "../Switch/IOSSwitch";
import { getIconForAgentDefinitions } from "src/sections/scenarios/common";
import { AGENT_TYPES, isLiveKitProvider } from "src/sections/agents/constants";

const steps = [
  {
    number: 1,
    label: "Add simulation details",
    icon: "/icons/runTest/ic_settings.svg",
  },
  {
    number: 2,
    label: "Choose Scenario(s)",
    icon: "/icons/runTest/ic_workflow.svg",
  },
  {
    number: 3,
    label: "Select Evaluations",
    icon: "/icons/runTest/ic_shield.svg",
  },
  {
    number: 4,
    label: "Summary",
    icon: "/icons/runTest/ic_summary.svg",
  },
];

const StepCircle = styled(Box)(({ theme, active, completed }) => {
  const isDark = theme.palette.mode === "dark";
  return {
    width: 36,
    height: 36,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: completed
      ? theme.palette.background.paper
      : active
        ? isDark
          ? theme.palette.background.neutral
          : theme.palette.primary.lighter
        : theme.palette.background.paper,
    border: `1px solid ${
      completed
        ? theme.palette.green["500"]
        : active
          ? theme.palette.primary.main
          : theme.palette.text.disabled
    }`,
    color: completed
      ? theme.palette.green["500"]
      : active
        ? theme.palette.primary.main
        : theme.palette.text.disabled,
    fontWeight: 600,
    fontSize: 14,
    transition: "all 0.3s",
    zIndex: 10,
  };
});

const StyledStepConnector = styled(StepConnector)(({ theme }) => ({
  [`&.${stepConnectorClasses.alternativeLabel}`]: {
    top: 19,
    left: "calc(-50% + 17px)",
    right: "calc(50% + 14px)",
  },
  [`&.${stepConnectorClasses.active}`]: {
    [`& .${stepConnectorClasses.line}`]: {
      backgroundColor: theme.palette.green[500], // green for active
    },
  },
  [`&.${stepConnectorClasses.completed}`]: {
    [`& .${stepConnectorClasses.line}`]: {
      backgroundColor: theme.palette.green[500], // green for completed
    },
  },
  [`& .${stepConnectorClasses.line}`]: {
    height: 2,
    border: 0,
    backgroundColor: theme.palette.divider,
    borderRadius: 1,
    ...theme.applyStyles("dark", {
      backgroundColor: theme.palette.divider,
    }),
  },
}));

const CreateRunTestPage = ({ open, onClose }) => {
  const { enqueueSnackbar } = useSnackbar();
  const theme = useTheme();
  const router = useRouter();
  const [activeStep, setActiveStep] = useState(0);
  const [completed, setCompleted] = React.useState({});
  const queryClient = useQueryClient();

  const totalSteps = () => {
    return steps.length;
  };

  const completedSteps = () => {
    return Object.keys(completed).length;
  };

  const allStepsCompleted = () => {
    return completedSteps() === totalSteps();
  };

  const [formData, setFormData] = useState({
    testName: "",
    description: "",
    selectedScenarios: [],
    agentDefinitionId: "",
    agentDefinitionVersionId: "",
    selectedEvaluations: [],
    enableToolEvaluation: false,
    schedule: "immediate",
    notifications: false,
    failOnError: true,
    agentType: null,
  });

  // Add evaluation configuration state
  const [evaluationsConfig, setEvaluationsConfig] = useState([]);
  const [openEvaluationDialog, setOpenEvaluationDialog] = useState(false);
  const [openUpdateKeysDialog, setOpenUpdateKeysDialog] = useState(false);
  // When non-null, the new EvalPickerDrawer opens directly at the config
  // step with this template loaded (edit flow).
  const [editingEvalItem, setEditingEvalItem] = useState(null);
  useEffect(() => {
    if (open) {
      setActiveStep(0);
      setFormData({
        testName: "",
        description: "",
        selectedScenarios: [],
        selectedEvaluations: [],
        schedule: "immediate",
        notifications: false,
        failOnError: true,
        agentDefinitionId: "",
        agentDefinitionVersionId: "",
        enableToolEvaluation: false,
      });
      setEvaluationsConfig([]);
      setOpenEvaluationDialog(false);
      setEditingEvalItem(null);
      setCompleted({});
    }
  }, [open]);

  // Scenarios state
  const [scenarioSearch, setScenarioSearch] = useState("");
  const debouncedSearch = useDebounce(scenarioSearch, 500);
  const [scenariosPagination, setScenariosPagination] = useState({
    page: 1,
    pageSize: 10,
  });

  // Fetch scenarios with pagination
  const {
    data: scenariosData,
    isLoading: isLoadingScenarios,
    error: scenariosError,
  } = useQuery({
    queryKey: [
      "scenarios",
      scenariosPagination.page,
      scenariosPagination.pageSize,
      debouncedSearch,
      formData?.agentType,
    ],
    queryFn: async () => {
      const response = await axios.get(endpoints.scenarios.list, {
        params: {
          page: scenariosPagination.page,
          limit: scenariosPagination.pageSize,
          search: debouncedSearch,
          agent_type: formData?.agentType,
        },
      });
      return response.data;
    },
    enabled: !!formData?.agentType,
    keepPreviousData: true,
  });

  // Extract scenarios data
  const scenarios = scenariosData?.results || [];
  const scenariosTotal = scenariosData?.count || 0;
  const scenariosPage = scenariosPagination.page;
  const scenariosTotalPages = Math.ceil(
    scenariosTotal / scenariosPagination.pageSize,
  );

  // Since search is done server-side, we don't need to filter locally
  const filteredScenarios = scenarios;

  const navigate = useNavigate();

  const {
    agentDefinitions,
    fetchNextPage,
    isLoading: agentDefinitionsLoading,
    isFetchingNextPage: fetchNextAgentDefs,
  } = useAgentDefinitions();

  const { data } = useQuery({
    queryKey: ["get-scenario-column-configs", [formData?.selectedScenarios]],
    queryFn: () =>
      axios.get(endpoints.scenarios.getColumns, {
        params: {
          scenarios: JSON.stringify(formData?.selectedScenarios),
        },
      }),
    enabled: activeStep === 2,
  });
  const baseEvalColumns = useMemo(
    () =>
      formData?.agentType === AGENT_TYPES.CHAT
        ? chatEvalColumns
        : voiceEvalColumns,
    [formData?.agentType],
  );
  const evalColumnsWithScenarios = useScenarioColumnConfig(baseEvalColumns, {
    scenariosDetail: data?.data?.columnConfigs ?? [],
  });

  // Synthetic binding vocabulary for the create-simulation flow. The
  // simulation hasn't run yet, so we can't fetch real call data — but
  // we already know the agent/persona/scenario/prompt the user picked,
  // and we know the runtime key set the backend resolver supports.
  // Keys use dot-hierarchy (agent.name, persona.name, call.transcript)
  // matching the backend aliases in
  // simulate/temporal/activities/xl.py (CONTEXT_MAP_DOT_ALIASES +
  // TRANSCRIPT_DOT_ALIASES).
  const syntheticEvalVocabulary = useMemo(() => {
    const isText = formData?.agentType === AGENT_TYPES.CHAT;
    const keys = [
      // Agent
      "agent.name",
      "agent.type",
      "agent.provider",
      "agent.model",
      "agent.language",
      "agent.description",
      "agent.contact_number",
      // Persona / simulator agent (derived from selected scenarios)
      "persona.name",
      "persona.prompt",
      "persona.description",
      "persona.voice_name",
      "persona.model",
      "persona.initial_message",
      // Prompt template (prompt-type sims)
      "prompt.name",
      "prompt.description",
      // Scenario row metadata
      "scenario.name",
      "scenario.description",
      "scenario.type",
      "scenario.source",
      // Simulation metadata
      "simulation.name",
      "simulation.type",
      "simulation.call_type",
      // Call-level runtime vocabulary — resolved server-side at eval run time
      "call.transcript",
      "call.agent_prompt",
      "call.summary",
      "call.ended_reason",
      "call.duration_seconds",
      "call.status",
      "call.overall_score",
      "call.phone_number",
      "call.recording_url",
      "call.stereo_recording_url",
      // Modality-specific runtime vocabulary
      ...(isText
        ? ["call.user_chat_transcript", "call.assistant_chat_transcript"]
        : [
            "call.voice_recording",
            "call.stereo_recording",
            "call.assistant_recording",
            "call.customer_recording",
          ]),
    ];
    return keys.map((key) => ({
      field: key,
      headerName: key,
      dataType: "text",
    }));
  }, [formData?.agentType]);

  const evalColumnsToDisplay = useMemo(() => {
    // Dedupe against scenario-column + base columns so a user-named
    // dataset column can't silently collide with a vocabulary key.
    const seen = new Set(
      (evalColumnsWithScenarios || []).map(
        (col) => col.field || col.headerName || col.name,
      ),
    );
    const extras = syntheticEvalVocabulary.filter(
      (col) => !seen.has(col.field),
    );
    return [...(evalColumnsWithScenarios || []), ...extras];
  }, [evalColumnsWithScenarios, syntheticEvalVocabulary]);

  // Build the preview snapshot for create-simulate mode. The flattened
  // shape is consumed by CreateSimulationPreviewMode to render known
  // values + placeholder runtime fields. See
  // CreateSimulationPreviewMode.jsx for the key contract.
  const selectedAgentDef = useMemo(
    () =>
      agentDefinitions?.find((d) => d.id === formData.agentDefinitionId) ||
      null,
    [agentDefinitions, formData.agentDefinitionId],
  );
  const selectedAgentVersion = useMemo(() => {
    if (!selectedAgentDef) return null;
    // Agent definition list response uses snake_case `agent_versions`
    // / `versions`. camelCase aliases are dead post middleware removal.
    const versions =
      selectedAgentDef.versions ?? selectedAgentDef.agent_versions ?? [];
    return (
      versions.find((v) => v.id === formData.agentDefinitionVersionId) || null
    );
  }, [selectedAgentDef, formData.agentDefinitionVersionId]);
  const selectedScenarioDetails = useMemo(() => {
    if (!formData?.selectedScenarios?.length) return [];
    return scenarios.filter((s) => formData.selectedScenarios.includes(s.id));
  }, [scenarios, formData?.selectedScenarios]);

  const sourcePreviewData = useMemo(() => {
    const isText = formData?.agentType === AGENT_TYPES.CHAT;
    // Collapse scenario column configs from all selected scenarios into
    // a single { uuid: { name, type } } dict. The query's columnConfigs
    // is an array of per-scenario entries; each has dataset_column_config.
    const scenarioColumns = {};
    (data?.data?.columnConfigs || []).forEach((detail) => {
      const cfg =
        detail?.dataset_column_config ?? detail?.datasetColumnConfig ?? {};
      Object.entries(cfg).forEach(([uuid, meta]) => {
        scenarioColumns[uuid] = meta;
      });
    });

    // For each selected scenario, collect a compact summary. We show
    // every selected scenario's metadata + persona in the preview so
    // users understand what the vocabulary maps to when the eval runs.
    // The ScenariosSerializer exposes the persona under `agent` (a
    // method field) — not `simulator_agent_detail`.
    const scenarioSummaries = selectedScenarioDetails.map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
      scenario_type: s.scenario_type ?? s.scenarioType,
      source: s.source,
      persona: s.agent ?? null,
      prompt_template:
        s.prompt_template_detail ?? s.promptTemplateDetail ?? null,
    }));

    // First-scenario values power the flat vocabulary keys. The per-
    // scenario breakdown (scenarioSummaries) is surfaced separately by
    // the preview component for visibility.
    const first = scenarioSummaries[0] || null;

    return {
      simCallType: isText ? "text" : "voice",
      sim_call_type: isText ? "text" : "voice",
      simulation_name: formData?.testName || "",
      simulation_type: formData?.sourceType || "agent_definition",
      agent_definition: selectedAgentDef,
      agent_version: selectedAgentVersion,
      simulator_agent: first?.persona || null,
      prompt_template: first?.prompt_template || null,
      scenario_info: first || null,
      scenario_columns: scenarioColumns,
      scenario_summaries: scenarioSummaries,
    };
  }, [
    formData?.agentType,
    formData?.testName,
    formData?.sourceType,
    selectedAgentDef,
    selectedAgentVersion,
    selectedScenarioDetails,
    data?.data?.columnConfigs,
  ]);

  const handleNextStep = () => {
    if (activeStep === 0 && !formData?.testName?.trim()) {
      enqueueSnackbar("Please enter a test name", { variant: "error" });
      return;
    }
    if (
      activeStep === 1 &&
      (!formData?.selectedScenarios || formData.selectedScenarios.length === 0)
    ) {
      enqueueSnackbar("Please select at least one scenario", {
        variant: "error",
      });
      return;
    }
    // if (activeStep === 2 && !formData?.selectedAgent) {
    //   enqueueSnackbar("Please select an agent", { variant: "error" });
    //   return;
    // }
    if (
      activeStep === 2 &&
      (!evaluationsConfig || evaluationsConfig.length === 0)
    ) {
      enqueueSnackbar("Please add at least one evaluation", {
        variant: "error",
      });
      return;
    }
    setCompleted({
      ...completed,
      [activeStep]: true,
    });

    setActiveStep((prev) => prev + 1);
  };

  const handleBack = () => {
    setActiveStep((prev) => prev - 1);
  };

  const handleCompletedStep = (index) => {
    setActiveStep(index);
  };

  // Create test mutation
  // ref is added for a extra safety that extreme buttons clicks
  // on slow devices don't cause multiple api calls happening
  // on slower devices
  const isMutatingRef = useRef(false);
  const createTestMutation = useMutation({
    /**
     *
     * @param {Object} payload
     * @returns
     */
    mutationFn: async (payload) => {
      if (isMutatingRef.current) return;
      isMutatingRef.current = true;
      const response = await axios.post(endpoints.runTests.create, payload);
      return response.data;
    },
    onSettled: () => {
      isMutatingRef.current = false;
    },
    onSuccess: (data) => {
      enqueueSnackbar("Test created successfully!", { variant: "success" });
      onClose();
      navigate(`/dashboard/simulate/test/${data.id}/runs`);
      // Navigate to run tests list page
    },
  });
  const handleSubmit = async () => {
    if (!allStepsCompleted) {
      enqueueSnackbar("Please complete all the steps!");
      return;
    }
    // Get the first agent definition if available
    if (!formData?.agentDefinitionId) {
      enqueueSnackbar("No agent definitions found. Please create one first.", {
        variant: "error",
      });
      return;
    }

    // Prepare evaluation data to send
    const evaluationsConfigData = (evaluationsConfig || []).map(
      (evalConfig) => {
        return {
          name:
            evalConfig.name ||
            evalConfig.evalTemplateName ||
            "Unnamed Evaluation",
          template_id: evalConfig.templateId || evalConfig.id?.split("_")[0], // Extract original template ID
          template_name: evalConfig.evalTemplateName,
          mapping: evalConfig.config?.mapping || {},
          config: evalConfig.config || {},
          description: evalConfig.description || "",
          type: evalConfig.type || "user_built",
          required_keys: evalConfig.evalRequiredKeys || [],
          tags: evalConfig.evalTemplateTags || [],
          error_localizer: evalConfig?.errorLocalizer,
          ...(evalConfig?.model && { model: evalConfig.model }),
          ...(evalConfig?.evalGroup && { eval_group: evalConfig.evalGroup }),
        };
      },
    );

    const payload = {
      name: formData?.testName || "",
      description: formData?.description || "",
      scenario_ids: formData?.selectedScenarios || [],
      agent_definition_id: formData.agentDefinitionId,
      agent_version: formData?.agentDefinitionVersionId,
      eval_config_ids: [], // Keep empty for existing eval configs
      evaluations_config: evaluationsConfigData, // Send evaluation data to create CustomEvalConfig instances
      dataset_row_ids: [], // Empty for now as not implemented in UI
      enable_tool_evaluation: formData?.enableToolEvaluation || false,
    };

    // Submit the payload
    trackEvent(Events.runTestCreateTestClicked, {
      [PropertyName.formFields]: payload,
    });
    createTestMutation.mutate(payload);
  };

  const canProceed = () => {
    switch (activeStep) {
      case 0:
        return (
          formData?.testName?.trim() !== "" &&
          formData?.agentDefinitionId !== "" &&
          formData?.agentDefinitionVersionId !== ""
        );
      case 1:
        return formData?.selectedScenarios?.length > 0;
      // case 2:
      //   return formData?.selectedAgent !== "";
      case 2:
        return evaluationsConfig?.length > 0;
      case 3:
        return true;
      default:
        return false;
    }
  };

  const isStepValid = (step) => {
    switch (step) {
      case 0:
        return (
          formData?.testName?.trim() !== "" &&
          formData?.agentDefinitionId !== ""
        );
      case 1:
        return formData?.selectedScenarios?.length > 0;
      case 2:
        return formData?.selectedAgent !== "";
      case 3:
        return evaluationsConfig?.length > 0;
      case 4:
        return true;
      default:
        return false;
    }
  };

  const handleScenarioToggle = (scenarioId) => {
    const isSelected = formData.selectedScenarios.includes(scenarioId);
    setFormData({
      ...formData,
      selectedScenarios: isSelected
        ? formData.selectedScenarios.filter((id) => id !== scenarioId)
        : [...formData.selectedScenarios, scenarioId],
    });
  };

  // Transform the new EvalPickerDrawer's camelCase payload into the legacy
  // `evaluationsConfig` shape expected by the existing render + final POST.
  // Keeps the rest of CreateRunTestPage untouched.
  const toLegacyEvalShape = (evalConfig, versionedName) => ({
    evalId: `${evalConfig.templateId}_${Date.now()}_${Math.random()
      .toString(36)
      .slice(2, 8)}`,
    templateId: evalConfig.templateId,
    name: versionedName,
    evalTemplateName: evalConfig.evalTemplate?.name || evalConfig.name,
    description: evalConfig.evalTemplate?.description || "",
    type: "user_built",
    config: {
      ...(evalConfig.config || {}),
      mapping: evalConfig.mapping || {},
      ...(evalConfig.instructions != null && {
        instructions: evalConfig.instructions,
      }),
      ...(evalConfig.passThreshold != null && {
        pass_threshold: evalConfig.passThreshold,
      }),
      ...(evalConfig.choiceScores &&
        Object.keys(evalConfig.choiceScores).length > 0 && {
          choice_scores: evalConfig.choiceScores,
        }),
    },
    model: evalConfig.model,
    evalRequiredKeys:
      evalConfig.evalTemplate?.required_keys ||
      evalConfig.evalTemplate?.requiredKeys ||
      [],
    evalTemplateTags: evalConfig.evalTemplate?.tags || [],
    errorLocalizer: false,
  });

  const handleAddEvaluation = async (evalConfig) => {
    // Capture edit-target before we close the drawer and clear state.
    const editingId = editingEvalItem?.evalId || null;

    setEvaluationsConfig((prev) => {
      let updated = [...prev];
      if (editingId) {
        updated = updated.filter((item) => item.evalId !== editingId);
      }
      const versionedName = getVersionedEvalName(
        evalConfig.name,
        updated,
        evalConfig.templateId,
      );
      return [...updated, toLegacyEvalShape(evalConfig, versionedName)];
    });

    setOpenEvaluationDialog(false);
    setEditingEvalItem(null);
  };

  const handleRemoveEvaluation = (id, isGroupEval) => {
    setEvaluationsConfig((prev) =>
      prev.filter((item) => {
        const targetId = item.evalId;
        return targetId !== id;
      }),
    );
  };

  const {
    data: agentDefVersions,
    fetchNextPage: fetchNextAgentVersions,
    isLoading: agentDefVersionsLoading,
    isFetchingNextPage: isFetchingAgentVersionsNextPage,
  } = useAgentDefinitionVersions({
    selectedAgentId: formData?.agentDefinitionId,
  });
  const versionOptions = useMemo(() => {
    return agentDefVersions?.pages?.reduce((acc, curr) => {
      const newOptions =
        curr.results?.map((result) => ({
          label: result.versionNameDisplay,
          value: result.id,
        })) ?? [];
      return [...acc, ...newOptions];
    }, []);
  }, [agentDefVersions]);

  const { data: agentVersionDetails } = useQuery({
    queryKey: [
      "agentVersionDetail",
      formData?.agentDefinitionId,
      formData?.agentDefinitionVersionId,
    ],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.agentDefinitions.versionDetail(
          formData?.agentDefinitionId,
          formData?.agentDefinitionVersionId,
        ),
      );
      return res.data;
    },
    enabled: Boolean(
      formData?.agentDefinitionId && formData?.agentDefinitionVersionId,
    ),
  });

  // Whether the agent version has the credentials required to run tool-call
  // evaluation. vapi/retell agents use api_key + assistant_id. LiveKit agents
  // use livekit_url + livekit_api_key + livekit_api_secret + livekit_agent_name
  // (none of which live under configurationSnapshot.apiKey/assistantId).
  // Without this branch, LiveKit agents hit a false "missing credentials"
  // gate and can't enable tool-call eval at all. [TH-4130]
  /** @param {Record<string, any> | null | undefined} snapshot */
  const hasToolCallCredentials = (snapshot) => {
    if (!snapshot) return false;
    if (isLiveKitProvider(snapshot.provider)) {
      return Boolean(
        snapshot.livekitUrl &&
          snapshot.livekitApiKey &&
          snapshot.livekitApiSecret &&
          snapshot.livekitAgentName,
      );
    }
    return Boolean(snapshot.apiKey && snapshot.assistantId);
  };

  useEffect(() => {
    if (agentVersionDetails && formData?.enableToolEvaluation) {
      const snapshot =
        agentVersionDetails?.configuration_snapshot ??
        agentVersionDetails?.configurationSnapshot;
      const vapiApiKey = snapshot?.api_key ?? snapshot?.apiKey;
      const vapiAssistantId = snapshot?.assistant_id ?? snapshot?.assistantId;

      if (
        !hasToolCallCredentials(snapshot) &&
        formData?.agentType !== AGENT_TYPES.CHAT
      ) {
        setFormData((prev) => ({
          ...prev,
          enableToolEvaluation: false,
        }));
      }
    }
  }, [setFormData, agentVersionDetails, formData?.enableToolEvaluation]);

  const onToggleToolCallCheck = (e) => {
    const value = e.target.checked;
    if (formData?.agentType === AGENT_TYPES.CHAT) {
      setFormData((prev) => ({
        ...prev,
        enableToolEvaluation: value,
      }));
      // Chat agents have no Vapi api_key / assistant_id on the
      // configuration_snapshot, so falling through to the voice-credential
      // check below opened the "Update Keys" dialog for every chat sim
      return;
    } else {
      if (!agentVersionDetails) {
        enqueueSnackbar("There was error getting agent version details", {
          variant: "error",
        });
        return;
      }
      const snapshot =
        agentVersionDetails?.configuration_snapshot ??
        agentVersionDetails?.configurationSnapshot;
      const vapiApiKey = snapshot?.api_key ?? snapshot?.apiKey;
      const vapiAssistantId = snapshot?.assistant_id ?? snapshot?.assistantId;
      if ((!vapiApiKey || !vapiAssistantId) && value) {
        setOpenUpdateKeysDialog(true);
        return;
      }
      setFormData((prev) => ({
        ...prev,
        enableToolEvaluation: value,
      }));
    }
    if (!agentVersionDetails) {
      enqueueSnackbar("There was error getting agent version details", {
        variant: "error",
      });
      return;
    }
    const snapshot = agentVersionDetails?.configurationSnapshot;
    if (!hasToolCallCredentials(snapshot) && value) {
      setOpenUpdateKeysDialog(true);
      return;
    }
    setFormData((prev) => ({
      ...prev,
      enableToolEvaluation: value,
    }));
  };
  const handleEditEvalItem = (evalConfig) => {
    // Pass through to the new picker via `initialEval`: it only needs `.id`
    // (the eval template id) to load the full template at the config step.
    setEditingEvalItem(evalConfig);
    setOpenEvaluationDialog(true);
  };
  useEffect(() => {
    if (versionOptions?.length > 0) {
      setFormData((prev) => ({
        ...prev,
        agentDefinitionVersionId: versionOptions[0]?.value,
      }));
    }
  }, [versionOptions]);

  const renderStepContent = () => {
    switch (activeStep) {
      case 0:
        return (
          <Box>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              align="center"
              sx={{ mb: 1 }}
            >
              Add simulation details
            </Typography>
            <Box
              display="flex"
              gap="4px"
              alignItems="center"
              justifyContent="center"
              sx={{ mb: 3 }}
            >
              <Typography
                variant="body2"
                color="text.primary"
                align="center"
                sx={{ color: "text.primary" }}
              >
                Set up basic details to create your simulation
              </Typography>
            </Box>

            <Box
              sx={{
                display: "flex",
                gap: 3,
                alignItems: "center",
                flexDirection: "column",
              }}
            >
              <TextField
                required
                label={"Simulation name"}
                fullWidth
                size="small"
                placeholder="Enter a name for your simulation run (eg: Sales agent performance test)"
                value={formData.testName}
                onChange={(e) =>
                  setFormData({ ...formData, testName: e.target.value })
                }
                sx={{
                  "& .MuiOutlinedInput-root": {
                    height: "40px",
                  },
                }}
              />
              <Stack
                direction={"row"}
                gap={2}
                sx={{
                  width: "100%",
                }}
              >
                <FormSearchSelectFieldState
                  label={"Choose Agent definition"}
                  fullWidth
                  placeholder={"Choose your agent that you want to test"}
                  value={formData.agentDefinitionId}
                  size="small"
                  options={
                    agentDefinitionsLoading
                      ? []
                      : agentDefinitions?.map((agent) => ({
                          label: agent?.agentName,
                          value: agent?.id,
                          type: agent?.agentType,
                          component: (
                            <Box
                              sx={{
                                display: "flex",
                                flexDirection: "row",
                                alignItems: "center",
                                gap: 1,
                                padding: 0.5,
                              }}
                            >
                              <SvgColor
                                sx={{ width: 18 }}
                                src={getIconForAgentDefinitions(
                                  agent?.agentType,
                                )}
                              />
                              <Typography variant="s2_1">
                                {agent?.agentName}
                              </Typography>
                            </Box>
                          ),
                        }))
                  }
                  onChange={(e) =>
                    setFormData((p) => ({
                      ...p,
                      agentDefinitionId: e.target.value,
                      agentType: e?.target?.option?.type,
                      agentDefinitionVersionId: "",
                    }))
                  }
                  emptyMessage={"You have not created any agent definition"}
                  handleCreateLabel={() =>
                    router.push("/dashboard/simulate/agent-definitions")
                  }
                  createLabel={"Define new agent"}
                  required
                  onScrollEnd={fetchNextPage}
                  isFetchingNextPage={fetchNextAgentDefs}
                />
                <CustomTooltip
                  arrow
                  show={!formData?.agentDefinitionId}
                  title={"Choose an agent definition."}
                  type={undefined}
                >
                  <FormSearchSelectFieldState
                    disabled={
                      !formData?.agentDefinitionId || agentDefVersionsLoading
                    }
                    label={"Choose version"}
                    value={formData.agentDefinitionVersionId}
                    size="small"
                    placeholder={
                      !versionOptions && agentDefVersionsLoading
                        ? "Loading versions..."
                        : "Select agent version (eg: v2)"
                    }
                    options={!versionOptions ? [] : versionOptions}
                    onChange={(e) =>
                      setFormData((p) => ({
                        ...p,
                        agentDefinitionVersionId: e.target.value,
                      }))
                    }
                    required
                    sx={{
                      width: "300px",
                    }}
                    onScrollEnd={fetchNextAgentVersions}
                    isFetchingNextPage={isFetchingAgentVersionsNextPage}
                  />
                </CustomTooltip>
              </Stack>
              <TextField
                label={"Description"}
                fullWidth
                multiline
                rows={6}
                size="small"
                placeholder="Describe what this simulation run will evaluate (eg: Testing our insurance sales agent's ability to handle diverse customer profiles, with focus on objection handling and conversion rates)"
                value={formData.description}
                onChange={(e) =>
                  setFormData({ ...formData, description: e.target.value })
                }
                sx={{
                  "& .MuiOutlinedInput-root": {
                    fontSize: "14px",
                  },
                }}
              />
            </Box>
          </Box>
        );

      case 1:
        return (
          <>
            {filteredScenarios.length === 0 && debouncedSearch === "" ? (
              <EmptyLayout
                title="Add your first scenario"
                description="Create scenarios and experiments to evaluate your application across different test cases and conditions."
                link="https://docs.futureagi.com/docs/simulation/concepts/scenarios"
                linkText="Check docs"
                action={
                  <Button
                    variant="contained"
                    color="primary"
                    sx={{
                      px: "24px",
                      borderRadius: "8px",
                      height: "38px",
                    }}
                    startIcon={
                      <Iconify
                        icon="octicon:plus-24"
                        color="background.paper"
                        sx={{
                          width: "20px",
                          height: "20px",
                        }}
                      />
                    }
                    onClick={() =>
                      router.push("/dashboard/simulate/scenarios/create")
                    }
                  >
                    <Typography
                      typography="s1"
                      fontWeight={"fontWeightSemiBold"}
                    >
                      Add Scenario
                    </Typography>
                  </Button>
                }
                icon="/assets/icons/navbar/hugeicons.svg"
              />
            ) : (
              <Box>
                <Typography
                  typography="m3"
                  fontWeight="fontWeightMedium"
                  align="center"
                  sx={{ mb: 1 }}
                >
                  Choose your scenarios
                </Typography>
                <Box
                  display="flex"
                  gap="4px"
                  alignItems="center"
                  justifyContent="center"
                  sx={{ mb: 3 }}
                >
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    align="center"
                    sx={{ color: "text.primary" }}
                  >
                    Choose scenarios that your agent will be tested against
                  </Typography>
                </Box>

                {/* Search Bar */}
                <Box sx={{ mb: 3 }}>
                  <TextField
                    fullWidth
                    size="small"
                    placeholder="Search scenarios..."
                    value={scenarioSearch}
                    onChange={(e) => setScenarioSearch(e.target.value)}
                    InputProps={{
                      startAdornment: (
                        <Box sx={{ mr: 1 }}>
                          <Iconify
                            icon="eva:search-fill"
                            width={20}
                            sx={{ color: "text.disabled" }}
                          />
                        </Box>
                      ),
                    }}
                  />
                </Box>

                {/* Scenarios List */}
                {isLoadingScenarios ? (
                  <Box
                    sx={{ display: "flex", justifyContent: "center", py: 4 }}
                  >
                    <CircularProgress />
                  </Box>
                ) : scenariosError ? (
                  <Box sx={{ textAlign: "center", py: 4 }}>
                    <Typography color="error">
                      Failed to load scenarios
                    </Typography>
                  </Box>
                ) : (
                  <>
                    <List sx={{ width: "100%", bgcolor: "background.paper" }}>
                      {filteredScenarios.map((scenario) => (
                        <ListItem
                          key={scenario.id}
                          sx={{
                            border: "1px solid",
                            borderColor: formData.selectedScenarios.includes(
                              scenario.id,
                            )
                              ? "primary.main"
                              : "divider",
                            borderRadius: 1,
                            mb: 1.5,
                            px: 2,
                            py: 1.5,
                            cursor: "pointer",
                            "&:hover": {
                              borderColor: "primary.lighter",
                              bgcolor: alpha(
                                theme.palette.primary["lighter"],
                                0.12,
                              ),
                            },
                          }}
                          onClick={() => {
                            handleScenarioToggle(scenario.id);
                          }}
                        >
                          <ListItemIcon sx={{ minWidth: 40 }}>
                            <Checkbox
                              edge="start"
                              checked={formData.selectedScenarios.includes(
                                scenario.id,
                              )}
                              tabIndex={-1}
                              disableRipple
                            />
                          </ListItemIcon>
                          <ListItemText
                            primary={
                              <Typography
                                variant="subtitle2"
                                sx={{
                                  display: "-webkit-box",
                                  WebkitLineClamp: 1,
                                  WebkitBoxOrient: "vertical",
                                  overflow: "hidden",
                                }}
                              >
                                {scenario.name}
                              </Typography>
                            }
                            secondary={
                              <Typography
                                variant="body2"
                                color="text.secondary"
                                sx={{
                                  display: "-webkit-box",
                                  WebkitLineClamp: 2,
                                  WebkitBoxOrient: "vertical",
                                  overflow: "hidden",
                                }}
                              >
                                {scenario.description ||
                                  scenario.source ||
                                  "No description available"}
                              </Typography>
                            }
                          />
                          <Box
                            sx={{
                              display: "flex",
                              flexDirection: "column",
                              alignItems: "flex-end",
                              gap: 0.5,
                            }}
                          >
                            {scenario.scenarioType === "dataset" && (
                              <Chip
                                label="Dataset"
                                size="small"
                                sx={{
                                  height: "20px",
                                  fontSize: "11px",
                                }}
                              />
                            )}
                            <Typography variant="body2" fontWeight={600}>
                              {scenario.datasetRows || 0}
                            </Typography>
                          </Box>
                        </ListItem>
                      ))}
                    </List>

                    {/* Pagination */}
                    {scenariosTotalPages > 1 && (
                      <Box
                        sx={{
                          display: "flex",
                          justifyContent: "center",
                          mt: 3,
                        }}
                      >
                        <TablePagination
                          component="div"
                          count={scenariosTotal}
                          page={scenariosPage - 1}
                          onPageChange={(event, newPage) =>
                            setScenariosPagination((prev) => ({
                              ...prev,
                              page: newPage + 1,
                            }))
                          }
                          rowsPerPage={scenariosPagination.pageSize}
                          onRowsPerPageChange={(event) => {
                            setScenariosPagination({
                              page: 1,
                              pageSize: parseInt(event.target.value, 10),
                            });
                          }}
                        />
                      </Box>
                    )}
                  </>
                )}
              </Box>
            )}
          </>
        );

      // case 2:
      //   return (
      //     <>
      //       {filteredAgents.length === 0 && debouncedAgentSearch === "" ? (
      //         <EmptyLayout
      //           title="Add your first simulator agent"
      //           description="Create simulator agents to handle voice conversations with custom prompts and voice settings."
      //           link="https://docs.futureagi.com"
      //           linkText="Check docs"
      //           action={
      //             <Button
      //               variant="contained"
      //               color="primary"
      //               sx={{
      //                 px: "24px",
      //                 borderRadius: "8px",
      //                 height: "38px",
      //               }}
      //               startIcon={
      //                 <Iconify
      //                   icon="octicon:plus-24"
      //                   color="background.paper"
      //                   sx={{
      //                     width: "20px",
      //                     height: "20px",
      //                   }}
      //                 />
      //               }
      //               onClick={() =>
      //                 router.push("/dashboard/simulate/simulator-agent")
      //               }
      //             >
      //               <Typography
      //                 typography="s1"
      //                 fontWeight={"fontWeightSemiBold"}
      //               >
      //                 Add Simulator Agent
      //               </Typography>
      //             </Button>
      //           }
      //           icon="/assets/icons/navbar/hugeicons.svg"
      //         />
      //       ) : (
      //         <Box>
      //           <Typography variant="h6" align="center" sx={{ mb: 1 }}>
      //             Select Test Agent
      //           </Typography>
      //           <Typography
      //             variant="body2"
      //             color="text.secondary"
      //             align="center"
      //             sx={{ mb: 4 }}
      //           >
      //             Choose an AI agent to run the test scenarios
      //           </Typography>

      //           {/* Search Bar */}
      //           <Box sx={{ mb: 3 }}>
      //             <TextField
      //               fullWidth
      //               size="small"
      //               placeholder="Search agents..."
      //               value={agentSearch}
      //               onChange={(e) => setAgentSearch(e.target.value)}
      //               InputProps={{
      //                 startAdornment: (
      //                   <Box sx={{ mr: 1 }}>
      //                     <Iconify
      //                       icon="eva:search-fill"
      //                       width={20}
      //                       sx={{ color: "text.disabled" }}
      //                     />
      //                   </Box>
      //                 ),
      //               }}
      //             />
      //           </Box>

      //           {/* Agents List */}
      //           {isLoadingAgents ? (
      //             <Box
      //               sx={{ display: "flex", justifyContent: "center", py: 4 }}
      //             >
      //               <CircularProgress />
      //             </Box>
      //           ) : agentsError ? (
      //             <Box sx={{ textAlign: "center", py: 4 }}>
      //               <Typography color="error">Failed to load agents</Typography>
      //             </Box>
      //           ) : (
      //             <>
      //               <Box
      //                 sx={{ display: "flex", flexDirection: "column", gap: 2 }}
      //               >
      //                 {filteredAgents.map((agent) => (
      //                   <SelectionCard
      //                     key={agent.id}
      //                     selected={formData.selectedAgent === agent.id}
      //                     onClick={() =>
      //                       setFormData({
      //                         ...formData,
      //                         selectedAgent: agent.id,
      //                       })
      //                     }
      //                   >
      //                     <Box
      //                       sx={{
      //                         display: "flex",
      //                         alignItems: "center",
      //                         gap: 2,
      //                       }}
      //                     >
      //                       <Radio
      //                         checked={formData.selectedAgent === agent.id}
      //                       />
      //                       <Box sx={{ flex: 1 }}>
      //                         <Typography variant="subtitle2">
      //                           {agent.name}
      //                         </Typography>
      //                       </Box>
      //                     </Box>
      //                   </SelectionCard>
      //                 ))}
      //               </Box>

      //               {/* Pagination */}
      //               {agentsTotalPages > 1 && (
      //                 <Box
      //                   sx={{
      //                     display: "flex",
      //                     justifyContent: "center",
      //                     mt: 3,
      //                   }}
      //                 >
      //                   <TablePagination
      //                     component="div"
      //                     count={agentsTotal}
      //                     page={agentsPage - 1}
      //                     onPageChange={(event, newPage) =>
      //                       setAgentsPagination((prev) => ({
      //                         ...prev,
      //                         page: newPage + 1,
      //                       }))
      //                     }
      //                     rowsPerPage={agentsPagination.pageSize}
      //                     onRowsPerPageChange={(event) => {
      //                       setAgentsPagination({
      //                         page: 1,
      //                         pageSize: parseInt(event.target.value, 10),
      //                       });
      //                     }}
      //                   />
      //                 </Box>
      //               )}
      //             </>
      //           )}
      //         </Box>
      //       )}
      //     </>
      //   );

      case 2: // Select Evaluations
        return (
          <Box>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              align="center"
              sx={{ mb: 1 }}
            >
              Select evaluations
            </Typography>
            <Box
              display="flex"
              gap="4px"
              alignItems="center"
              justifyContent="center"
              sx={{ mb: 3 }}
            >
              <Typography
                variant="body2"
                color="text.secondary"
                align="center"
                sx={{ color: "text.primary" }}
              >
                Apply evaluation metrics on your agents to measure its
                performance
              </Typography>
              {/* <Link
                href="https://docs.futureagi.com/docs/simulation/run-tests"
                color="blue.500"
                target="_blank"
                rel="noopener noreferrer"
                fontWeight="fontWeightMedium"
                fontSize="14px"
                sx={{
                  textDecoration: "underline",
                }}
              >
                Learn more
              </Link> */}
            </Box>
            <Box
              sx={{
                mb: 3,
                p: 1.5,
                py: 0.5,
                borderRadius: 1,
                // border: "1px solid #F49A54",
                display: "flex",
                flexDirection: "row",
                alignItems: "center",

                backgroundColor: "blue.o5",
              }}
            >
              <SvgColor
                src="/assets/icons/ic_info.svg"
                sx={{
                  color: "blue.500",
                  width: theme.spacing(2),
                  height: theme.spacing(2),
                }}
              />
              <Typography
                typography="s1"
                color="text.primary"
                sx={{
                  display: "block",
                  textAlign: "center",
                  p: 1,
                }}
              >
                Selected evaluations will be created and linked to this
                simulation run.
              </Typography>
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                justifyContent: "space-between",
                alignItems: "center",
                border: "1px solid",
                borderRadius: 0.5,
                borderColor: "divider",
                paddingY: 1.5,
                paddingX: 2,
                mb: 3,
              }}
            >
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  Enable tool call evaluation
                </Typography>
                <Typography
                  variant="s2_1"
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  Tool calling that happens during the calls will be evaluated
                </Typography>
              </Box>

              <FormControlLabel
                control={
                  <IOSSwitch
                    checked={formData.enableToolEvaluation}
                    onChange={onToggleToolCallCheck}
                    sx={{ m: 1 }}
                  />
                }
              />
            </Box>

            {/* Add Evaluations Button */}
            {evaluationsConfig.length === 0 ? (
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  py: 8,
                  border: "1px dashed",
                  borderColor: "primary.lighter",
                  borderRadius: 1,
                }}
              >
                <Typography
                  typography="m3"
                  fontWeight={"500"}
                  color="text.secondary"
                  sx={{ mb: 2 }}
                >
                  No evaluations added yet
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<Iconify icon="eva:plus-fill" />}
                  onClick={() => {
                    setEditingEvalItem(null);
                    setOpenEvaluationDialog(true);
                  }}
                  sx={{
                    backgroundColor: "primary.main",
                    color: "primary.contrastText",
                    "&:hover": {
                      backgroundColor: "primary.dark",
                    },
                  }}
                >
                  Add Evaluations
                </Button>
              </Box>
            ) : (
              <>
                {/* Selected Evaluations List */}
                <Box sx={{ mb: 2 }}>
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      mb: 2,
                    }}
                  >
                    <Typography variant="subtitle2">
                      Selected Evaluations ({evaluationsConfig.length})
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => {
                        setEditingEvalItem(null);
                        setOpenEvaluationDialog(true);
                      }}
                      startIcon={
                        <SvgColor
                          src="/assets/icons/ic_add.svg"
                          sx={{ color: "inherit" }}
                        />
                      }
                    >
                      Add More
                    </Button>
                  </Box>

                  <Box
                    sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
                  >
                    {evaluationsConfig.map((evalItem) => (
                      <Paper
                        key={evalItem.evalId}
                        sx={{
                          p: 2,
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: 1,
                        }}
                      >
                        <Box
                          sx={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "flex-start",
                          }}
                        >
                          <Box sx={{ flex: 1 }}>
                            <Typography variant="subtitle2">
                              {evalItem.name}
                            </Typography>
                            {evalItem.description && (
                              <Typography
                                variant="body2"
                                color="text.secondary"
                                sx={{ mt: 0.5 }}
                              >
                                {evalItem.description}
                              </Typography>
                            )}
                            <Box
                              sx={{
                                mt: 1,
                                display: "flex",
                                gap: 1,
                                flexWrap: "wrap",
                              }}
                            >
                              <ShowComponent condition={!!evalItem?.groupName}>
                                <Chip
                                  label={`Group name - ${evalItem?.groupName}.`}
                                  size="small"
                                  sx={{
                                    height: "24px",
                                    backgroundColor: "background.neutral",
                                    borderColor: "divider",
                                    fontSize: "11px",
                                    borderRadius: "2px",
                                    paddingX: "12px",
                                    lineHeight: "16px",
                                    fontWeight: 400,
                                    color: "text.primary",
                                    "& .MuiChip-label": {
                                      padding: 0,
                                    },
                                    ".MuiChip-icon ": {
                                      marginRight: "6px",
                                    },
                                    "&:hover": {
                                      backgroundColor: "background.neutral",
                                      borderColor: "divider",
                                    },
                                  }}
                                  icon={
                                    <SvgColor
                                      src="/assets/icons/ic_dashed_square.svg"
                                      sx={{ width: 16, height: 16, mr: 1 }}
                                      style={{
                                        color: theme.palette.text.primary,
                                      }}
                                    />
                                  }
                                />
                              </ShowComponent>
                              {evalItem.config?.mapping &&
                                Object.entries(evalItem.config.mapping).map(
                                  ([key, value]) => {
                                    let label = value;

                                    if (isUUID(value)) {
                                      const match = evalColumnsToDisplay.find(
                                        (col) => col.field === label,
                                      );

                                      if (match) {
                                        label = match.headerName;
                                      }
                                    }

                                    return (
                                      <Chip
                                        key={key}
                                        label={`${key}: ${label}`}
                                        size="small"
                                        variant="outlined"
                                      />
                                    );
                                  },
                                )}
                            </Box>
                          </Box>
                          <IconButton
                            size="small"
                            onClick={() => handleEditEvalItem(evalItem)}
                            sx={{
                              ml: 1,
                              border: "1px solid",
                              borderColor: "divider",
                              borderRadius: "2px",
                              color: "text.disabled",
                            }}
                          >
                            <SvgColor
                              src={`/assets/icons/ic_edit.svg`}
                              sx={{
                                width: 16,
                                height: 16,
                              }}
                            />
                          </IconButton>
                          <IconButton
                            size="small"
                            onClick={() =>
                              handleRemoveEvaluation(
                                evalItem.evalId,
                                !!evalItem?.groupName,
                              )
                            }
                            sx={{
                              ml: 1,
                              border: "1px solid",
                              borderColor: "divider",
                              borderRadius: "2px",
                              color: "text.disabled",
                            }}
                          >
                            <SvgColor
                              src="/assets/icons/ic_delete.svg"
                              sx={{
                                height: 16,
                                width: 16,
                              }}
                            />
                          </IconButton>
                        </Box>
                      </Paper>
                    ))}
                  </Box>
                </Box>
              </>
            )}
            <UpdateKeysDialog
              open={openUpdateKeysDialog}
              onComplete={(createVersionResponse) => {
                if (createVersionResponse) {
                  setFormData((prev) => ({
                    ...prev,
                    enableToolEvaluation: true,
                    agentDefinitionVersionId:
                      createVersionResponse?.data?.version?.id,
                  }));
                  queryClient.invalidateQueries({
                    queryKey: [
                      "agent-definition-versions",
                      formData?.agentDefinitionId,
                    ],
                  });
                }
                setOpenUpdateKeysDialog(false);
              }}
              onClose={() => setOpenUpdateKeysDialog(false)}
              agentDetails={agentVersionDetails}
              agentDefinitionId={agentVersionDetails?.agentDefinition}
            />
          </Box>
        );

      case 3:
        return (
          <Box>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              align="center"
              sx={{ mb: 1 }}
            >
              Summary
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              align="center"
              sx={{ mb: 4, color: "text.primary" }}
            >
              Review your simulation configuration before creating it
            </Typography>

            <Box sx={{ maxWidth: 800, mx: "auto" }}>
              {/* Test Configuration Section */}
              <Paper
                sx={{
                  p: 3,
                  mb: 3,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
                  <SvgColor
                    src={"/icons/runTest/ic_settings.svg"}
                    sx={{ mr: 2, width: 24 }}
                  />
                  <Typography typography="m3" fontWeight={"fontWeightBold"}>
                    Test Configuration
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <Box>
                    <Typography variant="subtitle2" color="text.secondary">
                      Test Name
                    </Typography>
                    <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                      {formData.testName}
                    </Typography>
                  </Box>
                  <Box>
                    <Typography variant="subtitle2" color="text.secondary">
                      Agent Definition
                    </Typography>
                    <Typography typography="s1" fontWeight={500}>
                      {
                        agentDefinitions.filter(
                          (definition) =>
                            definition.id === formData.agentDefinitionId,
                        )[0]?.agentName
                      }
                      &nbsp; (
                      {versionOptions.find(
                        (definition) =>
                          definition?.value ===
                          formData?.agentDefinitionVersionId,
                      )?.label || "-"}
                      )
                    </Typography>
                  </Box>
                  {formData.description && (
                    <Box>
                      <Typography variant="subtitle2" color="text.secondary">
                        Description
                      </Typography>
                      <Typography variant="body1">
                        {formData.description}
                      </Typography>
                    </Box>
                  )}
                </Box>
              </Paper>

              {/* Test Scenarios Section */}
              <Paper
                sx={{
                  p: 3,
                  mb: 3,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
                  <SvgColor
                    src={"/icons/runTest/ic_workflow.svg"}
                    sx={{ mr: 2, width: 24 }}
                  />
                  <Typography typography="m3" fontWeight={"fontWeightBold"}>
                    Selected Test Scenarios
                  </Typography>
                </Box>
                <Box sx={{ pl: 4 }}>
                  <Typography typography="s1" fontWeight={500} sx={{ mb: 2 }}>
                    {formData.selectedScenarios.length} scenario(s) selected
                  </Typography>
                  <Box
                    sx={{ display: "flex", flexDirection: "column", gap: 1 }}
                  >
                    {formData.selectedScenarios.map((scenarioId) => {
                      const scenario = scenarios.find(
                        (s) => s.id === scenarioId,
                      );
                      return scenario ? (
                        <Box
                          key={scenarioId}
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: 2,
                            p: 1.5,
                            backgroundColor: "background.default",
                            borderRadius: 1,
                          }}
                        >
                          <Box sx={{ flex: 1 }}>
                            <Typography variant="subtitle2">
                              {scenario.name}
                            </Typography>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                              }}
                            >
                              {scenario.description ||
                                scenario.source ||
                                "No description"}
                            </Typography>
                          </Box>
                          <Typography variant="body2" fontWeight={600}>
                            {scenario.datasetRows || 0} rows
                          </Typography>
                        </Box>
                      ) : null;
                    })}
                  </Box>
                </Box>
              </Paper>

              {/* Test Agent Section */}
              {/* <Paper
                sx={{
                  p: 3,
                  mb: 3,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
                  <SvgColor
                    src={"/icons/runTest/ic_user.svg"}
                    sx={{ mr: 2, width: 24 }}
                  />
                  <Typography variant="m3" fontWeight={"fontWeightBold"}>
                    Selected Test Agent
                  </Typography>
                </Box>
                <Box sx={{ pl: 4 }}>
                  {(() => {
                    const selectedAgent = agents.find(
                      (agent) => agent.id === formData.selectedAgent,
                    );
                    return selectedAgent ? (
                      <Box
                        sx={{
                          p: 1.5,
                               backgroundColor: theme.palette.mode === "light" ? "whiteScale.100" : "background.default",
                          borderRadius: 1,
                        }}
                      >
                        <Typography variant="subtitle2" fontWeight={500}>
                          {selectedAgent.name}
                        </Typography>
                        {selectedAgent.description && (
                          <Typography variant="body2" color="text.secondary">
                            {selectedAgent.description}
                          </Typography>
                        )}
                      </Box>
                    ) : (
                      <Typography variant="body1" color="text.secondary">
                        No agent selected
                      </Typography>
                    );
                  })()}
                </Box>
              </Paper> */}

              {/* Evaluations Section */}
              <Paper
                sx={{
                  p: 3,
                  mb: 3,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
                  <SvgColor
                    src={"/icons/runTest/ic_shield.svg"}
                    sx={{ mr: 2, width: 24 }}
                  />
                  <Typography typography="m3" fontWeight={"fontWeightBold"}>
                    Selected Evaluations
                  </Typography>
                </Box>
                <Box sx={{ pl: 4 }}>
                  <Typography typography="s1" fontWeight={500} sx={{ mb: 2 }}>
                    {evaluationsConfig.length} evaluation(s) selected
                  </Typography>
                  <Box
                    sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
                  >
                    {evaluationsConfig.map((evalItem) => (
                      <Box
                        key={evalItem.id}
                        sx={{
                          p: 1.5,
                          backgroundColor: "background.default",
                          borderRadius: 1,
                        }}
                      >
                        <Typography variant="subtitle2" fontWeight={500}>
                          {evalItem.name}
                        </Typography>
                        {evalItem.description && (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mt: 0.5 }}
                          >
                            {evalItem.description}
                          </Typography>
                        )}
                        <Box
                          sx={{
                            mt: 1,
                            display: "flex",
                            gap: 1,
                            flexWrap: "wrap",
                          }}
                        >
                          <ShowComponent condition={!!evalItem?.groupName}>
                            <Chip
                              label={`Group name - ${evalItem?.groupName}.`}
                              size="small"
                              sx={{
                                height: "24px",
                                backgroundColor: "background.neutral",
                                borderColor: "divider",
                                fontSize: "11px",
                                borderRadius: "2px",
                                paddingX: "12px",
                                lineHeight: "16px",
                                fontWeight: 400,
                                color: "text.primary",
                                "& .MuiChip-label": {
                                  padding: 0,
                                },
                                ".MuiChip-icon ": {
                                  marginRight: "6px",
                                },
                                "&:hover": {
                                  backgroundColor: "background.neutral",
                                  borderColor: "divider",
                                },
                              }}
                              icon={
                                <SvgColor
                                  src="/assets/icons/ic_dashed_square.svg"
                                  sx={{ width: 16, height: 16, mr: 1 }}
                                  style={{ color: theme.palette.text.primary }}
                                />
                              }
                            />
                          </ShowComponent>
                          {evalItem.config?.mapping &&
                            Object.entries(evalItem.config.mapping).map(
                              ([key, value]) => (
                                <Chip
                                  key={key}
                                  label={`${key}: ${value}`}
                                  size="small"
                                  variant="outlined"
                                />
                              ),
                            )}
                        </Box>
                      </Box>
                    ))}
                  </Box>
                </Box>
              </Paper>
            </Box>
          </Box>
        );

      default:
        return null;
    }
  };

  return (
    <>
      {open && (
        <Box
          sx={{
            height: "100vh",
            backgroundColor: "background.default",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Header with Close Button */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              p: 2,
              borderColor: "divider",
              backgroundColor: "background.paper",
            }}
          >
            <Box>
              <Typography
                color="text.primary"
                typography="m2"
                fontWeight={"fontWeightSemiBold"}
              >
                Run Simulation
              </Typography>
              <Typography
                typography="s1"
                color="text.secondary"
                fontWeight={"fontWeightRegular"}
              >
                Create and manage comprehensive tests for your AI agents with
                scenarios, evaluations, and automated runs.
              </Typography>
            </Box>

            <IconButton
              onClick={onClose}
              sx={{
                bgcolor: "background.default",
                "&:hover": { bgcolor: "background.neutral" },
              }}
            >
              <Iconify icon="eva:close-fill" width={24} />
            </IconButton>
          </Box>

          {/* Main Content */}
          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              bgcolor: "background.paper",
            }}
          >
            <Container
              maxWidth="lg"
              sx={{
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                pt: 4,
                pb: 0,
              }}
            >
              {/* Step Indicator */}
              <Stepper
                nonLinear
                alternativeLabel
                activeStep={activeStep}
                connector={<StyledStepConnector />}
              >
                {steps.map((step, index) => (
                  <Step key={step.number} completed={completed[index]}>
                    <Box
                      display={"flex"}
                      flexDirection={"column"}
                      alignItems={"center"}
                      gap={2}
                      component={"div"}
                      sx={{
                        backgroundColor: "background.paper",
                      }}
                      onClick={() => {
                        if (!completed[index]) return;
                        const activeValid = isStepValid(activeStep);
                        const activeCompleted = !!completed[activeStep];
                        if (!activeCompleted) {
                          handleCompletedStep(index);
                          return;
                        }

                        // proceed only if active validity matches its completed flag
                        if (activeValid !== activeCompleted) return;

                        handleCompletedStep(index);

                        // target step must be valid AND completed
                        const targetValid = isStepValid(index);
                        const targetCompleted = !!completed[index];

                        if (!(targetValid && targetCompleted)) return;

                        handleCompletedStep(index);
                      }}
                    >
                      <StepCircle
                        active={index === activeStep}
                        completed={completed[index]}
                      >
                        {completed[index] ? (
                          <SvgColor
                            src="/assets/icons/ic_check.svg"
                            sx={{
                              width: 24,
                              height: 24,
                              color: "green.500",
                            }}
                          />
                        ) : (
                          <SvgColor
                            src={step.icon}
                            sx={{
                              height: 20,
                              width: 20,
                            }}
                          />
                        )}
                      </StepCircle>
                      <Typography
                        key={step.number}
                        variant="body2"
                        color={
                          index < activeStep
                            ? "text.primary"
                            : index <= activeStep
                              ? "primary.main"
                              : "text.disabled"
                        }
                        sx={{
                          textAlign: "center",
                          maxWidth: 200,
                          fontWeight: index === activeStep ? 600 : 400,
                        }}
                      >
                        {step.label}
                      </Typography>
                    </Box>
                  </Step>
                ))}
              </Stepper>

              {/* Step Content — scrollable so the action footer below
                  stays pinned on tall viewports */}
              <Box
                sx={{
                  flex: 1,
                  minHeight: 0,
                  display: "flex",
                  flexDirection: "column",
                  overflowY: "auto",
                  mt: 2,
                }}
              >
                <Paper sx={{ flex: 1, p: 4 }}>{renderStepContent()}</Paper>
              </Box>

              {/* Action Buttons — pinned to the bottom of the viewport so
                  users can always see Back/Next without scrolling */}
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  py: 2,
                  borderTop: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.paper",
                  flexShrink: 0,
                }}
              >
                <Button
                  variant="outlined"
                  onClick={activeStep === 0 ? onClose : handleBack}
                  startIcon={<Iconify icon="akar-icons:chevron-left-small" />}
                >
                  Back
                </Button>

                <Box sx={{ display: "flex", gap: 2 }}>
                  {activeStep === steps.length - 1 ? (
                    <Button
                      variant="contained"
                      onClick={handleSubmit}
                      disabled={createTestMutation.isPending}
                      sx={{
                        bgcolor: "primary.main",
                        color: "primary.contrastText",
                        "&:hover": {
                          bgcolor: "primary.darker",
                        },
                      }}
                      startIcon={
                        <SvgColor
                          src={"/assets/icons/navbar/ic_evaluate.svg"}
                        />
                      }
                    >
                      {"Run Simulation"}
                    </Button>
                  ) : (
                    <Button
                      variant="contained"
                      onClick={handleNextStep}
                      disabled={!canProceed()}
                      endIcon={
                        <Iconify icon="akar-icons:chevron-right-small" />
                      }
                      sx={{
                        bgcolor: "primary.main",
                        color: "primary.contrastText",
                        "&:hover": {
                          bgcolor: "primary.dark",
                        },
                      }}
                    >
                      Next
                    </Button>
                  )}
                </Box>
              </Box>
            </Container>
          </Box>
        </Box>
      )}

      {/* Evaluation picker — new unified drawer (list → config → save) */}
      <EvalPickerDrawer
        open={openEvaluationDialog}
        onClose={() => {
          setOpenEvaluationDialog(false);
          setEditingEvalItem(null);
        }}
        source="create-simulate"
        sourceColumns={evalColumnsToDisplay}
        sourcePreviewData={sourcePreviewData}
        onEvalAdded={handleAddEvaluation}
        initialEval={
          editingEvalItem
            ? {
                id: editingEvalItem.templateId,
                template_id: editingEvalItem.templateId,
              }
            : null
        }
      />
    </>
  );
};

CreateRunTestPage.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default CreateRunTestPage;
