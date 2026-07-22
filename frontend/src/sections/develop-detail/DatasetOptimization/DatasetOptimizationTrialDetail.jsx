import React, { useState, useMemo } from "react";
import { Box, Typography, Breadcrumbs, Button, Switch } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import CustomTooltip from "src/components/tooltip";
// Import shared components from simulation
import EvalCellRenderer from "src/sections/test-detail/CellRenderers/EvalCellRenderer";
import PromptTooltip from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/CellRenderers/PromptTooltip";
import Growth from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/Growth";
import PromptPanel from "src/sections/test-detail/FixMyAgentDrawer/OptimizationDetail/PrompDetails/PromptPanel";
import PromptDiffView from "src/sections/test-detail/FixMyAgentDrawer/OptimizationDetail/PrompDetails/PromptDiffView";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/test-detail/FixMyAgentDrawer/OptimizationDetail/SharedComponents";

/**
 * Column configuration function matching simulation's getTrialItemsColumnConfig.
 * Handles both evaluation columns (with EvalCellRenderer) and text columns (with tooltips).
 * Handles both evaluation columns (with EvalCellRenderer) and text columns (with tooltips).
 */
const getTrialItemsColumnConfig = (columnConfig) => {
  if (!columnConfig || !Array.isArray(columnConfig)) return [];

  return columnConfig.map((column) => {
    // Evaluation columns are those that are not input_text or output_text
    const isEvaluationColumn =
      column.id !== "id" &&
      column.id !== "input_text" &&
      column.id !== "output_text";

    if (isEvaluationColumn) {
      return {
        field: column.id,
        headerName: column.name,
        minWidth: 150,
        cellRenderer: EvalCellRenderer,
        valueGetter: (params) => {
          // For UUID-based eval columns, the key stays the same (no conversion needed)
          const value = params.data?.[column.id];
          if (value == null) {
            return { type: "score", value: null };
          }
          return {
            type: "score",
            value: typeof value === "number" ? value : parseFloat(value),
          };
        },
        cellStyle: {
          padding: 0,
        },
      };
    }

    // Text columns (input_text, output_text)
    return {
      field: column.id,
      headerName: column.name,
      wrapText: true,
      flex: 1,
      minWidth: 400,
      tooltipComponent: PromptTooltip,
      tooltipValueGetter: ({ data }) => {
        const value = data?.[column.id];
        // Handle JSON objects in inputText
        if (typeof value === "object") {
          return JSON.stringify(value, null, 2);
        }
        return value;
      },
      valueFormatter: (params) => {
        // Handle JSON objects in inputText
        if (typeof params.value === "object") {
          return JSON.stringify(params.value, null, 2);
        }
        return params.value;
      },
      cellStyle: {
        lineHeight: 1.5,
      },
    };
  });
};

