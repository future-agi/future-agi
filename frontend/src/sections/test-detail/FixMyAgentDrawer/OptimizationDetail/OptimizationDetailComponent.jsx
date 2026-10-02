import { Box, Switch, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useState } from "react";
import FixMyAgentHeader from "../FixMyAgentHeader";
import SvgColor from "../../../../components/svg-color";
import Growth from "../OptimizationResults/Growth";
import { useFixMyAgentDrawerStoreShallow } from "../state";
import { FixMyAgentDrawerSections } from "../common";
import { CustomTab, CustomTabs, TabWrapper } from "./SharedComponents";
import CustomTooltip from "../../../../components/tooltip";
import { ShowComponent } from "../../../../components/show";
import PromptDetails from "./PrompDetails/PromptDetails";
import TrialItems from "./TrialItems/TrialItems";
import { useOptimizeTrialPrompts } from "src/api/tests/testDetails";

const OptimizationDetailComponent = ({
  optimizationId,
  trialId,
  onClose,
  isDrawer = true,
}) => {
  const [showDiff, setShowDiff] = useState(false);
  const [activeTab, setActiveTab] = useState("prompt");
  const { setOpenSection } = useFixMyAgentDrawerStoreShallow((state) => ({
    setOpenSection: state.setOpenSection,
  }));
  const { data: trailPromptData } = useOptimizeTrialPrompts({
    optimizationId,
    trialId,
  });

  const tabs = [
    { value: "prompt", label: "Prompt" },
    { value: "trial-items", label: "Trial Items" },
  ];

  const handleTabChange = (_event, newValue) => {
    setActiveTab(newValue);
  };
  return (
    <Box
      sx={{
        height: "100%",
        padding: isDrawer ? 2 : 0,
        gap: 2,
        width: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <ShowComponent condition={isDrawer}>
        <FixMyAgentHeader onClose={onClose} />

        <Box sx={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.disabled"
            sx={{ cursor: "pointer" }}
            onClick={() =>
              setOpenSection({
                section: FixMyAgentDrawerSections.OPTIMIZE,
                id: optimizationId,
              })
            }
          >
            Optimization Results
          </Typography>
          <SvgColor
            src="/assets/icons/custom/lucide--chevron-right.svg"
            sx={{ width: "20px", height: "20px" }}
            color="text.primary"
          />
          <Typography typography="s1" fontWeight="fontWeightMedium">
            {trailPromptData?.trial_name}
          </Typography>
        </Box>
      </ShowComponent>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {trailPromptData?.trial_name}
        </Typography>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Typography typography="s1">
            Optimization Name: {trailPromptData?.optimisation_name}
          </Typography>
          <Growth
            value={trailPromptData?.score_percentage_change}
            getText={(value) => {
              if (value > 0) {
                return `+${value}% improved from baseline prompt`;
              } else if (value < 0) {
                return `${value}% degraded from baseline prompt`;
              } else {
                return `No change from baseline prompt`;
              }
            }}
          />
        </Box>
      </Box>
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
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
        }}
      >
        <ShowComponent condition={activeTab === "prompt"}>
          <PromptDetails
            optimizationId={optimizationId}
            trialId={trialId}
            showDiff={showDiff}
          />
        </ShowComponent>
        <ShowComponent condition={activeTab === "trial-items"}>
          <TrialItems optimizationId={optimizationId} trialId={trialId} />
        </ShowComponent>
      </Box>
    </Box>
  );
};

OptimizationDetailComponent.propTypes = {
  optimizationId: PropTypes.string,
  trialId: PropTypes.string,
  onClose: PropTypes.func,
  isDrawer: PropTypes.bool,
};

export default OptimizationDetailComponent;
