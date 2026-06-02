import { Box, Chip, styled } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { OutlinedButton } from "src/sections/project-detail/ProjectDetailComponents";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { black, green, orange, red } from "src/theme/palette";
import { alpha } from "@mui/material";

const statusColorMap = {
  pending: {
    backgroundColor: orange[50],
    color: orange[400],
  },
  running: {
    backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.1),
    color: "primary.main",
  },
  completed: {
    backgroundColor: green.o10,
    color: green[500],
  },
  failed: {
    backgroundColor: red.o10,
    color: red[500],
  },
  paused: {
    backgroundColor: black.o5,
    color: black[400],
  },
};

const CustomIconButton = styled(OutlinedButton)(() => ({
  "& .MuiButton-startIcon": {
    margin: 0,
  },
  minWidth: 0,
  width: "30px",
  height: "32px",
}));

const RunningStatusRenderer = ({ value, data, api }) => {
  const { mutate: pauseEvalTask } = useMutation({
    mutationFn: () => axios.post(endpoints.project.pauseEvalTask(data.id)),
    onSuccess: (_) => {
      api.applyServerSideTransaction({
        update: [{ ...data, status: "paused" }],
      });
    },
  });

  const { mutate: resumeEvalTask } = useMutation({
    mutationFn: () => axios.post(endpoints.project.resumeEvalTask(data.id)),
    meta: { errorHandled: true },
    onSuccess: (_) => {
      api.applyServerSideTransaction({
        update: [{ ...data, status: "running" }],
      });
    },
    onError: () => {
      api.refreshServerSide?.();
      enqueueSnackbar("Failed to resume task. It may have already finished.", {
        variant: "error",
      });
    },
  });

  const onPause = () => {
    //@ts-ignore
    pauseEvalTask();
  };

  const onResume = () => {
    //@ts-ignore
    resumeEvalTask();
  };

  const mainValue = (value || "").trim().toLowerCase();

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        height: "100%",
        gap: 1,
      }}
    >
      <ShowComponent condition={value === "running"}>
        <CustomIconButton
          variant="outlined"
          onClick={onPause}
          startIcon={
            <Iconify
              icon="material-symbols-light:pause-outline-rounded"
              sx={{ color: "text.disabled" }}
              width={24}
            />
          }
        />
      </ShowComponent>
      <ShowComponent condition={value === "paused"}>
        <CustomIconButton
          variant="outlined"
          onClick={onResume}
          startIcon={
            <Iconify
              icon="material-symbols-light:resume-outline-rounded"
              sx={{ color: "text.disabled" }}
              width={30}
            />
          }
        />
      </ShowComponent>
      <Chip
        sx={{
          backgroundColor: statusColorMap[mainValue]?.backgroundColor,
          color: statusColorMap[mainValue]?.color,
          fontWeight: 400,
          fontSize: 12,
          height: 24,
          "&:hover": {
            backgroundColor: statusColorMap[mainValue]?.backgroundColor,
          },
        }}
        label={_.capitalize(mainValue)}
      />
    </Box>
  );
};

RunningStatusRenderer.propTypes = {
  value: PropTypes.string,
  node: PropTypes.object,
  data: PropTypes.object,
  api: PropTypes.object,
};

export default RunningStatusRenderer;
