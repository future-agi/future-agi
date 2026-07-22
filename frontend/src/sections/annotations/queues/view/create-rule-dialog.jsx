/* eslint-disable react/prop-types */
/* eslint-disable react-refresh/only-export-components */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "src/components/svg-color";
import {
  extractErrorMessage,
  useCreateAutomationRule,
} from "src/api/annotation-queues/annotation-queues";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import {
  DEVELOP_FILTER_CATEGORIES,
  DatasetColumnValuePicker,
  buildProperties as buildDatasetFilterProperties,
  panelFilterToStore as datasetPanelFilterToStore,
  storeFilterToPanel as datasetStoreFilterToPanel,
} from "src/sections/develop-detail/DataTab/DevelopFilters/DevelopFilterBox";
import FilterChips from "src/sections/projects/LLMTracing/FilterChips";
import TraceFilterPanel, {
  buildTraceFilterProperties,
} from "src/sections/projects/LLMTracing/TraceFilterPanel";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { apiPath } from "src/api/contracts/api-surface";
import { PROJECT_SOURCE } from "src/utils/constants";
import { getRandomId } from "src/utils/utils";
import { apiFilterHasValue } from "src/sections/annotations/queues/utils/filter-operators";
import {
  apiFilterToPanel,
  panelFilterToApi,
} from "src/sections/annotations/queues/utils/api-filter-converters";
import {
  DEFAULT_FILTER,
  MULTI_VALUE_OPS,
  SESSION_RULE_FILTER_FIELDS,
  SIMPLE_FILTER_CATEGORIES,
  SIMULATION_RULE_FILTER_FIELDS,
  SOURCE_OPTIONS,
  TRIGGER_FREQUENCY_OPTIONS,
} from "src/sections/annotations/queues/constants";
import {
  buildConditionsForRule,
  datasetFilterToCamel,
  datasetFilterToSnake,
  defaultFiltersForSource,
  getDatasetOptionId,
  getQueueScopeId,
  getRuleSubmitDisabledTooltipTitle,
  getSubmittableFilters,
  isDatasetFilterValid,
  isQueueScopeLocked,
  isScopeReady,
  makeDatasetDefaultFilter,
  resolveRuleScopeId,
  transformDatasetFilter,
} from "src/sections/annotations/queues/utils/automation-rule-utils";

const activeFilterButtonBg = (theme) => alpha(theme.palette.primary.main, 0.12);

