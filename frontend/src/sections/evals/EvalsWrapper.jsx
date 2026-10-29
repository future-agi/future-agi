import { Box, Button, Tab, Tabs, useTheme } from "@mui/material";
import React, { useEffect, useRef } from "react";
import PageHeadings from "../develop-detail/Common/PageHeadings";
import PropTypes from "prop-types";
import { useNavigate } from "react-router";
import EvaluationProvider from "../common/EvaluationDrawer/context/EvaluationProvider";
import { EvaluationContext } from "../common/EvaluationDrawer/context/EvaluationContext";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { resetEvalStore } from "./store/useEvalStore";
import SvgColor from "src/components/svg-color";

const ContextConsumer = ({
  context,
  setVisibleSectionRef,
  id,
  showTest,
  showAdd,
  runLabel,
  module,
}) => {
  useEffect(() => {
    if (context) {
      const { setModule, setActionButtonConfig, setVisibleSection } = context;
      if (setVisibleSection) {
        setVisibleSectionRef.current = setVisibleSection;
        setModule(module);
      }
      setActionButtonConfig((pre) => ({
        ...pre,
        id,
        showTest,
        showAdd,
        runLabel,
      }));
    }
  }, [module, id]);
  return null;
};

ContextConsumer.propTypes = {
  id: PropTypes.string,
  showTest: PropTypes.bool,
  showAdd: PropTypes.bool,
  runLabel: PropTypes.string,
  handleTest: PropTypes.func,
  handleRun: PropTypes.func,
  context: PropTypes.object,
  module: PropTypes.string,
  setVisibleSectionRef: PropTypes.any,
};

const EvalsWrapper = ({ currentTab, children, containerSx = {} }) => {
  const theme = useTheme();
  const setVisibleSectionRef = useRef(null);
  const navigate = useNavigate();
  const tabItems = [
    {
      label: "Evaluators",
      value: "evaluators",
      disabled: false,
      navigateRoute: "/dashboard/evaluations",
    },
    {
      label: "Usage",
      value: "usage",
      disabled: false,
      navigateRoute: "/dashboard/evaluations/usage",
    },
    {
      label: "Groups",
      value: "groups",
      disabled: false,
      navigateRoute: "/dashboard/evaluations/groups",
    },
  ];

  useEffect(() => {
    return () => {
      if (currentTab === "evaluators") {
        resetEvalStore();
      }
    };
  }, [currentTab]);

  return (
    <EvaluationProvider>
      <EvaluationContext.Consumer>
        {(context) => (
          <ContextConsumer
            context={context}
            setVisibleSectionRef={setVisibleSectionRef}
            id={"2063cf96-40fc-4840-b5cd-ce48f06c24ea"}
            showTest={true}
            showAdd={true}
            runLabel={"Run Label"}
            handleTest={() => {}}
            handleRun={() => {}}
          />
        )}
      </EvaluationContext.Consumer>
      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          padding: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          ...containerSx,
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <PageHeadings
            title="Evaluations"
            description="Create and manage your evaluations"
          />
          <Button
            variant="outlined"
            size="small"
            sx={{
              color: "text.primary",
              borderColor: "divider",
              padding: 1.5,
              fontSize: "14px",
              height: "38px",
            }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href="https://docs.futureagi.com/docs/cookbook/quickstart/first-eval"
            target="_blank"
          >
            View Docs
          </Button>
        </Box>
        <Box>
          <Tabs
            textColor="primary"
            value={currentTab}
            onChange={(e, value) => {
              navigate(
                tabItems.find((tab) => tab.value === value).navigateRoute,
              );
              if (value === "usage") {
                trackEvent(Events.evalsUsagetabClicked, {
                  [PropertyName.click]: true,
                });
              }
            }}
            TabIndicatorProps={{
              style: { backgroundColor: theme.palette.primary.main },
            }}
            sx={{ borderBottom: "1px solid", borderColor: "divider" }}
          >
            {tabItems.map((tab) => (
              <Tab
                disabled={tab.disabled}
                key={tab.value}
                label={tab.label}
                value={tab.value}
                sx={{
                  paddingLeft: "24px",
                  paddingRight: "24px",
                  ...theme.typography["s1"],
                  fontWeight: theme.typography["fontWeightSemiBold"],
                  "&:not(.Mui-selected)": {
                    color: "text.disabled", // Color for unselected tabs
                    fontWeight: theme.typography["fontWeightMedium"],
                  },
                  ["&:not(:last-of-type)"]: {
                    marginRight: "4px",
                  },
                }}
              />
            ))}
          </Tabs>
        </Box>
        <Box sx={{ height: "calc(100% - 120px)" }}>{children}</Box>
      </Box>
    </EvaluationProvider>
  );
};

export default EvalsWrapper;

EvalsWrapper.propTypes = {
  currentTab: PropTypes.string,
  children: PropTypes.node,
  containerSx: PropTypes.object,
};
