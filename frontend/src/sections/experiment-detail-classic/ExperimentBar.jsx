import { Box, Tab, Tabs, useTheme } from "@mui/material";
import React from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import SvgColor from "src/components/svg-color";
import ExperimentRunControl from "src/sections/common/ExperimentRunControl/ExperimentRunControl";

// Census §A: both working legacy destinations remain visible and enabled.
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";

const TabOptions = [
  { label: "Data", value: "data", icon: "/assets/icons/navbar/ic_llm.svg" },
  {
    label: CLASSIC_EXPERIMENT_COPY.summaryLabel,
    value: "summary",
    icon: "/assets/icons/ic_summary.svg",
  },
];

const ExperimentBar = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { experimentId } = useParams();
  const currentTab = location.pathname.split("/").pop();

  const theme = useTheme();

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
          sx={{
            "& .MuiTab-root": {
              margin: "0 !important",
              fontWeight: "fontWeightSemiBold",
              typography: "s1",
              padding: 1.5,
              color: "primary.main",
              "&:not(.Mui-selected)": {
                color: "text.disabled", // Color for unselected tabs
                fontWeight: "fontWeightMedium",
              },
            },
          }}
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
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
              sx={{
                margin: 0,
                px: "24px",
              }}
              key={tab.value}
              icon={
                <SvgColor
                  src={tab.icon}
                  sx={{ height: "20px", width: "20px", mr: 0.5 }}
                />
              }
              iconPosition="start"
              label={tab.label}
              value={tab.value}
            />
          ))}
        </Tabs>
      </Box>
      <ExperimentRunControl experimentId={experimentId} />
    </Box>
  );
};

export default ExperimentBar;