const DatasetOptimizationTrialDetail = ({
  optimizationId,
  trialId,
  onBack,
  onBackToList,
}) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [showDiff, setShowDiff] = useState(false);
  const [activeTab, setActiveTab] = useState("prompt");

  const tabs = [
    { value: "prompt", label: "Prompt" },
    { value: "trial-items", label: "Trial Items" },
  ];

  const handleTabChange = (_event, newValue) => {
    setActiveTab(newValue);
  };

  // Fetch trial prompt data
  const { data: trialData, isLoading } = useQuery({
    queryKey: ["dataset-optimization-trial-prompt", optimizationId, trialId],
    queryFn: async () => {
      const response = await axios.get(
        endpoints.develop.datasetOptimization.trialPrompt(
          optimizationId,
          trialId,
        ),
      );
      return response.data?.result || response.data;
    },
    enabled: !!optimizationId && !!trialId,
  });

  // Fetch full trial detail (for future use)
  const { data: _trialDetail } = useQuery({
    queryKey: ["dataset-optimization-trial-detail", optimizationId, trialId],
    queryFn: async () => {
      const response = await axios.get(
        endpoints.develop.datasetOptimization.trialDetail(
          optimizationId,
          trialId,
        ),
      );
      return response.data?.result || response.data;
    },
    enabled: !!optimizationId && !!trialId,
  });

  // Fetch trial scenarios (trial items) - matching simulation's pattern
  const { data: trialScenarios } = useQuery({
    queryKey: ["dataset-optimization-trial-scenarios", optimizationId, trialId],
    queryFn: async () => {
      const response = await axios.get(
        endpoints.develop.datasetOptimization.trialScenarios(
          optimizationId,
          trialId,
        ),
      );
      return response.data?.result || response.data;
    },
    enabled: !!optimizationId && !!trialId && activeTab === "trial-items",
  });

  // Column definitions for trial items grid - using simulation's pattern
  const trialItemsColumnDefs = useMemo(() => {
    return getTrialItemsColumnConfig(trialScenarios?.column_config);
  }, [trialScenarios?.column_config]);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: true,
      filter: false,
      resizable: true,
      suppressMenu: true,
    }),
    [],
  );

  if (isLoading) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography>Loading...</Typography>
      </Box>
    );
  }

  // Get trial name from API response
  const trialName = trialData?.trialName || trialData?.trial_name;
  const optimizationName = trialData?.optimizationName;
  const scorePercentageChange =
    trialData?.scorePercentageChange || trialData?.score_percentage_change;
  const basePrompt = trialData?.basePrompt || trialData?.base_prompt;
  const trialPrompt = trialData?.trialPrompt || trialData?.trial_prompt;

  return (
    <Box
      sx={{
        height: "100%",
        padding: 2,
        gap: 2,
        width: "100%",
        display: "flex",
        flexDirection: "column",
        paddingTop: 0,
      }}
    >
      {/* Breadcrumb navigation */}
      <Breadcrumbs
        sx={{
          "& .MuiBreadcrumbs-separator": {
            marginLeft: 0,
            marginRight: 0,
          },
        }}
        separator={
          <SvgColor
            src="/assets/icons/custom/lucide--chevron-right.svg"
            sx={{ width: 20, height: 20, bgcolor: "text.primary" }}
          />
        }
      >
        <Typography
          component={Button}
          size="small"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.secondary"
          onClick={onBackToList}
          sx={{
            px: "10px",
            "&:hover": { backgroundColor: "transparent" },
          }}
        >
          Optimization Runs
        </Typography>
        <Typography
          component={Button}
          size="small"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.secondary"
          onClick={onBack}
          sx={{
            px: "10px",
            "&:hover": { backgroundColor: "transparent" },
          }}
        >
          {optimizationName ?? "..."}
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ px: "10px" }}
        >
          {trialName}
        </Typography>
      </Breadcrumbs>

      {/* Trial Info - matching simulation */}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {trialName}
        </Typography>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Typography typography="s1">
            Optimization Name: {optimizationName}
          </Typography>
          <Growth
            value={scorePercentageChange}
            getText={(value) => {
              if (value > 0) {
                return `+${value} pts improved from baseline prompt`;
              } else if (value < 0) {
                return `${value} pts degraded from baseline prompt`;
              } else {
                return `No change from baseline prompt`;
              }
            }}
          />
        </Box>
      </Box>

      {/* Tabs - matching simulation's styled tabs */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <TabWrapper>
          <CustomTabs value={activeTab} onChange={handleTabChange}>
            {tabs.map((tab) => (
              <CustomTab key={tab.value} label={tab.label} value={tab.value} />
            ))}
          </CustomTabs>
        </TabWrapper>
        <ShowComponent condition={activeTab === "prompt"}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Switch
              size="small"
              checked={showDiff}
              onChange={() => {
                setShowDiff((v) => !v);
              }}
            />
            <Typography typography="s1">Show Diff</Typography>
            <CustomTooltip
              show
              title="Shows the difference between Agent Prompt vs Optimized Agent Prompt"
              placement="bottom"
              arrow
              type="black"
              size="small"
            >
              <SvgColor
                src="/assets/icons/ic_info.svg"
                sx={{ width: 16, height: 16 }}
              />
            </CustomTooltip>
          </Box>
        </ShowComponent>
      </Box>

      {/* Tab Content */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
        }}
      >
        {/* Prompt Tab - using simulation's PromptPanel */}
        <ShowComponent condition={activeTab === "prompt"}>
          {showDiff ? (
            <PromptDiffView
              originalPrompt={basePrompt}
              optimizedPrompt={trialPrompt}
            />
          ) : (
            <Box sx={{ display: "flex", gap: 1, height: "100%" }}>
              <Box sx={{ flex: 1 }}>
                <PromptPanel
                  title="OPTIMIZED AGENT PROMPT"
                  prompt={trialPrompt}
                />
              </Box>
            </Box>
          )}
        </ShowComponent>

        {/* Trial Items Tab - matching simulation's TrialItems component */}
        <ShowComponent condition={activeTab === "trial-items"}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              height: "100%",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                backgroundColor: "blue.o5",
                paddingX: "12px",
                paddingY: "4px",
                borderRadius: "4px",
              }}
            >
              <SvgColor
                src="/assets/icons/ic_info.svg"
                sx={{ width: 16, height: 16, color: "blue.500" }}
              />
              <Typography typography="s2">
                Iterations ran to optimize the prompt
              </Typography>
            </Box>
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Trial Items : {trialScenarios?.table?.length || 0}
            </Typography>
            <Box sx={{ width: "100%", flex: 1, minHeight: 0 }}>
              <AgGridReact
                theme={agTheme}
                rowHeight={100}
                rowSelection={undefined}
                columnDefs={trialItemsColumnDefs}
                defaultColDef={defaultColDef}
                pagination={true}
                paginationPageSizeSelector={false}
                rowData={trialScenarios?.table || []}
                getRowId={({ data }) => data.id}
                paginationPageSize={10}
                tooltipShowDelay={200}
                tooltipInteraction={true}
              />
            </Box>
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

DatasetOptimizationTrialDetail.propTypes = {
  optimizationId: PropTypes.string.isRequired,
  trialId: PropTypes.string.isRequired,
  onBack: PropTypes.func.isRequired,
  onBackToList: PropTypes.func.isRequired,
};

export default DatasetOptimizationTrialDetail;
