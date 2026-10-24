import { Box, IconButton, styled } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { AgentPromptOptimizerStatus } from "../../FixMyAgentDrawer/common";
import SvgColor from "../../../../components/svg-color";
import { ShowComponent } from "../../../../components/show";
import StatusBadge from "src/components/status-badge/StatusBadge";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useOptimizationRunDetailStoreShallow } from "../states";
import { format } from "date-fns";

const statusMap = {
  [AgentPromptOptimizerStatus.PENDING]: { role: "neutral", title: "Queue" },
  [AgentPromptOptimizerStatus.RUNNING]: { role: "info", title: "Running" },
  [AgentPromptOptimizerStatus.COMPLETED]: {
    role: "success",
    title: "Completed",
  },
  [AgentPromptOptimizerStatus.FAILED]: { role: "danger", title: "Failed" },
};

const StyledIconButton = styled(IconButton)(({ theme }) => ({
  borderRadius: theme.spacing(1),
  border: `1px solid ${theme.palette.divider}`,
  padding: "6px",
  color: "text.primary",
}));

const StatusCellRenderer = ({ value, data }) => {
  const { setOpenOptimizationRerun } = useOptimizationRunDetailStoreShallow(
    (state) => ({
      setOpenOptimizationRerun: state.setOpenOptimizationRerun,
    }),
  );
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        lineHeight: 1.5,
        height: "100%",
      }}
    >
      <ShowComponent condition={value === AgentPromptOptimizerStatus.FAILED}>
        <CustomTooltip
          show={true}
          title="Re run optimization. This will create a new optimization run."
          arrow
          size="small"
          type="black"
        >
          <StyledIconButton
            className="optimization-run-detail-refresh-button"
            onClick={() =>
              setOpenOptimizationRerun({
                name: `${data?.optimisation_name} - Rerun - ${format(new Date(), "dd MMM yyyy")}`,
                model: data?.model,
                optimiserType: data?.optimiser_type,
                configuration: data?.configuration,
              })
            }
          >
            <SvgColor
              src="/assets/icons/ic_refresh.svg"
              sx={{ width: 16, height: 16, color: "text.primary" }}
            />
          </StyledIconButton>
        </CustomTooltip>
      </ShowComponent>
      <StatusBadge
        status={value}
        label={statusMap[value]?.title ?? "-"}
        role={statusMap[value]?.role ?? "neutral"}
      />
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

export default StatusCellRenderer;
