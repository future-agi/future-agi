import { Box } from "@mui/material";
import React from "react";
import { useNavigate, useParams } from "react-router";
import DevelopExperimentSelect from "./DevelopExperimentSelect";
import BackButton from "../develop-detail/Common/BackButton";
import SvgColor from "src/components/svg-color";
import { useReRunExperiment } from "../develop-detail/Experiment/common";
import { useExperimentDetailContext } from "./experiment-context";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import ClassicRerunAction from "./ClassicRerunAction";
import ClassicEditExperiment from "./ClassicEditExperiment";

const ExperimentRow = () => {
  const navigate = useNavigate();
  const { experimentId } = useParams();
  const { experimentGridRef } = useExperimentDetailContext();
  const queryClient = useQueryClient();
  const refreshGrid = () => {
    experimentGridRef?.current?.api?.refreshServerSide({ purge: true });
  };
  const handleSuccess = () => {
    refreshGrid();
    queryClient.invalidateQueries(["experiment"]);
    enqueueSnackbar("Experiment re-run successfully initiated", {
      variant: "success",
    });
  };
  const { reRunExperiment, isReRunningExperiment } = useReRunExperiment(
    [experimentId],
    false,
    handleSuccess,
  );
  return (
    <Box
      sx={{
        paddingTop: 2,
        paddingX: 1,
        display: "flex",
        justifyContent: "space-between",
        gap: 2,
        alignItems: "center",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 2,
        }}
      >
        <BackButton onBack={() => navigate(-1)} />
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <DevelopExperimentSelect />
        </Box>
      </Box>
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <ClassicEditExperiment
          experimentId={experimentId}
          onSaved={refreshGrid}
        />
        <ClassicRerunAction experimentId={experimentId} onSaved={refreshGrid} />
        <LoadingButton
          loading={isReRunningExperiment}
          startIcon={
            <SvgColor src={"/assets/icons/navbar/ic_get_started.svg"} />
          }
          variant="outlined"
          color="primary"
          size="medium"
          onClick={reRunExperiment}
        >
          Rerun Experiment
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default ExperimentRow;
