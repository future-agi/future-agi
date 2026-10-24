import { useState, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  CircularProgress,
  Switch,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useUpdateMCPToolGroups } from "src/api/mcp";

const TOOL_GROUP_ICONS = {
  context: "solar:compass-linear",
  evaluations: "solar:clipboard-check-linear",
  datasets: "solar:database-linear",
  annotations: "solar:check-square-bold",
  optimization: "solar:stars-minimalistic-linear",
  observability: "solar:graph-linear",
  error_feed: "solar:danger-circle-linear",
  experiments: "solar:test-tube-linear",
  agents: "solar:bot-linear",
  simulation: "solar:soundwave-linear",
  prompts: "solar:chat-round-line-linear",
  users: "solar:users-group-rounded-linear",
  usage: "solar:graph-up-linear",
  docs: "solar:book-linear",
};

const DEFAULT_TOOL_GROUPS = [
  {
    id: "context",
    name: "Context & Navigation",
    description: "User profile, workspace management, schema discovery, search",
  },
  {
    id: "evaluations",
    name: "Evaluations",
    description:
      "Run, compare, and analyze LLM evaluations; manage eval templates and composite evals",
  },
  {
    id: "datasets",
    name: "Datasets & Knowledge Bases",
    description:
      "Create, manage, query datasets; manage knowledge bases for RAG",
  },
  {
    id: "annotations",
    name: "Annotations",
    description: "Human annotation tasks, labels, and review workflows",
  },
  {
    id: "optimization",
    name: "Prompt Optimization",
    description:
      "Optimize prompts using algorithms (random search, bayesian, metaprompt, etc.)",
  },
  {
    id: "observability",
    name: "Observability / Traces",
    description:
      "Search traces, projects, error analysis, alerts, and annotations",
  },
  {
    id: "error_feed",
    name: "Error Feed",
    description:
      "Browse error clusters, analyze project-wide traces, and submit findings",
  },
  {
    id: "experiments",
    name: "Experiments",
    description: "Create and analyze A/B experiments with variant comparison",
  },
  {
    id: "agents",
    name: "Agents & Simulation",
    description:
      "Manage agents, versions, scenarios, test executions, and call results",
  },
  {
    id: "simulation",
    name: "Simulation",
    description:
      "Agent definitions, versions, personas, scenarios, simulator agents, test runs, and call analysis",
  },
  {
    id: "prompts",
    name: "Prompt Workbench",
    description:
      "Manage prompt templates, versions, labels, folders, simulations, and evaluations",
  },
  {
    id: "users",
    name: "Users & Workspaces",
    description:
      "User management, workspace operations, organization settings, and API key management",
  },
  {
    id: "usage",
    name: "Usage & Costs",
    description: "Cost analytics and billing information",
  },
  {
    id: "docs",
    name: "Docs & Guides",
    description:
      "Search and query Future AGI documentation, setup guides, and API references",
  },
];

export function normalizeMCPToolGroups(config) {
  const toolConfig = config?.tool_config || config || {};
  const availableGroups = Array.isArray(toolConfig.available_groups)
    ? toolConfig.available_groups
    : Array.isArray(config?.tool_groups)
      ? config.tool_groups
      : [];

  if (!availableGroups.length) return DEFAULT_TOOL_GROUPS;

  return availableGroups
    .map((group) => ({
      id: group.slug || group.id,
      name: group.name,
      description: group.description || "",
    }))
    .filter((group) => group.id && group.name);
}

export function normalizeMCPEnabledGroups(config, toolGroups) {
  const toolConfig = config?.tool_config || config || {};
  const explicit =
    toolConfig.enabled_groups ||
    config?.enabled_groups ||
    config?.enabled_tool_groups;

  if (Array.isArray(explicit)) return explicit;

  const availableGroups = Array.isArray(toolConfig.available_groups)
    ? toolConfig.available_groups
    : [];
  if (availableGroups.length) {
    return availableGroups
      .filter((group) => group.enabled || group.checked)
      .map((group) => group.slug || group.id)
      .filter(Boolean);
  }

  return toolGroups.map((group) => group.id);
}

