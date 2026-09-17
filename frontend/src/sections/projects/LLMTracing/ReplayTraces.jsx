import React, { useCallback, useEffect } from "react";
import { useTraceGridStoreShallow } from "./states";
import CustomTooltip from "../../../components/tooltip";
import { Button } from "@mui/material";
import SvgColor from "../../../components/svg-color/svg-color";
import { useReplaySessionsStoreShallow } from "../SessionsView/ReplaySessions/store";
import { REPLAY_MODULES } from "../SessionsView/ReplaySessions/configurations";
import PropTypes from "prop-types";
import {
  formatSelectionCount,
  getSelectionCountState,
} from "./listTotalMetadata";

const ReplayTraces = ({ gridApi }) => {
  const {
    selectAll,
    toggledNodes,
    totalRowCount,
    totalRowCountLowerBound,
    totalRowCountIsLowerBound,
  } = useTraceGridStoreShallow((s) => ({
    selectAll: s.selectAll,
    toggledNodes: s.toggledNodes,
    totalRowCount: s.totalRowCount,
    totalRowCountLowerBound: s.totalRowCountLowerBound,
    totalRowCountIsLowerBound: s.totalRowCountIsLowerBound,
  }));

  const selectionCount = getSelectionCountState({
    selectAll,
    toggledNodes,
    totalRowCount,
    totalRowCountLowerBound,
    totalRowCountIsLowerBound,
  });

  const {
    openReplaySessionDrawer,
    setIsReplayDrawerCollapsed,
    setOpenCreateScenarios,
  } = useReplaySessionsStoreShallow((s) => ({
    openReplaySessionDrawer: s.openReplaySessionDrawer,
    setIsReplayDrawerCollapsed: s.setIsReplayDrawerCollapsed,
    setOpenCreateScenarios: s.setOpenCreateScenarios,
  }));
  const handleReplaySessions = () => {
    if (openReplaySessionDrawer[REPLAY_MODULES.TRACES]) {
      setIsReplayDrawerCollapsed(REPLAY_MODULES.TRACES, false);
    } else {
      setOpenCreateScenarios(true);
    }
  };

  // const shouldDisable = useMemo(() => {
  //   return (
  //     openReplaySessionDrawer[REPLAY_MODULES.TRACES] &&
  //     currentStep > 0 &&
  //     validatedSteps[currentStep - 1]
  //   );
  // }, [currentStep, openReplaySessionDrawer, validatedSteps]);

  // Restore selection state when grid data is first rendered
  const restoreSelectionState = useCallback(() => {
    if (!gridApi) return;
    gridApi?.setServerSideSelectionState({
      selectAll: selectAll,
      toggledNodes: toggledNodes,
    });
  }, [gridApi, selectAll, toggledNodes]);

  // Listen for firstDataRendered event
  useEffect(() => {
    if (!openReplaySessionDrawer?.[REPLAY_MODULES.TRACES] && !gridApi) return;
    const onFirstDataRendered = () => {
      restoreSelectionState();
    };

    // Add event listener
    gridApi.addEventListener("firstDataRendered", onFirstDataRendered);

    // Cleanup
    return () => {
      gridApi.removeEventListener("firstDataRendered", onFirstDataRendered);
    };
  }, [gridApi, openReplaySessionDrawer, restoreSelectionState]);
  return (
    <>
      <CustomTooltip
        show={!(selectAll || toggledNodes.length > 0)}
        size="small"
        arrow
        placement="top-start"
        type={"black"}
        title="Choose trace to replay."
      >
        <span>
          <Button
            sx={{
              p: "8px",
            }}
            color={selectAll || toggledNodes.length > 0 ? "primary" : "inherit"}
            startIcon={
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                  color:
                    selectAll || toggledNodes.length > 0
                      ? "primary"
                      : "text.primary",
                }}
                src={"/assets/icons/navbar/ic_get_started.svg"}
              />
            }
            onClick={handleReplaySessions}
            size="small"
            variant="outlined"
            disabled={
              !(selectAll || toggledNodes.length > 0) ||
              Object.values(openReplaySessionDrawer).some((open) => open)
            }
          >
            {`Replay Trace${selectAll || toggledNodes.length > 1 || toggledNodes.length === 0 ? "s" : ""} ${selectAll || toggledNodes.length > 0 ? `(${formatSelectionCount(selectionCount)})` : ""}`}
          </Button>
        </span>
      </CustomTooltip>
    </>
  );
};

export default ReplayTraces;

ReplayTraces.propTypes = {
  gridApi: PropTypes.object.isRequired,
};
