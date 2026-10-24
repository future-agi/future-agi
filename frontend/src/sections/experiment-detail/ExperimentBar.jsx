import { Box, Tab, Tabs } from "@mui/material";
import React from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import TabSvgIcon from "src/components/tabs/tab-svg-icon";

const TabOptions = [
  { label: "Data", value: "data", icon: "/assets/icons/navbar/ic_llm.svg" },
  {
    label: "Summary",
    value: "summary",
    icon: "/assets/icons/navbar/ic_chartsObserve.svg",
  },
];

const ExperimentBar = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { experimentId } = useParams();
  const currentTab = location.pathname.split("/").pop();

  return (
    <Box
      sx={{
        borderBottom: 1,
        borderColor: "divider",
        paddingX: 2.5,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box>
        <Tabs
          value={currentTab}
          onChange={(e, value) => {
            const currentParams = new URLSearchParams(location.search);
            // Keep existing params (datasetId, payload, etc.)
            const queryString = currentParams.toString();

            navigate(
              `/dashboard/develop/experiment/${experimentId}/${value}?${queryString}`,
              { replace: true },
            );
          }}
        >
          {TabOptions.map((tab) => (
            <Tab
              key={tab.value}
              icon={<TabSvgIcon src={tab.icon} size={20} />}
              iconPosition="start"
              label={tab.label}
              value={tab.value}
            />
          ))}
        </Tabs>
      </Box>
    </Box>
  );
};

export default ExperimentBar;