export function RuleScopePicker({
  sourceType,
  scope,
  setScope,
  queue,
  onInteraction,
}) {
  const needsDataset = sourceType === "dataset_row";
  const needsProject = ["trace", "observation_span", "trace_session"].includes(
    sourceType,
  );
  const needsAgentDefinition = sourceType === "call_execution";
  const queueDatasetId = getQueueScopeId(queue, "dataset");
  const queueProjectId = getQueueScopeId(queue, "project");
  const queueAgentId = getQueueScopeId(queue, "agent_definition");
  const defaultQueueHelperText = queue?.is_default
    ? "Default queues auto-receive direct annotations; this rule can target any source."
    : undefined;

  const { data: datasets = [], isLoading: datasetsLoading } = useQuery({
    queryKey: ["datasets-list-simple"],
    queryFn: () =>
      axios.get(apiPath("/model-hub/develops/get-datasets-names/")),
    select: (d) => d.data?.result?.datasets || [],
    enabled: needsDataset,
    staleTime: 1000 * 60 * 5,
  });

  const { data: projects = [], isLoading: projectsLoading } = useQuery({
    queryKey: ["projects-list-all-for-automation-rules"],
    queryFn: () =>
      axios.get(endpoints.project.listProjects(), {
        params: { project_type: "observe" },
      }),
    select: (d) => d.data?.result?.projects || [],
    enabled: needsProject,
    staleTime: 1000 * 60 * 5,
  });

  const { data: agentDefinitions = [], isLoading: agentDefinitionsLoading } =
    useQuery({
      queryKey: ["agent-definitions-list-for-automation-rules"],
      queryFn: () =>
        axios.get(endpoints.agentDefinitions.list, {
          params: { limit: 100 },
        }),
      select: (d) => d.data?.results || d.data?.result?.results || [],
      enabled: needsAgentDefinition,
      staleTime: 1000 * 60 * 5,
    });

  if (needsDataset) {
    const effectiveDatasetId =
      resolveRuleScopeId(queue, queueDatasetId, scope.dataset_id) || "";
    const isQueueScoped = isQueueScopeLocked(queue, queueDatasetId);
    return (
      <Autocomplete
        size="small"
        options={datasets}
        loading={datasetsLoading}
        disabled={isQueueScoped}
        noOptionsText={datasetsLoading ? "Loading datasets..." : "No datasets"}
        getOptionLabel={(dataset) => dataset?.name || ""}
        value={
          datasets.find(
            (dataset) => getDatasetOptionId(dataset) === effectiveDatasetId,
          ) || null
        }
        isOptionEqualToValue={(option, value) =>
          getDatasetOptionId(option) === getDatasetOptionId(value)
        }
        onChange={(_, dataset) => {
          onInteraction?.();
          setScope((prev) => ({
            ...prev,
            dataset_id: getDatasetOptionId(dataset),
          }));
        }}
        sx={{ minWidth: 0 }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Dataset"
            placeholder={
              isQueueScoped ? "Queue dataset is fixed" : "Choose dataset"
            }
            onFocus={onInteraction}
            helperText={
              isQueueScoped ? "Locked by this queue" : defaultQueueHelperText
            }
          />
        )}
      />
    );
  }

  if (needsProject) {
    const effectiveProjectId =
      resolveRuleScopeId(queue, queueProjectId, scope.project_id) || "";
    const isQueueScoped = isQueueScopeLocked(queue, queueProjectId);
    return (
      <Autocomplete
        size="small"
        options={projects}
        loading={projectsLoading}
        disabled={isQueueScoped}
        noOptionsText={projectsLoading ? "Loading projects..." : "No projects"}
        getOptionLabel={(project) => project?.name || ""}
        value={
          projects.find((project) => project.id === effectiveProjectId) || null
        }
        isOptionEqualToValue={(option, value) => option?.id === value?.id}
        onChange={(_, project) => {
          onInteraction?.();
          setScope((prev) => ({
            ...prev,
            project_id: project?.id || "",
            is_voice_call: false,
            remove_simulation_calls: false,
          }));
        }}
        sx={{ minWidth: 0 }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Project"
            placeholder={
              isQueueScoped ? "Queue project is fixed" : "Choose project"
            }
            onFocus={onInteraction}
            helperText={
              isQueueScoped ? "Locked by this queue" : defaultQueueHelperText
            }
          />
        )}
      />
    );
  }

  if (needsAgentDefinition) {
    const effectiveAgentDefinitionId =
      resolveRuleScopeId(queue, queueAgentId, scope.project_id) || "";
    const isQueueScoped = isQueueScopeLocked(queue, queueAgentId);
    return (
      <Autocomplete
        size="small"
        options={agentDefinitions}
        loading={agentDefinitionsLoading}
        disabled={isQueueScoped}
        noOptionsText={
          agentDefinitionsLoading
            ? "Loading agent definitions..."
            : "No agent definitions"
        }
        getOptionLabel={(agent) => agent?.agent_name || agent?.name || ""}
        value={
          agentDefinitions.find(
            (agent) => agent.id === effectiveAgentDefinitionId,
          ) || null
        }
        isOptionEqualToValue={(option, value) => option?.id === value?.id}
        onChange={(_, agent) => {
          onInteraction?.();
          setScope((prev) => ({
            ...prev,
            project_id: agent?.id || "",
          }));
        }}
        sx={{ minWidth: 0 }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Agent Definition"
            placeholder={
              isQueueScoped
                ? "Queue agent definition is fixed"
                : "Choose agent definition"
            }
            onFocus={onInteraction}
            helperText={
              isQueueScoped ? "Locked by this queue" : defaultQueueHelperText
            }
          />
        )}
      />
    );
  }

  return null;
}

