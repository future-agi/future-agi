import { Box, Button, Typography, useTheme } from "@mui/material";
import React, { useState } from "react";
import { useTestDetailStoreShallow, useTestExecutionStore } from "./states";
import { useMemo } from "react";
import { useTestDetail } from "./context/TestDetailContext";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import RerunModal from "./RerunModal";
import { useParams } from "react-router";
import PropTypes from "prop-types";
import { useTestEvaluationStoreShallow } from "../test/states";
import TestEvaluationDrawer from "../test/TestEvaluationDrawer";
import { TestRunLoadingStatus } from "./common";
import CustomTooltip from "src/components/tooltip";

const TestDetailSelection = ({ agentType }) => {
  const { toggledNodes, selectAll, setToggledNodes, setSelectAll } =
    useTestDetailStoreShallow((s) => ({
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
      setToggledNodes: s.setToggledNodes,
      setSelectAll: s.setSelectAll,
    }));

  const { executionId } = useParams();

  const [openRerunModal, setOpenRerunModal] = useState(false);
  const theme = useTheme();

  const { getGridApi, refreshGrid } = useTestDetail();

  const gridApi = getGridApi?.();

  const selectedCount = useMemo(() => {
    if (!gridApi) return 0;
    const context = gridApi.getGridOption("context");
    if (selectAll) {
      return context.totalRowCount - toggledNodes.length;
    } else {
      return toggledNodes.length;
    }
  }, [toggledNodes, selectAll, gridApi]);

  const clearSelection = () => {
    gridApi?.deselectAll();
    setToggledNodes([]);
    setSelectAll(false);
  };

  const status = useTestExecutionStore((s) => s.status);

  const { setOpenTestEvaluation } = useTestEvaluationStoreShallow((s) => ({
    setOpenTestEvaluation: s.setOpenTestEvaluation,
  }));

  return (
    <>
      <ShowComponent condition={selectedCount > 0}>
        <Box
          sx={{
            // border: "1px solid",
            // borderColor: "action.hover",
            borderRadius: "4px",
            paddingY: 0.5,
            paddingX: 2,
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          {/* <Typography variant="s1" color="primary.main">
          {selectedCount} selected
        </Typography>
        <Divider orientation="vertical" flexItem /> */}
          <Button
            size="small"
            variant="outlined"
            sx={{ ...theme.typography.s2_1 }}
            startIcon={
              <SvgColor src="/assets/icons/navbar/ic_get_started.svg" />
            }
            onClick={() => {
              setOpenRerunModal(true);
            }}
          >
            {`Rerun test (${selectedCount})`}
          </Button>
          {/* <Divider orientation="vertical" flexItem /> */}
          <Button
            size="small"
            variant="outlined"
            sx={{ ...theme.typography.s1 }}
            onClick={() => {
              clearSelection();
            }}
          >
            Cancel
          </Button>
        </Box>
        <RerunModal
          agentType={agentType}
          open={openRerunModal}
          onClose={() => {
            setOpenRerunModal(false);
            clearSelection();
            refreshGrid();
          }}
          selectedNodes={toggledNodes}
          selectAll={selectAll}
          selectedCount={selectedCount}
          executionId={executionId}
        />
      </ShowComponent>
      <ShowComponent condition={selectedCount === 0}>
        <CustomTooltip
          size="small"
          type="black"
          arrow={true}
          show={TestRunLoadingStatus.includes(status)}
          title={`Hang tight! You can add evals once the test finishes.`}
        >
          <span>
            <Button
              sx={{ borderColor: "text.disabled" }}
              onClick={() => setOpenTestEvaluation(true)}
              startIcon={<SvgColor width={20} src="/assets/icons/ic_add.svg" />}
              variant="outlined"
              size="small"
              disabled={TestRunLoadingStatus.includes(status)}
            >
              <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
                Add More Evals
              </Typography>
            </Button>
          </span>
        </CustomTooltip>

        <TestEvaluationDrawer
          onSuccessOfAdditionOfEvals={() => {
            refreshGrid?.();
          }}
          executionIds={[executionId]}
        />
      </ShowComponent>
    </>
  );
};
TestDetailSelection.propTypes = {
  agentType: PropTypes.string,
};
export default TestDetailSelection;
