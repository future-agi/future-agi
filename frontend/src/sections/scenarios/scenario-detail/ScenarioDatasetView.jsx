import React, { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Box, Typography, Button, CircularProgress } from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import PropTypes from "prop-types";
import { useGetScenarioDetail } from "src/api/scenarios/scenarios";
import { LoadingScreen } from "src/components/loading-screen";
import SvgColor from "src/components/svg-color";
import { getChipConfig } from "src/components/scenarios/CustomCellRenderers/ChipCellRenderer";
import GraphPreview from "./GraphPreview";
import PromptPreview from "./PromptPreview";
import { ShowComponent } from "src/components/show";
import DevelopDataV2 from "src/sections/develop-detail/DataTab/DevelopDataV2";
import DevelopDetailProvider from "src/sections/develop-detail/DevelopDetailProvider";
import AddRowScenario from "./AddRowScenario";
import ScenarioDetailRightSection from "./ScenarioDetailRightSection";
import AddColumnScenario from "./AddColumnScenario";

const MetaChip = ({ label, value, icon }) => (
  <Box
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.75,
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 0.5,
      px: 1.25,
      py: 0.5,
      backgroundColor: "background.default",
    }}
  >
    {icon && (
      <SvgColor
        src={icon}
        sx={{ width: 14, height: 14, color: "text.secondary" }}
      />
    )}
    <Typography
      variant="caption"
      sx={{ color: "text.secondary", fontWeight: 500 }}
    >
      {label}
    </Typography>
    <Typography
      variant="caption"
      sx={{ color: "text.primary", fontWeight: 600 }}
    >
      {value}
    </Typography>
  </Box>
);

MetaChip.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  icon: PropTypes.string,
};

const formatCreatedAt = (value) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return formatDistanceToNow(date, { addSuffix: true });
};

const ScenarioDatasetView = () => {
  const { scenarioId } = useParams();
  const navigate = useNavigate();
  const [addRowScenarioOpen, setAddRowScenarioOpen] = useState(false);
  const [addColumnScenarioOpen, setColumnScenarioOpen] = useState(false);
  // Fetch scenario details
  const { data: scenario, isLoading, error } = useGetScenarioDetail(scenarioId);
  if (isLoading) {
    return <LoadingScreen />;
  }

  if (error || !scenario) {
    return (
      <Box
        sx={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Typography color="error">
          Error loading scenario: {error.message}
        </Typography>
        <Button
          variant="contained"
          onClick={() => navigate("/dashboard/simulate/scenarios")}
        >
          Back to Scenarios
        </Button>
      </Box>
    );
  }

  const scenarioType = scenario?.scenarioType || scenario?.scenario_type;
  const agentType = scenario?.agentType || scenario?.agent_type;
  const datasetId = scenario?.dataset || scenario?.dataset_id;
  const datasetRows = scenario?.datasetRows ?? scenario?.dataset_rows ?? 0;
  const createdAt = scenario?.createdAt || scenario?.created_at;
  const agentChipConfig = getChipConfig(agentType);
  const scenarioTypeChipConfig = getChipConfig(scenarioType);
  const hasGraph = Boolean(
    scenario?.graph && Object.keys(scenario.graph).length > 0,
  );

  return (
    <DevelopDetailProvider>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          backgroundColor: "background.paper",
          height: "100%",
        }}
      >
        <Box
          sx={{
            padding: 2,
            display: "flex",
            flexDirection: "column",
            gap: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.disabled"
              onClick={() => navigate("/dashboard/simulate/scenarios")}
              sx={{ cursor: "pointer" }}
            >
              All Scenarios
            </Typography>
            <SvgColor src="/assets/icons/custom/lucide--chevron-right.svg" />
            <Typography typography="m3" fontWeight="fontWeightMedium">
              {scenario?.name}
            </Typography>
          </Box>
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            <MetaChip
              label="Agent Type"
              value={agentChipConfig.label}
              icon={agentChipConfig.icon}
            />
            <MetaChip
              label="Scenario Type"
              value={scenarioTypeChipConfig.label}
              icon={scenarioTypeChipConfig.icon}
            />
            <MetaChip label="No of Datapoints" value={datasetRows} />
            <MetaChip label="Created" value={formatCreatedAt(createdAt)} />
          </Box>
        </Box>
        <Box
          sx={{
            display: "flex",
            paddingX: 2,
            paddingTop: 1,
            gap: 2,
            height: "50%",
          }}
        >
          <ShowComponent condition={scenarioType !== "dataset" || hasGraph}>
            <GraphPreview agentType={agentType} scenario={scenario} />
          </ShowComponent>
          <PromptPreview scenario={scenario} />
        </Box>
        <Box
          sx={{
            paddingX: 2,
            paddingTop: 2,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Typography typography="m3" fontWeight="fontWeightSemiBold">
            Generated scenarios
          </Typography>
          <ScenarioDetailRightSection
            scenario={scenario}
            setAddRowScenarioOpen={setAddRowScenarioOpen}
            setColumnScenarioOpen={setColumnScenarioOpen}
          />
        </Box>

        <ShowComponent condition={!datasetId}>
          <ShowComponent condition={scenario?.status === "Processing"}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 2,
                paddingX: 2,
                height: "100%",
                justifyContent: "center",
              }}
            >
              <CircularProgress size={20} />
              <Typography typography="s1">
                We are generating the scenario...
              </Typography>
            </Box>
          </ShowComponent>
          <ShowComponent condition={scenario?.status === "Failed"}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 2,
                paddingX: 2,
                height: "100%",
                justifyContent: "center",
              }}
            >
              <Typography typography="s1">
                There was an error generating the scenario.
              </Typography>
            </Box>
          </ShowComponent>
        </ShowComponent>
        <ShowComponent condition={datasetId}>
          <Box
            sx={{ height: "100vh", display: "flex", flexDirection: "column" }}
          >
            <DevelopDataV2
              datasetId={datasetId}
              viewOptions={{
                showDrawer: false,
                bottomRow: false,
                showEvals: false,
              }}
            />
            <AddRowScenario
              open={addRowScenarioOpen}
              onClose={() => setAddRowScenarioOpen(false)}
              datasetId={datasetId}
              scenarioType={scenarioType}
              scenarioId={scenarioId}
            />
            <AddColumnScenario
              open={addColumnScenarioOpen}
              onClose={() => setColumnScenarioOpen(false)}
              datasetId={datasetId}
              scenarioType={scenarioType}
              scenarioId={scenarioId}
            />
          </Box>
        </ShowComponent>
      </Box>
    </DevelopDetailProvider>
  );

  // // For dataset type scenarios, redirect to the develop detail view
  // // This approach reuses all existing functionality of DevelopDetailView
  // // Using URL params to ensure the state is preserved
  // const params = new URLSearchParams({
  //   fromScenario: "true",
  //   scenarioId: scenario.id,
  //   scenarioName: scenario.name,
  //   tab: "data",
  // });

  // return (
  //   <Navigate
  //     to={`/dashboard/develop/${datasetId}?${params.toString()}`}
  //     replace
  //   />
  // );
};

export default ScenarioDatasetView;