function DatasetRuleFilters({
  filters,
  setFilters,
  scope,
  queue,
  onInteraction,
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const buttonRef = useRef(null);
  const queueDatasetId = getQueueScopeId(queue, "dataset");
  const datasetId = resolveRuleScopeId(queue, queueDatasetId, scope.dataset_id);
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], "", {
      enabled: !!datasetId,
      staleTime: Infinity,
    }),
  );

  const columnConfig = useMemo(
    () => tableData?.data?.result?.column_config || [],
    [tableData],
  );

  const allColumns = useMemo(
    () =>
      columnConfig.map((column) => ({
        field: column.id,
        headerName: column.name,
        col: column,
      })),
    [columnConfig],
  );

  const properties = useMemo(
    () => buildDatasetFilterProperties(allColumns),
    [allColumns],
  );

  const columnLookup = useMemo(() => {
    const lookup = {};
    for (const property of properties) {
      lookup[property.id] = property;
    }
    return lookup;
  }, [properties]);

  const labelLookup = useMemo(() => {
    const lookup = {};
    for (const column of allColumns) {
      const colData = column?.col;
      const id = column.field || colData?.id;
      if (!id) continue;
      lookup[id] = column.headerName || colData?.name || colData?.id;
    }
    return lookup;
  }, [allColumns]);

  const panelCurrentFilters = useMemo(
    () =>
      filters
        .filter((filter) => filter.column_id)
        .map((filter) =>
          datasetStoreFilterToPanel(datasetFilterToCamel(filter), columnLookup),
        ),
    [filters, columnLookup],
  );

  const chipFilters = useMemo(
    () =>
      filters
        .filter(isDatasetFilterValid)
        .map(transformDatasetFilter)
        .map((filter) => ({
          ...filter,
          display_name:
            labelLookup[filter.column_id] ||
            columnLookup[filter.column_id]?.name ||
            filter.display_name,
        })),
    [filters, columnLookup, labelLookup],
  );

  const validFilterIndices = useMemo(() => {
    const indices = [];
    filters.forEach((filter, index) => {
      if (isDatasetFilterValid(filter)) indices.push(index);
    });
    return indices;
  }, [filters]);

  const handleApply = useCallback(
    (newPanelFilters) => {
      onInteraction?.();
      const nextFilters = (newPanelFilters || [])
        .map(datasetPanelFilterToStore)
        .map(datasetFilterToSnake);
      setFilters(
        nextFilters.length ? nextFilters : [makeDatasetDefaultFilter()],
      );
    },
    [onInteraction, setFilters],
  );

  if (!datasetId) {
    return (
      <Typography variant="body2" color="text.secondary">
        Choose a dataset to configure row filters.
      </Typography>
    );
  }

  return (
    <Box sx={{ maxWidth: "100%", minWidth: 0, overflow: "hidden" }}>
      <IconButton
        ref={buttonRef}
        size="small"
        aria-label="Open rule filters"
        data-testid="automation-rule-filter-button"
        onClick={() => {
          onInteraction?.();
          setFilterAnchorEl(buttonRef.current);
          setFilterOpen((value) => !value);
        }}
        sx={{
          border: "1px solid",
          borderColor: filters.some((filter) => filter.column_id)
            ? "primary.main"
            : "divider",
          borderRadius: 0.5,
          p: 0.75,
          mb: 1,
          bgcolor: (theme) =>
            filters.some((filter) => filter.column_id)
              ? activeFilterButtonBg(theme)
              : "transparent",
        }}
      >
        <SvgColor
          src="/assets/icons/action_buttons/ic_filter.svg"
          sx={{ width: 16, height: 16 }}
        />
      </IconButton>

      <TraceFilterPanel
        anchorEl={filterAnchorEl || buttonRef.current}
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        currentFilters={panelCurrentFilters}
        onApply={handleApply}
        properties={properties}
        ValuePickerOverride={DatasetColumnValuePicker}
        freeSoloValues={(filter) => MULTI_VALUE_OPS.has(filter.operator)}
        projectId={datasetId}
        source="dataset"
        showAi
        showQueryTab
        categories={DEVELOP_FILTER_CATEGORIES}
        panelWidth={560}
      />

      <FilterChips
        extraFilters={chipFilters}
        onAddFilter={(anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onChipClick={(_chipIndex, anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onRemoveFilter={(chipIndex) => {
          onInteraction?.();
          setFilterAnchorEl(null);
          const filterIndex = validFilterIndices[chipIndex];
          if (filterIndex === undefined) return;
          setFilters((prev) => {
            const nextFilters = prev.filter(
              (_, index) => index !== filterIndex,
            );
            return nextFilters.length
              ? nextFilters
              : [makeDatasetDefaultFilter()];
          });
        }}
        onClearAll={() => {
          onInteraction?.();
          setFilterAnchorEl(null);
          setFilters([makeDatasetDefaultFilter()]);
          setFilterOpen(false);
        }}
      />
    </Box>
  );
}

function TraceRuleFilters({
  filters,
  setFilters,
  scope,
  setScope,
  queue,
  sourceType,
  onInteraction,
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const buttonRef = useRef(null);
  const queueProjectId = getQueueScopeId(queue, "project");
  const projectId = resolveRuleScopeId(queue, queueProjectId, scope.project_id);
  const { data: projectDetails } = useGetProjectDetails(
    projectId,
    sourceType === "trace" && !!projectId,
  );
  const isVoiceProject = projectDetails?.source === PROJECT_SOURCE.SIMULATOR;
  const panelSource = sourceType === "trace_session" ? "sessions" : "traces";
  const isSpanSource = sourceType === "observation_span";
  const filterFields =
    sourceType === "trace_session" ? SESSION_RULE_FILTER_FIELDS : undefined;

  const snakeFilters = useMemo(() => getSubmittableFilters(filters), [filters]);

  useEffect(() => {
    if (sourceType !== "trace" || !projectId) return;
    setScope((prev) => {
      const nextIsVoice = !!isVoiceProject;
      const nextRemoveSimulationCalls = false;
      if (
        prev.is_voice_call === nextIsVoice &&
        prev.remove_simulation_calls === nextRemoveSimulationCalls
      ) {
        return prev;
      }
      return {
        ...prev,
        is_voice_call: nextIsVoice,
        remove_simulation_calls: nextRemoveSimulationCalls,
      };
    });
  }, [isVoiceProject, projectId, setScope, sourceType]);

  if (!projectId) {
    return (
      <Typography variant="body2" color="text.secondary">
        Choose a project to configure filters.
      </Typography>
    );
  }

  return (
    <Box sx={{ maxWidth: "100%", minWidth: 0, overflow: "hidden" }}>
      <IconButton
        ref={buttonRef}
        size="small"
        aria-label="Open rule filters"
        data-testid="automation-rule-filter-button"
        onClick={() => {
          onInteraction?.();
          setFilterAnchorEl(buttonRef.current);
          setFilterOpen((value) => !value);
        }}
        sx={{
          border: "1px solid",
          borderColor: filters.some((filter) => filter.column_id)
            ? "primary.main"
            : "divider",
          borderRadius: 0.5,
          p: 0.75,
          mb: 1,
          bgcolor: (theme) =>
            filters.some((filter) => filter.column_id)
              ? activeFilterButtonBg(theme)
              : "transparent",
        }}
      >
        <SvgColor
          src="/assets/icons/action_buttons/ic_filter.svg"
          sx={{ width: 16, height: 16 }}
        />
      </IconButton>

      <TraceFilterPanel
        anchorEl={filterAnchorEl || buttonRef.current}
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        projectId={projectId}
        source={panelSource}
        tab={isSpanSource ? "spans" : undefined}
        isSpansView={isSpanSource}
        filterFields={filterFields}
        isSimulator={isVoiceProject}
        key={`${projectId}-${panelSource}-${isVoiceProject ? "voice" : "trace"}`}
        currentFilters={getSubmittableFilters(filters).map(apiFilterToPanel)}
        onApply={(newPanelFilters) => {
          onInteraction?.();
          const nextFilters = (newPanelFilters || [])
            .map(panelFilterToApi)
            .filter(apiFilterHasValue);
          setFilters(
            nextFilters.length
              ? nextFilters.map((filter) => ({ ...filter, id: getRandomId() }))
              : [{ ...DEFAULT_FILTER, id: getRandomId() }],
          );
        }}
        freeSoloValues={(filter) => MULTI_VALUE_OPS.has(filter.operator)}
      />

      <FilterChips
        extraFilters={snakeFilters}
        onAddFilter={(anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onChipClick={(_index, anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onRemoveFilter={(index) => {
          onInteraction?.();
          setFilterAnchorEl(null);
          const target = snakeFilters[index];
          if (!target) return;
          setFilters((prev) =>
            prev.filter((filter) => {
              const colMatches = filter.column_id === target.column_id;
              const opMatches =
                filter.filter_config?.filter_op ===
                target.filter_config?.filter_op;
              return !(colMatches && opMatches);
            }),
          );
        }}
        onClearAll={() => {
          onInteraction?.();
          setFilterAnchorEl(null);
          setFilters([{ ...DEFAULT_FILTER, id: getRandomId() }]);
          setFilterOpen(false);
        }}
      />
    </Box>
  );
}

function SimulationRuleFilters({
  filters,
  setFilters,
  scope,
  queue,
  onInteraction,
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const buttonRef = useRef(null);
  const queueAgentId = getQueueScopeId(queue, "agent_definition");
  const agentDefinitionId = resolveRuleScopeId(
    queue,
    queueAgentId,
    scope.project_id,
  );

  const panelCurrentFilters = useMemo(
    () => getSubmittableFilters(filters).map(apiFilterToPanel),
    [filters],
  );

  const snakeFilters = useMemo(() => getSubmittableFilters(filters), [filters]);
  const { data: simulationEvalFields = [] } = useQuery({
    queryKey: ["automation-rule-simulation-eval-fields", agentDefinitionId],
    queryFn: () =>
      axios.get(endpoints.dashboard.metrics, {
        params: { agent_definition_id: agentDefinitionId },
      }),
    enabled: Boolean(agentDefinitionId),
    select: (response) =>
      buildTraceFilterProperties(response.data?.result?.metrics || [], {
        isSimulator: true,
        sourceScope: "simulation",
      }).filter((property) => property.category === "eval"),
    staleTime: 5 * 60_000,
  });
  const properties = useMemo(() => {
    const fieldsById = new Map(
      SIMULATION_RULE_FILTER_FIELDS.map((field) => [field.id, field]),
    );
    simulationEvalFields.forEach((field) => fieldsById.set(field.id, field));
    return Array.from(fieldsById.values());
  }, [simulationEvalFields]);

  return (
    <Box sx={{ maxWidth: "100%", minWidth: 0, overflow: "hidden" }}>
      <IconButton
        ref={buttonRef}
        size="small"
        aria-label="Open rule filters"
        data-testid="automation-rule-filter-button"
        onClick={() => {
          onInteraction?.();
          setFilterAnchorEl(buttonRef.current);
          setFilterOpen((value) => !value);
        }}
        sx={{
          border: "1px solid",
          borderColor: filters.some((filter) => filter.column_id)
            ? "primary.main"
            : "divider",
          borderRadius: 0.5,
          p: 0.75,
          mb: 1,
          bgcolor: (theme) =>
            filters.some((filter) => filter.column_id)
              ? activeFilterButtonBg(theme)
              : "transparent",
        }}
      >
        <SvgColor
          src="/assets/icons/action_buttons/ic_filter.svg"
          sx={{ width: 16, height: 16 }}
        />
      </IconButton>

      <TraceFilterPanel
        anchorEl={filterAnchorEl || buttonRef.current}
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        currentFilters={panelCurrentFilters}
        onApply={(newPanelFilters) => {
          onInteraction?.();
          const nextFilters = (newPanelFilters || [])
            .map(panelFilterToApi)
            .filter(apiFilterHasValue);
          setFilters(
            nextFilters.length
              ? nextFilters.map((filter) => ({ ...filter, id: getRandomId() }))
              : [{ ...DEFAULT_FILTER, id: getRandomId() }],
          );
        }}
        properties={properties}
        source="simulation"
        showAi={false}
        showQueryTab={false}
        categories={SIMPLE_FILTER_CATEGORIES}
        panelWidth={560}
      />

      <FilterChips
        extraFilters={snakeFilters}
        onAddFilter={(anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onChipClick={(_index, anchorEl) => {
          onInteraction?.();
          setFilterAnchorEl(anchorEl || buttonRef.current);
          setFilterOpen(true);
        }}
        onRemoveFilter={(index) => {
          onInteraction?.();
          setFilterAnchorEl(null);
          const target = snakeFilters[index];
          if (!target) return;
          setFilters((prev) =>
            prev.filter((filter) => {
              const colMatches = filter.column_id === target.column_id;
              const opMatches =
                filter.filter_config?.filter_op ===
                target.filter_config?.filter_op;
              return !(colMatches && opMatches);
            }),
          );
        }}
        onClearAll={() => {
          onInteraction?.();
          setFilterAnchorEl(null);
          setFilters([{ ...DEFAULT_FILTER, id: getRandomId() }]);
          setFilterOpen(false);
        }}
      />
    </Box>
  );
}

export function RuleFilterSection({
  sourceType,
  filters,
  setFilters,
  scope,
  setScope,
  queue,
  onInteraction,
}) {
  if (sourceType === "dataset_row") {
    return (
      <DatasetRuleFilters
        filters={filters}
        setFilters={setFilters}
        scope={scope}
        queue={queue}
        onInteraction={onInteraction}
      />
    );
  }
  if (["trace", "observation_span", "trace_session"].includes(sourceType)) {
    return (
      <TraceRuleFilters
        filters={filters}
        setFilters={setFilters}
        scope={scope}
        setScope={setScope}
        queue={queue}
        sourceType={sourceType}
        onInteraction={onInteraction}
      />
    );
  }
  return (
    <SimulationRuleFilters
      filters={filters}
      setFilters={setFilters}
      scope={scope}
      queue={queue}
      onInteraction={onInteraction}
    />
  );
}

export default function CreateRuleDialog({ open, onClose, queueId, queue }) {
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [sourceType, setSourceType] = useState("trace");
  const [triggerFrequency, setTriggerFrequency] = useState("manual");
  const [scope, setScope] = useState({});
  const [filters, setFilters] = useState(defaultFiltersForSource("trace"));
  // Inline copy of the latest server error. The hook also enqueues a
  // toast on error, but quota / validation errors are easy to miss when
  // the toast is brief or covered by the dialog stack — keeping the
  // message pinned in the dialog ensures the user always sees it.
  const [serverError, setServerError] = useState("");

  const { mutate: createRule, isPending } = useCreateAutomationRule();

  useEffect(() => {
    if (!open) {
      setName("");
      setNameTouched(false);
      setSourceType("trace");
      setTriggerFrequency("manual");
      setScope({});
      setFilters(defaultFiltersForSource("trace"));
      setServerError("");
    }
  }, [open]);

  const handleSourceChange = useCallback((newSource) => {
    setSourceType(newSource);
    setScope({});
    setFilters(defaultFiltersForSource(newSource));
  }, []);

  const markNameTouched = useCallback(() => {
    setNameTouched(true);
  }, []);

  const handleCreate = () => {
    setServerError("");
    createRule(
      {
        queueId,
        name,
        source_type: sourceType,
        trigger_frequency: triggerFrequency,
        conditions: buildConditionsForRule(sourceType, filters, scope, queue),
        enabled: true,
      },
      {
        onSuccess: () => {
          onClose();
          setName("");
          setNameTouched(false);
          setSourceType("trace");
          setTriggerFrequency("manual");
          setScope({});
          setFilters(defaultFiltersForSource("trace"));
          setServerError("");
        },
        onError: (error) => {
          setServerError(extractErrorMessage(error, "Failed to create rule"));
        },
      },
    );
  };

  const disabled =
    isPending || !name.trim() || !isScopeReady(sourceType, scope, queue);
  const showNameError = nameTouched && !name.trim();
  const disabledTooltipTitle = getRuleSubmitDisabledTooltipTitle(
    sourceType,
    scope,
    queue,
    name,
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Create Automation Rule</DialogTitle>
      <DialogContent sx={{ overflowX: "hidden" }}>
        <Stack spacing={2.5} sx={{ mt: 1, minWidth: 0 }}>
          {serverError && (
            <Alert
              severity="error"
              onClose={() => setServerError("")}
              data-testid="automation-rule-server-error"
            >
              {serverError}
            </Alert>
          )}
          {queue?.is_default && (
            <Alert severity="info" variant="outlined">
              This is a default queue. Direct annotations still land here
              automatically, and this rule can add items from any selected
              source.
            </Alert>
          )}
          <TextField
            label="Rule name"
            fullWidth
            value={name}
            size="small"
            onChange={(event) => setName(event.target.value)}
            onBlur={markNameTouched}
            error={showNameError}
            helperText={showNameError ? "Rule name is required" : ""}
            required
            autoFocus
            inputProps={{ "data-testid": "automation-rule-name-input" }}
          />

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            sx={{ minWidth: 0 }}
          >
            <TextField
              select
              label="Source type"
              fullWidth
              size="small"
              value={sourceType}
              onChange={(event) => {
                markNameTouched();
                handleSourceChange(event.target.value);
              }}
              SelectProps={{
                SelectDisplayProps: {
                  "data-testid": "automation-rule-source-select",
                },
              }}
            >
              {SOURCE_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Trigger"
              fullWidth
              size="small"
              value={triggerFrequency}
              onChange={(event) => {
                markNameTouched();
                setTriggerFrequency(event.target.value);
              }}
              SelectProps={{
                SelectDisplayProps: {
                  "data-testid": "automation-rule-trigger-select",
                },
              }}
            >
              {TRIGGER_FREQUENCY_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          <RuleScopePicker
            sourceType={sourceType}
            scope={scope}
            setScope={setScope}
            queue={queue}
            onInteraction={markNameTouched}
          />

          <Box sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Conditions
            </Typography>
            <RuleFilterSection
              sourceType={sourceType}
              filters={filters}
              setFilters={setFilters}
              scope={scope}
              setScope={setScope}
              queue={queue}
              onInteraction={markNameTouched}
            />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ flexWrap: "wrap", gap: 1 }}>
        <Button onClick={onClose} disabled={isPending}>
          Cancel
        </Button>
        <Tooltip
          title={disabledTooltipTitle}
          disableHoverListener={!disabledTooltipTitle}
        >
          <span
            data-testid="automation-rule-create-submit-wrapper"
            style={{ display: "inline-flex" }}
          >
            <Button
              variant="contained"
              color="primary"
              onClick={handleCreate}
              disabled={disabled}
              data-testid="automation-rule-create-submit"
            >
              {isPending ? "Creating..." : "Create Rule"}
            </Button>
          </span>
        </Tooltip>
      </DialogActions>
    </Dialog>
  );
}
