import { Box } from "@mui/material";
import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import EvaluationProvider from "../common/EvaluationDrawer/context/EvaluationProvider";
import { EvaluationContext } from "../common/EvaluationDrawer/context/EvaluationContext";
import { resetEvalStore } from "./store/useEvalStore";
import EvalsPageHeader from "./EvalsPageHeader";

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
  const setVisibleSectionRef = useRef(null);

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
        <EvalsPageHeader currentTab={currentTab} />
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
