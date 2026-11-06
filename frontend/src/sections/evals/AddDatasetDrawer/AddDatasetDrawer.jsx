import { Box, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import CustomEvalDrawer from "../CustomEvalDrawer/CustomEvalDrawer";
import EvaluationConfigureForm from "../EvalDetails/EvaluationConfigureForm";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const AddDatasetDrawer = ({
  open,
  onClose,
  refreshGrid,
  allColumns,
  refresh,
  setRefresh,
  onBack,
}) => {
  const [selectedEval, setSelectedEval] = useState(null);

  const onOptionClick = (data) => {
    onClose();
    setSelectedEval(data);
  };

  const onClickClose = () => {
    onClose();
    setSelectedEval(null);
  };

  const onBackClick = () => {
    onBack();
    setSelectedEval(null);
  };

  const covered = useDrawerCovered(open);
  const configCovered = useDrawerCovered(Boolean(selectedEval));

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        sx={coveredDrawerSx(covered)}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "640px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "8px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <Box
          sx={{
            //  padding: "20px",
            gap: "34px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <CustomEvalDrawer
            onOptionClick={onOptionClick}
            onClose={onClose}
            refreshGrid={refreshGrid}
          />
        </Box>
      </Drawer>
      <Drawer
        anchor="right"
        open={Boolean(selectedEval)}
        onClose={onClickClose}
        sx={coveredDrawerSx(configCovered)}
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "8px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <Box
          sx={{
            //  padding: "20px",
            gap: "34px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <EvaluationConfigureForm
            onClose={onClickClose}
            onBackClick={onBackClick}
            selectedEval={selectedEval}
            allColumns={allColumns}
            refreshGrid={refreshGrid}
            setRefresh={setRefresh}
            refresh={refresh}
            datasetId={"2063cf96-40fc-4840-b5cd-ce48f06c24ea"}
          />
        </Box>
      </Drawer>
    </>
  );
};

AddDatasetDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onBack: PropTypes.func,
  refreshGrid: PropTypes.func,
  allColumns: PropTypes.array,
  setDrawer: PropTypes.func,
  refresh: PropTypes.any,
  setRefresh: PropTypes.func,
};

export default AddDatasetDrawer;
