import React from "react";
import { Box, Tab, Tabs } from "@mui/material";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import { evalDrawerTabs } from "./common";
import { ShowComponent } from "src/components/show";
import Evaluations from "./sections/Evaluations";
import { useEvaluationContext } from "./context/EvaluationContext";

const UseCasesAndFilters = ({
  control,
  setValue,
  isEvalsView,
  currentTab,
  setCurrentTab,
}) => {
  const theme = useTheme();
  const { setSelectedGroup, module } = useEvaluationContext();

  return (
    <Box
      sx={{ position: "relative" }}
      display="flex"
      flexDirection="column"
      gap={theme.spacing(1.5)}
    >
      <ShowComponent condition={!isEvalsView}>
        <Tabs
          textColor="primary"
          value={currentTab}
          onChange={(e, value) => {
            setCurrentTab(value);
            setSelectedGroup(null);
          }}
          sx={{ borderBottom: "1px solid", borderColor: "divider" }}
        >
          {evalDrawerTabs
            .filter((t) => {
              if (
                isEvalsView ||
                module === "task" ||
                module === "workbench" ||
                module === "dataset" ||
                module === "run-experiment" ||
                module === "run-optimization" ||
                module === "experiment" ||
                module === "create-simulate" ||
                module === "simulate-eval-update" ||
                module === "create-experiment1"
              ) {
                return true;
              }
              return t.value !== "groups"; // hide "groups"
            })
            .map((tab) => (
              <Tab
                disabled={tab.disabled}
                key={tab.value}
                label={tab.label}
                value={tab.value}
              />
            ))}
        </Tabs>
      </ShowComponent>

      <ShowComponent condition={currentTab === "evals"}>
        <Evaluations
          control={control}
          setValue={setValue}
          isEvalsView={isEvalsView}
        />
      </ShowComponent>
    </Box>
  );
};

UseCasesAndFilters.propTypes = {
  useCases: PropTypes.object,
  EvalTypes: PropTypes.object,
  control: PropTypes.object,
  setValue: PropTypes.func,
  isEvalsView: PropTypes.bool,
  currentTab: PropTypes.oneOf(["evals", "groups"]),
  setCurrentTab: PropTypes.func,
};

export default UseCasesAndFilters;
