import {
  Box,
  Button,
  LinearProgress,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import SvgColor from "../../../components/svg-color/svg-color";
import { ShowComponent } from "src/components/show";

const textStatusMapper = {
  "default-synthetic": {
    title: "Generating synthetic data",
    description:
      "We are generating your synthetic dataset, this might take some time",
    buttonTitle: "Configure Synthetic Data ",
  },
  "failed-to-generate-synthetic": {
    title: "Failed to generate synthetic data",
    description:
      "Something went wrong while generating synthetic data. Please click on configure rto re-generate or edit configuration",
    buttonTitle: "Configure Synthetic Data ",
  },
  "regenerating-synthetic": {
    title: "Re-Generating synthetic data",
    description:
      "We are re-generating your synthetic dataset, this might take some time",
    buttonTitle: "Configure Synthetic Data ",
  },
};

export default function DatasetLoader({
  icon = "/assets/icons/action_buttons/ic_configure.svg",
  onAction = () => {},
  status = "default-synthetic",
  gridApiRef,
  isSyntheticDataset,
  updateProcessingSyntheticData,
  failureReason,
}) {
  const theme = useTheme();
  const [progress, setProgress] = useState(0);
  const {
    title,
    description: fallbackDescription,
    buttonTitle,
  } = textStatusMapper[status];
  const description = failureReason || fallbackDescription;
  const hideProgress = status === "failed-to-generate-synthetic";

  useEffect(() => {
    if (hideProgress || !isSyntheticDataset) return;
    const api = gridApiRef?.current?.api;
    if (!api) return;

    const interval = setInterval(() => {
      const value = Number(api?.syntheticDatasetPercentage);

      if (Number.isFinite(value) && value >= 0) {
        setProgress(value);
        if (value === 100) {
          api.refreshServerSide({ purge: true });
          api.hideOverlay();
          updateProcessingSyntheticData(false);
        }
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [
    gridApiRef,
    hideProgress,
    isSyntheticDataset,
    updateProcessingSyntheticData,
  ]);

  if (!isSyntheticDataset) return null;

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <Stack direction={"column"} alignItems={"center"} gap={2}>
        <ShowComponent
          condition={["failed-to-generate-synthetic"].includes(status)}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              borderRadius: "50%",
              bgcolor: "red.o5",
              height: "68px",
              width: "68px",
            }}
          >
            <SvgColor
              sx={{
                height: "29px",
                width: "29px",
                bgcolor: "red.500",
              }}
              src="/assets/icons/ic_critical.svg"
            />
          </Box>
        </ShowComponent>
        <Stack alignItems={"center"}>
          <Typography
            variant="m3"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            {title}
          </Typography>
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
            sx={{
              maxWidth: "432px",
            }}
          >
            {description}
          </Typography>
        </Stack>
        <ShowComponent condition={!hideProgress}>
          <LinearProgress
            variant="determinate"
            value={progress}
            sx={{
              height: theme.spacing(0.5),
              borderRadius: theme.spacing(2),
              width: "100%",
              backgroundColor: "action.hover", // track background
              "& .MuiLinearProgress-bar": {
                backgroundColor: "text.primary", // progress fill
              },
            }}
          />
          <Typography
            variant="s2"
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            {progress}% completed
          </Typography>
        </ShowComponent>

        <Button
          variant="outlined"
          sx={{
            width: "fit-content",
          }}
          onClick={onAction}
          startIcon={
            <SvgColor
              src={icon}
              sx={{
                height: 16,
                width: 16,
                color: "text.primary",
              }}
            />
          }
        >
          {buttonTitle}
        </Button>
      </Stack>
    </Box>
  );
}

DatasetLoader.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  buttonTitle: PropTypes.string,
  icon: PropTypes.string,
  onAction: PropTypes.func,
  status: PropTypes.string,
  gridApiRef: PropTypes.any,
  isSyntheticDataset: PropTypes.bool,
  updateProcessingSyntheticData: PropTypes.func,
  failureReason: PropTypes.string,
};
