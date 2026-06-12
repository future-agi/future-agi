import {
  Badge,
  Box,
  Button,
  Divider,
  LinearProgress,
  Typography,
  useTheme,
} from "@mui/material";
import { styled } from "@mui/material/styles";
import LoadingButton from "@mui/lab/LoadingButton";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import React, { useRef, useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import { useLocation } from "react-router";
import { Helmet } from "react-helmet-async";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import axiosInstance from "src/utils/axios";
import { useSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";

import ExperimentListView from "./ExperimentListView";
import ObserveListView from "./ObserveListView";
import ProjectObserveContextProvider from "./context/ProjectObserveContextProvider";
import ProjectExperimentContextProvider from "./context/ProjectExperimentContextProvider";
import ProjectRightSection from "./RightSection/ProjectRightSection";
import ProjectFtux from "./ProjectFtux";
import ProjectFilterPanel from "./ProjectFilterPanel";

export const SearchFieldBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.spacing(0.5),
  height: "38px",
  width: "360px",
  // margin: '0 auto 17px'
}));

const ProjectWrapperView = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedAll, setSelectedAll] = useState(false);
  const [selectedRowsData, setSelectedRowsData] = useState([]);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [observeFilters, setObserveFilters] = useState(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const location = useLocation();
  const gridRef = useRef(null);
  const currentTab = location.pathname.split("/").pop();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: [`project-${currentTab}-list`],
    queryFn: () =>
      axios.get(
        currentTab === "observe"
          ? endpoints.project.projectObserveList
          : endpoints.project.projectExperimentList,
        {
          params: {
            project_type: currentTab === "observe" ? "observe" : "experiment",
          },
        },
      ),
    select: (data) => data.data,
  });

  const theme = useTheme();

  const isProjectCount =
    currentTab === "observe"
      ? data?.result?.metadata?.total_rows > 0
      : data?.result?.projects?.length > 0;

  const handleSearchChange = (e) => {
    setSearchQuery(e.target.value);
  };

  const handleSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelectedRowsData([]);
      }, 300);
      return;
    }
    if (event?.data?.id) {
      const rowId = event?.data?.id;
      setSelectedRowsData((prevSelectedItems) => {
        const updatedSelectedRowsData = [...prevSelectedItems];

        const rowIndex = updatedSelectedRowsData?.findIndex(
          (row) => row === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event?.data?.id);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }

        return updatedSelectedRowsData;
      });
    }
  };

  const clearSelection = () => {
    gridRef.current?.clearSelection?.();
    setSelectedAll(false);
    setSelectedRowsData([]);
  };

  const handleDelete = () => {
    setDeleteModalOpen(true);
  };

  const { mutate: confirmDelete, isPending: isDeleting } = useMutation({
    mutationFn: () =>
      axiosInstance.delete(endpoints.project.deleteObservePrototype, {
        data: {
          project_ids: selectedRowsData,
          project_type: currentTab === "observe" ? "observe" : "experiment",
        },
      }),
    onSuccess: () => {
      const filesLength = selectedRowsData.length;
      const message =
        filesLength === 1
          ? "Project has been deleted."
          : `${filesLength} Projects have been deleted.`;
      setDeleteModalOpen(false);
      enqueueSnackbar(message, {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: [`project-${currentTab}-list`],
      });
      queryClient.invalidateQueries({
        queryKey: [
          currentTab === "observe" ? "observe-projects" : "experiment-projects",
        ],
      });
      gridRef?.current?.clearSelection?.();
      setSelectedRowsData([]);
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.message ||
          "An unexpected error occurred while deleting monitors.",
        {
          variant: "error",
        },
      );
    },
  });

  if (isLoading) {
    return <LinearProgress />;
  }

  if (!isProjectCount) {
    return <ProjectFtux />;
  }

  return (
    <ProjectExperimentContextProvider>
      <ProjectObserveContextProvider>
        <Helmet>
          <title>{currentTab === "observe" ? "Tracing" : "Prototype"}</title>
        </Helmet>
        <Box
          sx={{
            backgroundColor: "background.paper",
            height: "100%",
            padding: (theme) => theme.spacing(2),
            display: "flex",
            flexDirection: "column",
            gap: (theme) => theme.spacing(2),
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: (theme) => theme.spacing(0.25),
            }}
          >
            <Typography
              color="text.primary"
              variant="m2"
              fontWeight={"fontWeightSemiBold"}
            >
              {currentTab === "observe" ? "Tracing" : "Prototype"}
            </Typography>
            <Box
              sx={{
                display: "flex",
                gap: (theme) => theme.spacing(0.5),
                alignItems: "center",
              }}
            >
              <Typography
                variant="s1"
                color="text.primary"
                fontWeight={"fontWeightRegular"}
              >
                Create a project to experiment on your model
              </Typography>
            </Box>
          </Box>

          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <FormSearchField
                size="small"
                placeholder="Search"
                searchQuery={searchQuery}
                onChange={handleSearchChange}
                sx={{
                  minWidth: "250px",
                  "& .MuiOutlinedInput-root": { height: "30px" },
                }}
              />
              {currentTab === "observe" && (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={
                    observeFilters?.length > 0 ? (
                      <Badge variant="dot" color="error" overlap="circular">
                        <Iconify icon="mage:filter" width={14} />
                      </Badge>
                    ) : (
                      <Iconify icon="mage:filter" width={14} />
                    )
                  }
                  onClick={(e) => setFilterAnchorEl(e.currentTarget)}
                  sx={{
                    textTransform: "none",
                    fontSize: 12,
                    height: 36,
                    borderColor: "divider",
                    color: "text.secondary",
                  }}
                >
                  Filter
                </Button>
              )}
            </Box>
            {selectedRowsData.length > 0 ? (
              <Box
                sx={{
                  display: "flex",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  paddingX: theme.spacing(2),
                  paddingY: theme.spacing(0.5),
                  alignItems: "center",
                  gap: theme.spacing(2),
                }}
              >
                <Typography
                  fontWeight="fontWeightMedium"
                  variant="s1"
                  color="primary.main"
                >
                  {selectedRowsData.length} Selected
                </Typography>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{ borderRightWidth: theme.spacing(0.25) }}
                />
                <Button
                  startIcon={
                    <SvgColor
                      src="/assets/icons/ic_delete.svg"
                      sx={{ width: 20, height: 20, color: "text.disabled" }}
                    />
                  }
                  size="small"
                  sx={{ color: "text.secondary" }}
                  onClick={handleDelete}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    color="text.primary"
                  >
                    Delete
                  </Typography>
                </Button>
                <Button
                  size="small"
                  sx={{ color: "text.secondary" }}
                  onClick={clearSelection}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    color="text.primary"
                  >
                    Cancel
                  </Typography>
                </Button>
              </Box>
            ) : (
              <ProjectRightSection isObserve={currentTab === "observe"} />
            )}
          </Box>

          {currentTab === "observe" ? (
            <>
              <ObserveListView
                ref={gridRef}
                searchQuery={searchQuery}
                onSelectionChanged={handleSelectionChanged}
                selectedAll={selectedAll}
                setSelectedAll={setSelectedAll}
                setSelectedRowsData={setSelectedRowsData}
                filters={observeFilters}
              />
              <ProjectFilterPanel
                anchorEl={filterAnchorEl}
                open={Boolean(filterAnchorEl)}
                onClose={() => setFilterAnchorEl(null)}
                currentFilters={observeFilters}
                onApply={setObserveFilters}
              />
            </>
          ) : (
            <ExperimentListView
              ref={gridRef}
              searchQuery={searchQuery}
              setSelectedRowsData={setSelectedRowsData}
            />
          )}
          <ConfirmDialog
            open={deleteModalOpen}
            onClose={() => setDeleteModalOpen(false)}
            title="Delete Project"
            content={
              <Typography color="text.secondary">
                Are you sure you want to delete{" "}
                {selectedRowsData.length === 1
                  ? "this project?"
                  : `these ${selectedRowsData.length} projects?`}
              </Typography>
            }
            action={
              <LoadingButton
                variant="contained"
                color="error"
                size="small"
                onClick={confirmDelete}
                loading={isDeleting}
              >
                <Typography variant="s2" fontWeight="fontWeightSemiBold">
                  Delete
                </Typography>
              </LoadingButton>
            }
          />
        </Box>
      </ProjectObserveContextProvider>
    </ProjectExperimentContextProvider>
  );
};

export default ProjectWrapperView;
