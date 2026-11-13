import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";

import OptimizeTabPanel from "./OptimizeTabPanel";
import PromptTemplateExplore from "./PromptTemplateExplore";
import RightAnswerExplore from "./RightAnswerExplore";
import PromptTemplateResults from "./PromptTemplateResults";
import { LoadingScreen } from "src/components/loading-screen";

function CustomTabPanel(props) {
  const { children, value, panelValue, loading, ...other } = props;

  return (
    <div hidden={panelValue !== value} role="tabpanel" {...other}>
      {loading ? <LoadingScreen /> : null}
      {value === panelValue && !loading && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  panelValue: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

const OptimizeDetailView = ({ selectedOptimization }) => {
  const [openTab, setOpenTab] = useState("explore");
  const optimizationType = selectedOptimization?.optimizeType;

  const onOpenTabChange = (newTab) => {
    setOpenTab(newTab);
    // let eventName;
    // if (newTab === "explore") {
    //   eventName = Events.optimizeDetailPageExplore;
    // } else {
    //   eventName = Events.optimizeDetailPageResult;
    // }
    // trackEvent(eventName, {
    //   "Optimization Name": selectedOptimization?.name,
    //   "Optimization Id": selectedOptimization?.id,
    //   "Optimization Type": selectedOptimization?.optimizeType,
    // });
  };

  return (
    <>
      <Box>
        {optimizationType === "PromptTemplate" && (
          <OptimizeTabPanel openTab={openTab} setOpenTab={onOpenTabChange} />
        )}
        <CustomTabPanel panelValue="explore" value={openTab}>
          {optimizationType === "PromptTemplate" ? (
            <PromptTemplateExplore
              selectedOptimization={selectedOptimization}
            />
          ) : (
            <RightAnswerExplore selectedOptimization={selectedOptimization} />
          )}
        </CustomTabPanel>
        <CustomTabPanel panelValue="results" value={openTab}>
          <PromptTemplateResults selectedOptimization={selectedOptimization} />
        </CustomTabPanel>
      </Box>
    </>
  );
};

OptimizeDetailView.propTypes = {
  selectedOptimization: PropTypes.object,
};

export default OptimizeDetailView;