ToolGroupSelector.propTypes = {
  config: PropTypes.object,
};

export default function ToolGroupSelector({ config }) {
  const theme = useTheme();
  const updateMutation = useUpdateMCPToolGroups();

  const toolGroups = useMemo(() => normalizeMCPToolGroups(config), [config]);
  const enabledFromConfig = useMemo(
    () => normalizeMCPEnabledGroups(config, toolGroups),
    [config, toolGroups],
  );
  const enabledKey = useMemo(
    () => JSON.stringify(enabledFromConfig),
    [enabledFromConfig],
  );

  const [enabled, setEnabled] = useState(enabledFromConfig);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (enabledFromConfig) {
      setEnabled(enabledFromConfig);
      setDirty(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabledKey]);

  const handleToggle = (id) => {
    setEnabled((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : [...prev, id];
      setDirty(true);
      return next;
    });
  };

  const handleSave = () => {
    updateMutation.mutate(
      { enabled_groups: enabled },
      {
        onSuccess: () => setDirty(false),
      },
    );
  };

  const allEnabled =
    toolGroups.length > 0 && enabled.length === toolGroups.length;

  return (
    <Accordion
      variant="outlined"
      disableGutters
      defaultExpanded={false}
      sx={{
        mb: theme.spacing(3),
        borderRadius: "8px !important",
        "&::before": { display: "none" },
        "& .MuiAccordionSummary-root": { borderRadius: 1 },
      }}
    >
      <AccordionSummary
        expandIcon={<Iconify icon="solar:alt-arrow-down-linear" width={16} />}
        sx={{ px: 3, py: 0.5 }}
      >
        <Box display="flex" alignItems="center" gap={1.5} flex={1} mr={2}>
          <Iconify
            icon="solar:toolbox-linear"
            width={20}
            sx={{ color: "text.secondary" }}
          />
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Tool Groups
          </Typography>
          <Chip
            label={
              allEnabled
                ? "All enabled"
                : `${enabled.length} of ${toolGroups.length}`
            }
            size="small"
            color={allEnabled ? "success" : "default"}
            variant="outlined"
          />
          <Typography
            sx={{ typography: "s2", color: "text.disabled", ml: "auto" }}
          >
            Restrict which tools are available to connected clients
          </Typography>
        </Box>
      </AccordionSummary>

      <AccordionDetails sx={{ px: 3, pt: 0, pb: 2.5 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 0,
            borderTop: "1px solid",
            borderColor: "border.subtle",
          }}
        >
          {toolGroups.map((group) => {
            const isEnabled = enabled.includes(group.id);
            return (
              <Box
                key={group.id}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.25,
                  py: 1,
                  px: 1,
                  borderBottom: "1px solid",
                  borderColor: "border.subtle",
                  opacity: isEnabled ? 1 : 0.5,
                  transition: "opacity 0.15s",
                }}
              >
                <Iconify
                  icon={TOOL_GROUP_ICONS[group.id] || "solar:toolbox-linear"}
                  width={16}
                  sx={{
                    color: isEnabled ? "primary.main" : "text.disabled",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2_1",
                    fontWeight: "fontWeightMedium",
                    color: "text.primary",
                    flex: 1,
                    lineHeight: 1.3,
                  }}
                >
                  {group.name}
                </Typography>
                <Switch
                  size="small"
                  checked={isEnabled}
                  onChange={() => handleToggle(group.id)}
                  sx={{ ml: "auto" }}
                />
              </Box>
            );
          })}
        </Box>

        {dirty && (
          <Box display="flex" justifyContent="flex-end" mt={2}>
            <Button
              variant="contained"
              size="small"
              disabled={updateMutation.isPending}
              onClick={handleSave}
              startIcon={
                updateMutation.isPending ? (
                  <CircularProgress size={14} color="inherit" />
                ) : null
              }
            >
              {updateMutation.isPending ? "Saving..." : "Save changes"}
            </Button>
          </Box>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
