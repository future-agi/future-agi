import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useNavigate } from "react-router";
import { zodResolver } from "@hookform/resolvers/zod";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

import { projectSchema } from "./common";
import DeleteProject from "./DeleteProject";
import { RHFSlider } from "src/components/hook-form";
import { ShowComponent } from "src/components/show/ShowComponent";

const samplingRateMarks = [
  {
    value: 0,
    label: "",
  },
  {
    value: 100,
    label: "",
  },
];

const ConfigureProject = ({ open, onClose, id, refreshGrid, module }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: projectDetail } = useGetProjectDetails(id);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const {
    control,
    setValue,
    handleSubmit,
    formState: { errors, isValid },
    reset,
    watch,
  } = useForm({
    defaultValues: {
      projectName: projectDetail?.name || "",
      samplingRate: projectDetail?.samplingRate * 100,
    },
    resolver: zodResolver(projectSchema),
    mode: "onChange",
  });
  const watchSamplingRate = watch("samplingRate");

  const handleClose = () => {
    reset();
    onClose();
  };

  const { mutate: updateProject, isPending: isUpdating } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) => axios.post(endpoints.project.updateProject, data),
    onSuccess: () => {
      enqueueSnackbar(`${projectDetail?.name} has been updated`, {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: ["project-detail", id],
      });
      handleClose();
      refreshGrid();
    },
  });

  const onSubmit = (data) => {
    if (!data.projectName.trim()) {
      return;
    }
    const payload = {
      project_id: projectDetail.id,
      name: data.projectName,
    };
    if (module === "observe") {
      payload["sampling_rate"] = data.samplingRate / 100;
    }
    updateProject(payload);
  };

  useEffect(() => {
    if (projectDetail) {
      reset({
        projectName: projectDetail.name || "",
        samplingRate: projectDetail.samplingRate * 100,
      });
    }
  }, [projectDetail, reset]);

  const handleDeleteClick = () => {
    handleClose();
    setDeleteDialogOpen(true);
  };

  const handleDeleteSuccess = () => {
    navigate(
      module === "observe" ? "/dashboard/observe" : "/dashboard/prototype",
    );
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={handleClose}
        fullWidth
        aria-labelledby="configure-dialog-title"
        PaperProps={{
          sx: {
            width: 550,
            maxWidth: 590,
            maxHeight: 450,
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <DialogTitle
          id="configure-dialog-title"
          sx={{
            paddingTop: theme.spacing(1.5),
            paddingBottom: theme.spacing(0.5),
            paddingX: theme.spacing(1.5),
          }}
        >
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
          >
            <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
              Configure Project
            </Box>
            <IconButton
              aria-label="close-configure-project"
              onClick={handleClose}
              sx={{
                p: 0,
              }}
            >
              <Iconify icon="line-md:close" color="text.primary" />
            </IconButton>
          </Box>
        </DialogTitle>

        <form onSubmit={handleSubmit(onSubmit)}>
          <DialogContent
            sx={{
              overflow: "hidden",
              flexGrow: 1,
              paddingX: theme.spacing(1.5),
              mb: theme.spacing(1),
              alignItems: "center",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <FormTextFieldV2
              control={control}
              fieldName="projectName"
              label="Project Name"
              size="small"
              placeholder="Enter project name"
              margin="normal"
              fullWidth
              helperText={errors.projectName?.message}
              isSpinnerField={false}
              fieldType="text"
            />
            <ShowComponent condition={module === "observe"}>
              <Box
                sx={{
                  width: "100%",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                  padding: 1.5,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.5,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 1,
                  }}
                >
                  <Box>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightMedium"}
                    >
                      Sampling rate
                    </Typography>
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "row",
                        alignItems: "center",
                        gap: 0.5,
                      }}
                    >
                      <Typography
                        fontSize={"12px"}
                        typography={"s2_1"}
                        fontWeight={"fontWeightRegular"}
                        color={"text.secondary"}
                      >
                        Defines the percentage of data processed for agent
                        compass
                      </Typography>
                      <a
                        href="https://docs.futureagi.com/docs/error-feed/features/sampling"
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ textDecoration: "none" }}
                      >
                        <Typography
                          typography={"s3"}
                          fontWeight={"fontWeightMedium"}
                          sx={{
                            textDecoration: "underline",
                            textDecorationColor: "blue.500",
                          }}
                          color={"blue.500"}
                        >
                          Docs
                        </Typography>
                      </a>
                    </Box>
                  </Box>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 0.25,
                      display: "flex",
                      flexDirection: "row",
                      gap: 0.5,
                      padding: "2px 15px",
                      alignItems: "center",
                      height: "26px",
                    }}
                  >
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {`${watchSamplingRate}%`}
                    </Typography>
                  </Box>
                </Box>
                <RHFSlider
                  control={control}
                  name={"samplingRate"}
                  helperText={errors.samplingRate?.message}
                  defaultValue={projectDetail?.samplingRate * 100}
                  min={0}
                  valueLabelDisplay="auto"
                  max={100}
                  marks={samplingRateMarks}
                  step={1}
                  sx={(t) => ({
                    color: t.palette.text.primary,
                    height: 4,
                    mt: -1,
                    "& .MuiSlider-thumb": {
                      width: 8,
                      height: 8,
                      "&::before": {
                        boxShadow: "0 2px 12px 0 rgba(0,0,0,0.4)",
                      },
                      "&:hover, &.Mui-focusVisible": {
                        boxShadow: `0px 0px 0px 8px ${"rgb(0 0 0 / 16%)"}`,
                        ...t.applyStyles("dark", {
                          boxShadow: `0px 0px 0px 8px ${"rgb(255 255 255 / 16%)"}`,
                        }),
                      },
                      "&.Mui-active": {
                        width: 20,
                        height: 20,
                      },
                    },
                    "& .MuiSlider-rail": {
                      opacity: 0.28,
                    },
                    ...t.applyStyles("dark", {
                      color: t.palette.common.white,
                    }),
                  })}
                />
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    mt: -1.5,
                  }}
                >
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    onClick={() => setValue("samplingRate", 0)}
                    sx={{ cursor: "pointer" }}
                  >
                    0%
                  </Typography>
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    onClick={() => setValue("samplingRate", 100)}
                    sx={{ cursor: "pointer" }}
                  >
                    100%
                  </Typography>
                </Box>
              </Box>
            </ShowComponent>
          </DialogContent>

          <DialogActions
            sx={{
              padding: theme.spacing(1.5),
            }}
          >
            <Button
              aria-label="Cancel-configure-project"
              variant="outlined"
              color="inherit"
              onClick={handleClose}
              sx={{
                width: "90px",
                height: "30px",
                fontSize: "12px",
                color: "text.disabled",
              }}
            >
              Cancel
            </Button>
            <Button
              aria-label="delete-project"
              variant="outlined"
              color="inherit"
              onClick={handleDeleteClick}
              sx={{
                textTransform: "none",
                fontSize: "14px",
                color: "text.disabled",
              }}
            >
              Delete
            </Button>
            <LoadingButton
              aria-label="update-project"
              type="submit"
              loading={isUpdating}
              disabled={!isValid}
              sx={{
                textTransform: "none",
                fontSize: "14px",
                minWidth: "90px",
              }}
              variant="contained"
              color="primary"
            >
              Update
            </LoadingButton>
          </DialogActions>
        </form>
      </Dialog>
      <DeleteProject
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        projectId={projectDetail?.id}
        projectName={projectDetail?.name}
        projectType={module === "observe" ? "observe" : "experiment"}
        onSuccess={handleDeleteSuccess}
      />
    </>
  );
};

export default ConfigureProject;

ConfigureProject.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  id: PropTypes.string,
  refreshGrid: PropTypes.func,
  module: PropTypes.oneOf(["prototype", "observe"]),
};
