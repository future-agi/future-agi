import { Box, IconButton } from "@mui/material";
import React, { useCallback } from "react";
import PropTypes from "prop-types";
import SvgColor from "../svg-color";
import { ShowComponent } from "../show/ShowComponent";
import CustomTooltip from "../tooltip";
import {
  useReRunExperiment,
  useStopExperiment,
} from "src/sections/develop-detail/Experiment/common";
const allowedStatus = {
  COMPLETED: "completed",
  FAILED: "failed",
  RUNNING: "running",
  QUEUED: "queued",
  NOTSTARTED: "notstarted",
  CANCELLED: "cancelled",
};
const TableAction = (params) => {
  const experimentStatus = params?.data?.status?.toLowerCase();
  const experimentId = params?.data?.id;
  const refreshGrid = useCallback(() => {
    params?.api?.refreshServerSide({ purge: true });
  }, [params?.api]);
  const { stopExperiment, isStoppingExperiment } = useStopExperiment(
    experimentId,
    refreshGrid,
  );
  const { reRunExperiment, isReRunningExperiment } = useReRunExperiment(
    [experimentId],
    false,
    refreshGrid,
  );
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        width: "100%",
        gap: 1,
      }}
    >
      <ShowComponent
        condition={
          !isReRunningExperiment &&
          [
            allowedStatus.COMPLETED,
            allowedStatus.FAILED,
            allowedStatus.CANCELLED,
          ].includes(experimentStatus)
        }
      >
        <CustomTooltip
          size="small"
          type="black"
          arrow={true}
          show={true}
          title={"Rerun Experiment"}
        >
          <IconButton
            sx={{
              width: 32,
              height: 32,
              border: "1px solid",
              borderColor: isReRunningExperiment ? "text.disabled" : "divider",
              borderRadius: "4px",
              backgroundColor: "transparent",
              transition: "border-color 0.2s",
              p: 0,
            }}
            onClick={() => reRunExperiment()}
            disabled={isReRunningExperiment}
          >
            <SvgColor
              sx={{ width: 20, height: 20 }}
              src="/assets/icons/navbar/ic_evaluate.svg"
            />
          </IconButton>
        </CustomTooltip>
      </ShowComponent>
      <ShowComponent
        condition={
          experimentStatus === allowedStatus.RUNNING ||
          experimentStatus === allowedStatus.QUEUED ||
          experimentStatus === allowedStatus.NOTSTARTED
        }
      >
        <CustomTooltip
          size="small"
          type="black"
          arrow={true}
          show={true}
          title={
            experimentStatus === allowedStatus.NOTSTARTED
              ? "Experiment has not started yet"
              : "Stop Experiment"
          }
        >
          <span>
            <IconButton
              sx={{
                width: 32,
                height: 32,
                border: "1px solid",
                borderColor: isStoppingExperiment
                  ? "border.hover"
                  : "border.default",
                borderRadius: "4px",
                backgroundColor: "transparent",
                transition: "border-color 0.2s",
                p: 0,
              }}
              onClick={() => stopExperiment()}
              disabled={
                isStoppingExperiment ||
                experimentStatus === allowedStatus.NOTSTARTED
              }
            >
              <SvgColor
                sx={{ width: 20, height: 20, color: "error.main" }}
                src="/assets/icons/ic_stop.svg"
              />
            </IconButton>
          </span>
        </CustomTooltip>
      </ShowComponent>
    </Box>
  );
};

TableAction.propTypes = {
  scenario: PropTypes.object,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func,
};

export default TableAction;
